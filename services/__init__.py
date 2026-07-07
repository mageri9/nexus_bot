from redis.asyncio import Redis  # [1]
from config import settings
from agents import registry

from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector

# Инициализируем асинхронный синглтон-клиент Redis [1]
redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

query_service = QueryService(registry=registry, redis_client=redis_client)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)

# Инициализируем сборщик состояний
state_collector = StateCollector(registry=registry, redis_client=redis_client)