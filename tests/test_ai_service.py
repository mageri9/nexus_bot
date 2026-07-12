import pytest
import json
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from services.ai import AIService
from services.incident import Incident
from services import incident_service


@pytest.mark.asyncio
async def test_diagnose_incident_builds_contextual_bundle_and_calls_api(
    fake_redis, event_bus
):
    from services.query import QueryService
    from services.health_engine import HealthEngine
    from core import AgentRegistry

    qs = QueryService(AgentRegistry(), fake_redis, HealthEngine())
    ai_service = AIService(qs, event_bus, fake_redis)

    # Имитируем инцидент со связанным фингерпринтом
    mock_incident = Incident(
        id="99",
        project="tarot_bot",
        resource="app",
        severity="HIGH",
        status="open",
        opened_at=datetime.now(timezone.utc),
        reason="Database connection pool timeout",
        logs="ValueError: Pool limit reached",
        fingerprint="fp_db_err",
    )

    # Заполняем кольцевой буфер логов контейнера в Redis
    await fake_redis.rpush("nexus:logs:tarot_bot:app", "ring line A", "ring line B")

    # Заполняем историю фингерпринта в реестре ошибок Redis
    await fake_redis.hset(
        "nexus:errors:fp_db_err",
        mapping={
            "count": "15",
            "first_seen": "2026-07-12T00:00:00Z",
            "last_seen": "2026-07-12T05:00:00Z",
            "last_message": "Pool limit reached",
        },
    )

    # Создаем моки для AsyncOpenAI completions API
    mock_completion = AsyncMock()
    mock_completion.choices = [
        AsyncMock(message=AsyncMock(content="AI Diagnostics Analysis Result"))
    ]
    mock_completion.usage = AsyncMock(prompt_tokens=150, completion_tokens=80)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    ai_service._client = mock_client

    # Заменяем get_incident в incident_service на заглушку
    with patch.object(incident_service, "get_incident", return_value=mock_incident):
        result = await ai_service.diagnose_incident("99")

        assert result == "AI Diagnostics Analysis Result"

        # Проверяем, что API вызвалось с правильной агрегированной информацией
        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args[1]

        system_prompt = kwargs["messages"][0]["content"]

        # Убеждаемся, что все три слоя данных (Bundle) попали в единый ИИ-запрос
        assert "ValueError: Pool limit reached" in system_prompt  # Из логов инцидента
        assert "ring line A" in system_prompt  # Из кольцевого буфера логов Redis
        assert (
            "Повторений всего: 15" in system_prompt
        )  # Из реестра фингерпринтов ошибок Redis