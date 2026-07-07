from loguru import logger  # [1]
from core import AgentRegistry


class CommandService:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    async def restart_resource(self, agent_name: str, resource_name: str) -> str:
        """
        Перезапускает конкретный ресурс конкретного агента,
        если данный ресурс поддерживает перезапуск.
        """
        agent = self.registry.get(agent_name)
        resource = agent.resources.get(resource_name)

        if not resource:
            raise KeyError(
                f"Resource '{resource_name}' not found in agent '{agent_name}'."
            )

        # Проверяем контракт ресурса на поддержку метода restart
        if not hasattr(resource, "restart"):
            raise TypeError(
                f"Resource '{resource_name}' in agent '{agent_name}' does not support restart."
            )

        logger.warning(f"Executing RESTART for {agent_name}:{resource_name}...")
        return await resource.restart()