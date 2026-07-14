import json
from typing import Dict, Any, List
from redis.asyncio import Redis
from core import AgentRegistry
from services.health_engine import HealthEngine


class QueryService:
    def __init__(
        self, registry: AgentRegistry, redis_client: Redis, health_engine: HealthEngine
    ):
        self.registry = registry
        self.redis = redis_client
        self.health_engine = health_engine

    async def get_system_status(self) -> Dict[str, Any]:
        """Мгновенно читает последнее кэшированное состояние ВСЕХ агентов из Redis."""
        agent_names = self.registry.list_agents()
        status_report = {}

        for name in agent_names:
            data = await self.redis.get(f"nexus:state:{name}")
            if data:
                state_details = json.loads(data)

                # Поддержка структуры State V2
                if state_details.get("version") == 2:
                    status_report[name] = {}
                    for res_name, res_data in state_details.get(
                        "containers", {}
                    ).items():
                        status_report[name][res_name] = res_data["status"]
                    for res_name, res_data in state_details.get("storage", {}).items():
                        status_report[name][res_name] = res_data["status"]
                else:
                    # Обратная совместимость с V1 структурой
                    status_report[name] = {
                        res_name: res_data["status"]
                        for res_name, res_data in state_details.items()
                    }
            else:
                status_report[name] = {
                    "error": "No cached data. Please wait for collector."
                }

        return status_report

    async def get_agent_details(self, agent_name: str) -> Dict[str, Any]:
        """Мгновенно читает подробное состояние ОДНОГО агента из Redis."""
        data = await self.redis.get(f"nexus:state:{agent_name}")
        if not data:
            return {"error": f"No cached details for agent '{agent_name}'"}
        return json.loads(data)

    async def get_resource_logs(
        self, agent_name: str, resource_name: str, limit: int = 50
    ) -> str:
        agent = self.registry.get(agent_name)

        # Разрешаем алиасы 'app' <-> 'bot' для совместимости старых манифестов и SDK
        resolved_name = resource_name
        if resource_name not in agent.resources:
            if resource_name == "app" and "bot" in agent.resources:
                resolved_name = "bot"
            elif resource_name == "bot" and "app" in agent.resources:
                resolved_name = "app"

        # 1. Попытка прочесть данные из кольцевого буфера в Redis (проверяем оба варианта)
        for r_name in (resource_name, resolved_name):
            key = f"nexus:logs:{agent_name}:{r_name}"
            try:
                buffered_lines = await self.redis.lrange(key, -limit, -1)
                if buffered_lines:
                    return "\n".join(buffered_lines)
            except Exception as e:
                from loguru import logger

                logger.warning(
                    f"QueryService: Failed to fetch logs from Redis buffer for {agent_name}:{r_name}: {e}"
                )

        # 2. Фолбек на прямое чтение через Docker API транспорт
        resource = agent.resources.get(resolved_name)
        if not resource:
            raise KeyError(
                f"Resource '{resource_name}' (resolved as '{resolved_name}') not found in agent '{agent_name}'."
            )
        if not hasattr(resource, "get_logs"):
            raise TypeError(
                f"Resource '{resolved_name}' in agent '{agent_name}' does not support log collection."
            )
        return await resource.get_logs(limit=limit)

    def calculate_health_score(self, agent_name: str, state_details: dict) -> int:
        """
        Вычисляет динамический показатель здоровья (Health Score) проекта.
        Делегирует исполнение модульному HealthEngine.
        """
        return self.health_engine.calculate_score(state_details)

    async def get_agent_health_history(
        self, agent_name: str, limit: int = 24
    ) -> List[Dict[str, Any]]:
        """Возвращает историю показателей здоровья агента из временного ряда в Redis"""
        key = f"nexus:health:history:{agent_name}"
        # Извлекаем элементы по возрастанию метки времени (от старых к новым)
        raw_elements = await self.redis.zrange(key, -limit, -1)
        return [json.loads(el) for el in raw_elements]

    async def get_health_trend(self, agent_name: str) -> str:
        """Определяет тренд здоровья на основе сопоставления двух последних замеров"""
        try:
            history = await self.get_agent_health_history(agent_name, limit=2)
            if len(history) < 2:
                return ""

            old_score = history[0]["score"]
            new_score = history[1]["score"]

            if new_score > old_score:
                return " 📈"
            elif new_score < old_score:
                return " 📉"
            return " ➡️"
        except Exception:
            return ""

    def calculate_health_score(self, agent_name: str, state_details: dict) -> int:
        return self.health_engine.calculate_score(state_details)