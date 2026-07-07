import json
from typing import Dict, Any, List
from redis.asyncio import Redis  # [1]
from core import AgentRegistry


class QueryService:
    def __init__(self, registry: AgentRegistry, redis_client: Redis):
        self.registry = registry
        self.redis = redis_client

    async def get_system_status(self) -> Dict[str, Any]:
        """
        Мгновенно читает последнее кэшированное состояние ВСЕХ агентов из Redis.
        """
        agent_names = self.registry.list_agents()
        status_report = {}

        for name in agent_names:
            # Читаем кэш из Redis [1]
            data = await self.redis.get(f"nexus:state:{name}")
            if data:
                state_details = json.loads(data)
                # Приводим к плоскому виду {res_name: status} для совместимости с ботом
                status_report[name] = {
                    res_name: res_data["status"]
                    for res_name, res_data in state_details.items()
                }
            else:
                # Если кэш пуст (например, при первом холодном запуске)
                status_report[name] = {
                    "error": "No cached data. Please wait for collector."
                }

        return status_report

    async def get_agent_details(self, agent_name: str) -> Dict[str, Any]:
        """
        Мгновенно читает подробное состояние ОДНОГО агента из Redis.
        """
        data = await self.redis.get(f"nexus:state:{agent_name}")
        if not data:
            return {"error": f"No cached details for agent '{agent_name}'"}
        return json.loads(data)

    async def get_resource_logs(
        self, agent_name: str, resource_name: str, limit: int = 50
    ) -> str:
        """
        Логи не кэшируются, опрашиваем ресурс напрямую по требованию.
        """
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