from core import AgentRegistry
from .manifest import imagebot_agent, chronicle_agent, skillbook_agent, nexus_agent

# Создаем центральный реестр Nexus
registry = AgentRegistry()

# Регистрируем наши проекты
registry.register(imagebot_agent)
registry.register(chronicle_agent)
registry.register(skillbook_agent)
registry.register(nexus_agent)