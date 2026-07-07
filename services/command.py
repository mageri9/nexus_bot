from loguru import logger  # [1]
from core import AgentRegistry
from services.event_bus import EventBus


class CommandService:
    def __init__(self, registry: AgentRegistry, event_bus: EventBus):
        self.registry = registry
        self.event_bus = event_bus

    async def restart_resource(self, agent_name: str, resource_name: str) -> str:
        agent = self.registry.get(agent_name)
        resource = agent.resources.get(resource_name)

        if not resource:
            raise KeyError(
                f"Resource '{resource_name}' not found in agent '{agent_name}'."
            )
        if not hasattr(resource, "restart"):
            raise TypeError(
                f"Resource '{resource_name}' in agent '{agent_name}' does not support restart."
            )

        payload = {"agent": agent_name, "resource": resource_name, "action": "restart"}

        # 1. Публикуем событие о начале перезапуска
        await self.event_bus.publish("action:started", payload)

        try:
            result = await resource.restart()
            # 2. Публикуем событие об успешном завершении
            await self.event_bus.publish(
                "action:success", {**payload, "result": result}
            )
            return result
        except Exception as e:
            # 3. Публикуем событие об ошибке
            await self.event_bus.publish("action:failed", {**payload, "error": str(e)})
            raise e