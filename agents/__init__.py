from core import AgentRegistry
from .manifest import imagebot_agent, chronicle_agent

# Создаем центральный реестр Nexus
registry = AgentRegistry()

# Регистрируем наши проекты
registry.register(imagebot_agent)
registry.register(chronicle_agent)