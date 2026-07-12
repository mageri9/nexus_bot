import os
import json
import hmac
import hashlib
from fastapi import FastAPI, Request, Header, HTTPException
from redis.asyncio import Redis

app = FastAPI(title="Nexus DevOps Webhook Receiver")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
# Секретный ключ для приложений, подключенных по SDK
NEXUS_APP_SECRET = os.getenv("NEXUS_APP_SECRET", "")


def verify_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Проверяет HMAC-SHA256 подпись тела запроса от GitHub."""
    if not WEBHOOK_SECRET:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")

    return hmac.compare_digest(expected, received)


def verify_app_signature(payload_body: bytes, signature_header: str | None) -> bool:
    """Проверяет HMAC-SHA256 подпись тела запроса от приложений."""
    if not NEXUS_APP_SECRET:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        NEXUS_APP_SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")

    return hmac.compare_digest(expected, received)


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(None),
    x_hub_signature_256: str = Header(None),
):
    """Принимает вебхуки от GitHub Webhooks API"""
    raw_body = await request.body()

    if not verify_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if x_github_event == "workflow_run":
        workflow_run = payload.get("workflow_run", {})
        status = workflow_run.get("status")
        conclusion = workflow_run.get("conclusion")

        if status == "completed":
            event_type = f"devops:workflow_{conclusion}"
            event_data = {
                "event_type": event_type,
                "payload": {
                    "repository": payload.get("repository", {}).get("full_name", "unknown"),
                    "workflow_name": workflow_run.get("name", "Unnamed Pipeline"),
                    "branch": workflow_run.get("head_branch", "unknown"),
                    "commit_sha": workflow_run.get("head_sha", "unknown")[:7],
                    "commit_message": workflow_run.get("head_commit", {}).get("message", "No message").strip(),
                    "author": workflow_run.get("triggering_actor", {}).get("login", "unknown"),
                    "url": workflow_run.get("html_url", "#")
                }
            }

            await redis_client.publish("nexus:pubsub:devops", json.dumps(event_data))
            return {"status": "dispatched", "event": event_type}

    return {"status": "ignored"}


@app.post("/events/app")
async def app_event(
    request: Request,
    x_nexus_signature_256: str = Header(None),
):
    """Принимает телеметрию, исключения и пульс от приложений через SDK."""
    raw_body = await request.body()

    if not verify_app_signature(raw_body, x_nexus_signature_256):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if "project" not in payload:
        raise HTTPException(status_code=400, detail="Missing required 'project' field")

    event_type = payload.get("event_type", "app:error")

    # Валидация структуры под каждый тип событий
    if event_type == "app:error":
        required_fields = {"exception_type", "message", "traceback"}
        if not required_fields.issubset(payload.keys()):
            raise HTTPException(status_code=400, detail="Missing required error fields")
    elif event_type == "app:heartbeat":
        # Для пульса достаточно валидного поля project
        pass
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported event_type: {event_type}")

    event_data = {
        "event_type": event_type,
        "payload": payload
    }

    # Публикация в Redis Pub/Sub канал телеметрии
    await redis_client.publish("nexus:pubsub:telemetry", json.dumps(event_data))
    return {"status": "dispatched", "event": event_type}