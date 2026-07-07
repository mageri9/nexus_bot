import asyncio
from typing import Dict, Any, List
from core import AgentRegistry


class QueryService:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    async def get_system_status(self) -> Dict[str, Any]:
        """
        Собирает высокоуровневый статус (здоровье) всех проектов в системе.
        Вызовы выполняются параллельно для всех агентов.
        """
        agent_names = self.registry.list_agents()

        # Создаем список асинхронных задач для параллельного опроса [1]
        tasks = [self.registry.get(name).get_health() for name in agent_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        status_report = {}
        for name, result in zip(agent_names, results):
            if isinstance(result, Exception):
                status_report[name] = {"error": str(result)}
            else:
                status_report[name] = result
        return status_report

    async def get_agent_details(self, agent_name: str) -> Dict[str, Any]:
        """
        Собирает подробные метрики и статусы для конкретного агента.
        Запросы к ресурсам также идут параллельно.
        """
        agent = self.registry.get(agent_name)
        resource_names = list(agent.resources.keys())

        # Параллельно собираем статусы и метрики всех ресурсов агента [1]
        status_tasks = [res.get_status() for res in agent.resources.values()]
        metric_tasks = [res.get_metrics() for res in agent.resources.values()]

        all_tasks = status_tasks + metric_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Разделяем результаты выполнения
        num_resources = len(resource_names)
        statuses = results[:num_resources]
        metrics = results[num_resources:]

        details = {}
        for i, name in enumerate(resource_names):
            res_status = statuses[i]
            res_metrics = metrics[i]

            details[name] = {
                "status": str(res_status)
                if not isinstance(res_status, Exception)
                else f"error: {res_status}",
                "metrics": res_metrics
                if not isinstance(res_metrics, Exception)
                else {"error": str(res_metrics)},
            }
        return details

    async def get_resource_logs(
        self, agent_name: str, resource_name: str, limit: int = 50
    ) -> str:
        """
        Безопасно запрашивает логи у конкретного ресурса конкретного агента.
        Вызывает исключение, если ресурс не поддерживает логирование.
        """
        agent = self.registry.get(agent_name)
        resource = agent.resources.get(resource_name)
        if not resource:
            raise KeyError(
                f"Resource '{resource_name}' not found in agent '{agent_name}'."
            )

        # Проверяем контракт ресурса на поддержку метода получения логов
        if not hasattr(resource, "get_logs"):
            raise TypeError(
                f"Resource '{resource_name}' in agent '{agent_name}' does not support log collection."
            )

        return await resource.get_logs(limit=limit)