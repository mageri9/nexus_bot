from redis.asyncio import Redis
from config import settings
from agents import registry

from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector
from .ai import AIService

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

query_service = QueryService(registry=registry, redis_client=redis_client)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)

# Инжектируем event_bus третьим аргументом
state_collector = StateCollector(
    registry=registry,
    redis_client=redis_client,
    event_bus=event_bus
)

ai_service = AIService(query_service=query_service)