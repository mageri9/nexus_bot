from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from agents import registry

query_service = QueryService(registry=registry)
event_bus = EventBus()
# Передаем EventBus в CommandService через DI
command_service = CommandService(registry=registry, event_bus=event_bus)