import pytest
import hmac
import hashlib
import json
import asyncio
from unittest.mock import AsyncMock, patch
from nexus_sdk import NexusSDK


@pytest.mark.asyncio
async def test_sdk_correctly_formats_and_signs_payload():
    sdk = NexusSDK(
        endpoint_url="http://nexus/events/app",
        app_secret="super-secret-key",
        project_name="imagebot",
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 200

    with patch.object(sdk._client, "post", return_value=mock_resp) as mock_post:
        try:
            raise ValueError("Test dynamic error")
        except Exception as e:
            await sdk.report_error(e, context="chat_id=98765")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args

        assert args[0] == "http://nexus/events/app"
        body = kwargs["content"]
        headers = kwargs["headers"]

        # Валидируем JSON структуру внутри отправленного тела
        payload = json.loads(body.decode())
        assert payload["project"] == "imagebot"
        assert payload["exception_type"] == "ValueError"
        assert payload["message"] == "Test dynamic error"
        assert "Test dynamic error" in payload["traceback"]
        assert payload["context"] == "chat_id=98765"

        # Проверяем корректность HMAC-SHA256 подписи
        expected_sig = hmac.new(
            b"super-secret-key", body, hashlib.sha256
        ).hexdigest()
        assert headers["X-Nexus-Signature-256"] == f"sha256={expected_sig}"

    await sdk.close()