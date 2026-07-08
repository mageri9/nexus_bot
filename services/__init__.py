from redis.asyncio import Redis
from config import settings
from agents import registry

from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector
from .ai import AIService
from .incident import IncidentService  # Импортируем новый сервис

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

query_service = QueryService(registry=registry, redis_client=redis_client)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)
state_collector = StateCollector(registry=registry, redis_client=redis_client, event_bus=event_bus)

# Инициализируем IncidentService
incident_service = IncidentService(
    redis_client=redis_client,
    query_service=query_service,
    event_bus=event_bus
)

# Подписываем службу инцидентов на события коллектора
event_bus.subscribe("ResourceStopped", incident_service.on_resource_failed)
event_bus.subscribe("ResourceUnhealthy", incident_service.on_resource_failed)
event_bus.subscribe("ResourceRecovered", incident_service.on_resource_recovered)

ai_service = AIService(query_service=query_service)