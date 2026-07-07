from .query import QueryService
from .command import CommandService
from agents import registry

# Экспортируем синглтоны для CQS-слоя Nexus
query_service = QueryService(registry=registry)
command_service = CommandService(registry=registry)