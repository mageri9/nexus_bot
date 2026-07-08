import json
from openai import AsyncOpenAI
from loguru import logger  # [1]
from config import settings
from services.query import QueryService


class AIService:
    def __init__(self, query_service: QueryService):
        self.query_service = query_service
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

    async def analyze_system(self, user_query: str) -> str:
        """
        Собирает слепок состояния из Redis, логи падений
        и передает их на анализ модели Gemma 4 через AITUNNEL.
        """
        MAX_TOTAL_LOG_CHARS = 6000  # общий бюджет на все логи проблемных ресурсов
        MAX_RESOURCES_IN_PROMPT = 8  # не тащим логи больше чем с N ресурсов разом

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

            # Урезаем каждый лог под оставшийся бюджет
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

        return response.choices[0].message.content