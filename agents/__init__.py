from core import AgentRegistry
from .manifest import imagebot_agent, tarot_agent, chronicle_agent, nexus_agent, quant_agent

# Создаем центральный реестр Nexus
registry = AgentRegistry()

# Регистрируем проекты хоста
registry.register(imagebot_agent)
registry.register(tarot_agent)
registry.register(chronicle_agent)
registry.register(nexus_agent)
registry.register(quant_agent)