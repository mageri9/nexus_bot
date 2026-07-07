from redis.asyncio import Redis  # [1]
from config import settings
from agents import registry

from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector
from .ai import AIService  # <--- Поменяли .ai_service на .ai

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

query_service = QueryService(registry=registry, redis_client=redis_client)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)
state_collector = StateCollector(registry=registry, redis_client=redis_client)

# Инициализируем синглтон AI сервиса
ai_service = AIService(query_service=query_service)