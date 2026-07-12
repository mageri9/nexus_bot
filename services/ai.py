import json
from openai import AsyncOpenAI
from loguru import logger
from config import settings
from services.query import QueryService
from services.event_bus import EventBus
from redis.asyncio import Redis


class AIService:
    def __init__(
        self, query_service: QueryService, event_bus: EventBus, redis_client: Redis
    ):
        self.query_service = query_service
        self.event_bus = event_bus
        self.redis = redis_client
        self._client = None

    @property
    def client(self) -> AsyncOpenAI:
        """Ленивая инициализация асинхронного клиента AITUNNEL"""
        if not self._client:
            key = settings.aitunnel_api_key_str
            if not key:
                raise ValueError("AITUNNEL_API_KEY is not configured in settings.")

            # Подключаемся к AITUNNEL через OpenAI-совместимый SDK
            self._client = AsyncOpenAI(api_key=key, base_url=settings.AITUNNEL_BASE_URL)
        return self._client

    async def record_usage(
        self,
        project: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        modality: str = "text",
    ) -> None:
        try:
            key = f"nexus:telemetry:ai:{project}:{provider}:{model}:{modality}"

            async with self.redis.pipeline() as pipe:
                pipe.hincrby(key, "prompt_tokens", prompt_tokens)
                pipe.hincrby(key, "completion_tokens", completion_tokens)
                pipe.hincrby(key, "requests", 1)
                await pipe.execute()

            logger.debug(
                f"AI Telemetry: Recorded {prompt_tokens}p/{completion_tokens}c tokens "
                f"for {project} ({provider}:{model}:{modality})"
            )
        except Exception as e:
            logger.error(f"Failed to record AI telemetry: {e}")

    async def on_ai_request(self, event_type: str, data: dict) -> None:
        project = data.get("project", "unknown")
        provider = data.get("provider", "unknown")
        model = data.get("model", "unknown")
        prompt_tokens = data.get("prompt_tokens", 0)
        completion_tokens = data.get("completion_tokens", 0)
        modality = data.get("modality", "text")

        await self.record_usage(
            project, provider, model, prompt_tokens, completion_tokens, modality
        )

    async def diagnose_incident(self, incident_id: str) -> str:
        """
        Собирает полный контекст аварии (логи инцидента, кольцевой буфер логов и историю фингерпринта)
        и формирует консолидированный авто-диагноз с пошаговыми рекомендациями SRE.
        """
        # Ленивый импорт во избежание круговых зависимостей при сборке ядра
        from services import incident_service

        incident = await incident_service.get_incident(incident_id)
        if not incident:
            raise ValueError(f"Incident #{incident_id} not found.")

        # 1. Извлекаем последние 30 строк кольцевого буфера логов из Redis
        ring_key = f"nexus:logs:{incident.project}:{incident.resource}"
        ring_lines = await self.redis.lrange(ring_key, -30, -1)
        ring_logs = "\n".join(ring_lines) if ring_lines else "Логи в кольцевом буфере Redis отсутствуют."

        # 2. Получаем историю фингерпринта из Error Registry, если она присутствует
        error_history = {}
        if incident.fingerprint:
            err_key = f"nexus:errors:{incident.fingerprint}"
            error_history = await self.redis.hgetall(err_key)

        # 3. Собираем структурированный промпт для Gemma 4
        system_prompt = (
            "Вы — Nexus AI, опытный системный администратор и эксперт по надежности инфраструктуры (SRE).\n"
            "Проанализируйте аварию на основе структурированного пакета данных (Incident Bundle):\n\n"
            f"ID инцидента: #{incident.id}\n"
            f"Проект: {incident.project.upper()}\n"
            f"Компонент: {incident.resource}\n"
            f"Критичность: {incident.severity}\n"
            f"Количество автоматических перезапусков: {incident.restart_count}\n"
            f"Причина перехода: {incident.reason}\n"
        )

        if error_history:
            system_prompt += (
                f"\nСтатистика дедупликации ошибок (Error Registry):\n"
                f"- Фингерпринт: {incident.fingerprint}\n"
                f"- Повторений всего: {error_history.get('count', '1')}\n"
                f"- Впервые зарегистрирована: {error_history.get('first_seen', 'N/A')}\n"
                f"- Последний замер активности: {error_history.get('last_seen', 'N/A')}\n"
                f"- Предыдущее сообщение: {error_history.get('last_message', 'N/A')}\n"
            )

        system_prompt += (
            f"\nЛоги, зафиксированные в момент инцидента (Snapshot):\n"
            f"```\n{incident.logs}\n```\n"
            f"\nСвежие логи из кольцевого буфера за последние циклы (Ring Buffer):\n"
            f"```\n{ring_logs}\n```\n"
            "\nСформулируйте профессиональный, предельно емкий отчет на русском языке:\n"
            "1. Суть ошибки (1-2 емкие фразы, почему произошел сбой).\n"
            "2. Анализ частоты повторений и влияния на экосистему на основе статистики фингерпринтов.\n"
            "3. Четкий пошаговый план устранения проблемы (2-3 конкретных действия)."
        )

        logger.info(f"AI: Diagnosing incident #{incident_id} via {settings.AITUNNEL_MODEL}...")

        response = await self.client.chat.completions.create(
            model=settings.AITUNNEL_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Проанализируй аварию и сформируй пошаговые рекомендации."},
            ],
            temperature=0.3,
            max_tokens=600,
        )

        report = response.choices[0].message.content

        # Публикуем событие расхода токенов
        usage = response.usage
        if usage:
            await self.event_bus.publish(
                "ai.request",
                {
                    "project": "nexus_incident",
                    "provider": "aitunnel",
                    "model": settings.AITUNNEL_MODEL,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "modality": "text",
                },
            )

        return report

    async def analyze_system(self, user_query: str) -> str:
        """Сохраняем старый метод общего системного анализа для обратной совместимости"""
        MAX_TOTAL_LOG_CHARS = 6000
        MAX_RESOURCES_IN_PROMPT = 8

        system_status = await self.query_service.get_system_status()

        problem_resources = [
            (agent_name, res_name)
            for agent_name, resources in system_status.items()
            for res_name, res_status in resources.items()
            if res_status in ("exited", "stopped", "unhealthy", "error", "unknown")
        ]

        problem_logs = {}
        truncated_notice = None

        if len(problem_resources) > MAX_RESOURCES_IN_PROMPT:
            truncated_notice = (
                f"Показаны логи первых {MAX_RESOURCES_IN_PROMPT} из "
                f"{len(problem_resources)} проблемных ресурсов (лимит контекста)."
            )
            problem_resources = problem_resources[:MAX_RESOURCES_IN_PROMPT]

        remaining_budget = MAX_TOTAL_LOG_CHARS
        for agent_name, res_name in problem_resources:
            if remaining_budget <= 0:
                problem_logs[f"{agent_name}:{res_name}"] = (
                    "[Пропущено: лимит контекста исчерпан]"
                )
                continue
            try:
                logs = await self.query_service.get_resource_logs(
                    agent_name, res_name, limit=25
                )
            except Exception as e:
                logs = f"Failed to retrieve logs: {e}"

            if len(logs) > remaining_budget:
                logs = logs[-remaining_budget:]
                logs = f"[...обрезано]\n{logs}"

            problem_logs[f"{agent_name}:{res_name}"] = logs
            remaining_budget -= len(logs)

        system_prompt = (
            "Вы — Nexus AI, опытный DevOps-ассистент и системный администратор.\n"
            "Вам предоставлено текущее состояние инфраструктуры проектов и сырые логи упавших/проблемных сервисов.\n\n"
            f"Текущий статус экосистемы (из кэша Redis):\n{json.dumps(system_status, indent=2, ensure_ascii=False)}\n\n"
            f"Логи проблемных ресурсов:\n{json.dumps(problem_logs, indent=2, ensure_ascii=False)}\n\n"
            + (f"\n[Примечание: {truncated_notice}]\n\n" if truncated_notice else "")
            + "Задача: профессионально, емко и по делу ответить на вопрос администратора на русском языке. "
            "Если видны ошибки в логах, проанализируйте их техническую причину и предложите четкий план устранения."
        )

        logger.info(
            f"AI: Routing query to AITUNNEL (Model: {settings.AITUNNEL_MODEL})..."
        )

        response = await self.client.chat.completions.create(
            model=settings.AITUNNEL_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ],
            temperature=0.3,
            max_tokens=800,
        )

        usage = response.usage
        if usage:
            await self.event_bus.publish(
                "ai.request",
                {
                    "project": "nexus",
                    "provider": "aitunnel",
                    "model": settings.AITUNNEL_MODEL,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "modality": "text",
                },
            )

        return response.choices[0].message.content