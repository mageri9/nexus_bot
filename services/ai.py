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
        Собирает слепок состояния из Redis, логи падений [1]
        и передает их на анализ модели Gemma 4 через AITUNNEL.
        """
        # 1. Забираем моментальный слепок системы из Redis [1]
        system_status = await self.query_service.get_system_status()

        # 2. Ищем упавшие ресурсы и собираем логи
        problem_logs = {}
        for agent_name, resources in system_status.items():
            for res_name, res_status in resources.items():
                if res_status in ("exited", "stopped", "unhealthy", "error", "unknown"):
                    try:
                        # Считываем последние 25 строк логов упавшего ресурса
                        logs = await self.query_service.get_resource_logs(
                            agent_name, res_name, limit=25
                        )
                        problem_logs[f"{agent_name}:{res_name}"] = logs
                    except Exception as e:
                        problem_logs[f"{agent_name}:{res_name}"] = (
                            f"Failed to retrieve logs: {e}"
                        )

        # 3. Формируем подробный контекст для ИИ
        system_prompt = (
            "Вы — Nexus AI, опытный DevOps-ассистент и системный администратор.\n"
            "Вам предоставлено текущее состояние инфраструктуры проектов и сырые логи упавших/проблемных сервисов.\n\n"
            f"Текущий статус экосистемы (из кэша Redis):\n{json.dumps(system_status, indent=2, ensure_ascii=False)}\n\n"
            f"Логи проблемных ресурсов:\n{json.dumps(problem_logs, indent=2, ensure_ascii=False)}\n\n"
            "Задача: профессионально, емко и по делу ответить на вопрос администратора на русском языке. "
            "Если видны ошибки в логах, проанализируйте их техническую причину и предложите четкий план устранения."
        )

        logger.info(
            f"AI: Routing query to AITUNNEL (Model: {settings.AITUNNEL_MODEL})..."
        )

        # 4. Делаем запрос в AITUNNEL
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