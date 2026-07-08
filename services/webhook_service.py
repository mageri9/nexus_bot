import os
import json
from fastapi import FastAPI, Request, Header, HTTPException
from redis.asyncio import Redis

app = FastAPI(title="Nexus DevOps Webhook Receiver")

# Подключаемся к Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)


@app.post("/webhooks/github")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    """Принимает вебхуки от GitHub Webhooks API"""
    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    payload = await request.json()

    # Обрабатываем событие завершения выполнения воркфлоу
    if x_github_event == "workflow_run":
        workflow_run = payload.get("workflow_run", {})
        status = workflow_run.get("status")  # например, completed, requested
        conclusion = workflow_run.get("conclusion")  # например, success, failure

        if status == "completed":
            # Формируем унифицированное событие для шины Nexus
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

            # Публикуем событие во внешний транспорт Redis Pub/Sub
            await redis_client.publish("nexus:pubsub:devops", json.dumps(event_data))
            return {"status": "dispatched", "event": event_type}

    return {"status": "ignored"}