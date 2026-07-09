from redis.asyncio import Redis
from config import settings
from agents import registry

from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector
from .ai import AIService
from .incident import IncidentService
from .pubsub_listener import PubSubListener
from .health_engine import HealthEngine

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

health_engine = HealthEngine()
query_service = QueryService(registry=registry, redis_client=redis_client, health_engine=health_engine)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)
state_collector = StateCollector(registry=registry, redis_client=redis_client, event_bus=event_bus)

incident_service = IncidentService(
    redis_client=redis_client,
    query_service=query_service,
    event_bus=event_bus
)

# Инициализируем слушатель Pub/Sub
pubsub_listener = PubSubListener(redis_client=redis_client, event_bus=event_bus)

event_bus.subscribe("ResourceStopped", incident_service.on_resource_failed)
event_bus.subscribe("ResourceUnhealthy", incident_service.on_resource_failed)
event_bus.subscribe("ResourceRecovered", incident_service.on_resource_recovered)

ai_service = AIService(
    query_service=query_service,
    event_bus=event_bus,
    redis_client=redis_client
)

# Подписываем AI Service на события использования токенов (как внутренних, так и внешних через Pub/Sub)
event_bus.subscribe("ai.request", ai_service.on_ai_request)