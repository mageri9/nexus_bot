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
        # 1. Попытка прочесть данные из кольцевого буфера в Redis
        key = f"nexus:logs:{agent_name}:{resource_name}"
        try:
            buffered_lines = await self.redis.lrange(key, -limit, -1)
            if buffered_lines:
                return "\n".join(buffered_lines)
        except Exception as e:
            # Логируем ошибку, но не падаем — пробуем фолбек
            from loguru import logger
            logger.warning(f"QueryService: Failed to fetch logs from Redis buffer for {agent_name}:{resource_name}: {e}")

        # 2. Фолбек на прямое чтение через Docker API транспорт, если буфер пуст
        agent = self.registry.get(agent_name)
        resource = agent.resources.get(resource_name)
        if not resource:
            raise KeyError(
                f"Resource '{resource_name}' not found in agent '{agent_name}'."
            )
        if not hasattr(resource, "get_logs"):
            raise TypeError(
                f"Resource '{resource_name}' in agent '{agent_name}' does not support log collection."
            )
        return await resource.get_logs(limit=limit)

    def calculate_health_score(self, agent_name: str, state_details: dict) -> int:
        """
        Вычисляет динамический показатель здоровья (Health Score) проекта.
        Делегирует исполнение модульному HealthEngine.
        """
        return self.health_engine.calculate_score(state_details)