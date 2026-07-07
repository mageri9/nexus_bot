# core/agent.py
import asyncio
from typing import Dict, Any
from core.resource import Resource


class ProjectAgent:
    def __init__(self, name: str, resources: Dict[str, Resource]):
        self.name = name
        self.resources = resources

    async def get_health(self) -> Dict[str, Any]:
        """
        Параллельно запрашивает статус всех ресурсов агента.
        Возвращает словарь вида: {resource_name: status}
        """
        resource_names = list(self.resources.keys())
        # Создаем список асинхронных задач для параллельного выполнения [1]
        tasks = [res.get_status() for res in self.resources.values()]

        # Выполняем параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)

        health_report = {}
        for name, result in zip(resource_names, results):
            if isinstance(result, Exception):
                health_report[name] = f"error: {str(result)}"
            else:
                health_report[name] = result

        return health_report