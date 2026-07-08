import json
from typing import Dict, Any, List
from redis.asyncio import Redis
from core import AgentRegistry


class QueryService:
    def __init__(self, registry: AgentRegistry, redis_client: Redis):
        self.registry = registry
        self.redis = redis_client

    async def get_system_status(self) -> Dict[str, Any]:
        """Мгновенно читает последнее кэшированное состояние ВСЕХ агентов из Redis."""
        agent_names = self.registry.list_agents()
        status_report = {}

        for name in agent_names:
            data = await self.redis.get(f"nexus:state:{name}")
            if data:
                state_details = json.loads(data)
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
        Ограничен в диапазоне от 0 до 100.
        """
        score = 100

        if not state_details or "error" in state_details:
            return 0

        for res_name, res_data in state_details.items():
            status = res_data.get("status", "unknown")
            metrics = res_data.get("metrics", {})

            # 1. Штраф за нерабочий сервис (-50)
            if status not in ("running", "healthy"):
                score -= 50

            # 2. Штраф за пиковую утилизацию CPU > 95% (-15)
            cpu_str = metrics.get("cpu", "0.00%")
            try:
                cpu_val = float(cpu_str.replace("%", "").strip())
                if cpu_val > 95.0:
                    score -= 15
            except ValueError:
                pass

            # 3. Штраф за критическую утилизацию RAM > 90% (-15)
            mem_perc_str = metrics.get("mem_perc", "0.00%")
            try:
                mem_val = float(mem_perc_str.replace("%", "").strip())
                if mem_val > 90.0:
                    score -= 15
            except ValueError:
                pass

            # 4. Штраф за перезапуски контейнеров (-5 за каждый рестарт)
            restarts = metrics.get("restarts", 0)
            score -= (restarts * 5)

        # 5. Дополнительный штраф за падение инфраструктурных баз (Redis/Postgres) (-20)
        for res_name, res_data in state_details.items():
            status = res_data.get("status", "unknown")
            if res_name.lower() in ("redis", "postgres", "postgresql") and status not in ("running", "healthy"):
                score -= 20

        return max(0, score)