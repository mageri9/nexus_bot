import hashlib
import hmac
import importlib
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-value"


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def webhook_module(monkeypatch):
    """
    webhook_service.py читает GITHUB_WEBHOOK_SECRET на уровне модуля, поэтому
    для контролируемого тестового окружения задаём переменную и
    перезагружаем модуль. Redis-клиент подменяем на fakeredis, чтобы не
    требовать реального Redis в CI.
    """
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    import webhook_service as ws
    importlib.reload(ws)

    ws.redis_client = fakeredis.FakeAsyncRedis(decode_responses=True)
    return ws


@pytest.fixture
def client(webhook_module):
    return TestClient(webhook_module.app)


def workflow_run_payload(status="completed", conclusion="success"):
    return {
        "workflow_run": {
            "status": status,
            "conclusion": conclusion,
            "name": "Build and Deploy Nexus to VPS",
            "head_branch": "main",
            "head_sha": "abcdef1234567890",
            "head_commit": {"message": "fix: something"},
            "triggering_actor": {"login": "mageri9"},
            "html_url": "https://github.com/mageri9/nexus/actions/runs/1",
        },
        "repository": {"full_name": "mageri9/nexus"},
    }


# ---- verify_signature() unit tests ----

def test_verify_signature_accepts_valid_signature(webhook_module):
    body = b'{"a": 1}'
    header = sign(SECRET, body)
    assert webhook_module.verify_signature(body, header) is True


def test_verify_signature_rejects_wrong_secret(webhook_module):
    body = b'{"a": 1}'
    header = sign("wrong-secret", body)
    assert webhook_module.verify_signature(body, header) is False


def test_verify_signature_rejects_tampered_body(webhook_module):
    header = sign(SECRET, b'{"a": 1}')
    assert webhook_module.verify_signature(b'{"a": 2}', header) is False


def test_verify_signature_rejects_missing_header(webhook_module):
    assert webhook_module.verify_signature(b"{}", None) is False


def test_verify_signature_rejects_header_without_sha256_prefix(webhook_module):
    body = b"{}"
    bad_header = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()  # без "sha256=" префикса
    assert webhook_module.verify_signature(body, bad_header) is False


def test_verify_signature_fails_closed_when_secret_not_configured(monkeypatch):
    """Критично для безопасности: если секрет не сконфигурирован, ЛЮБОЙ запрос
    должен отклоняться, а не приниматься по умолчанию."""
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    import webhook_service as ws
    importlib.reload(ws)

    body = b'{"a": 1}'
    # Даже если бы у нас была валидная на вид подпись, без секрета это не имеет значения
    header = f"sha256={hashlib.sha256(body).hexdigest()}"
    assert ws.verify_signature(body, header) is False


# ---- HTTP endpoint tests ----

def test_endpoint_rejects_missing_signature(client):
    body = json.dumps(workflow_run_payload()).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "workflow_run"},
    )
    assert resp.status_code == 401


def test_endpoint_rejects_invalid_signature(client):
    body = json.dumps(workflow_run_payload()).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401


def test_endpoint_rejects_missing_event_header(client):
    body = json.dumps(workflow_run_payload()).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(SECRET, body)},
    )
    assert resp.status_code == 400


def test_endpoint_rejects_invalid_json_body(client):
    body = b"not-json{{{"
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    assert resp.status_code == 400


def test_endpoint_dispatches_successful_workflow_run(client, webhook_module):
    body = json.dumps(workflow_run_payload(conclusion="success")).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "dispatched", "event": "devops:workflow_success"}


def test_endpoint_dispatches_failed_workflow_run(client):
    body = json.dumps(workflow_run_payload(conclusion="failure")).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["event"] == "devops:workflow_failure"


def test_endpoint_ignores_incomplete_workflow_run(client):
    body = json.dumps(workflow_run_payload(status="in_progress", conclusion=None)).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}


def test_endpoint_ignores_unrelated_event_types(client):
    body = json.dumps({"zen": "keep it logically awesome"}).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
