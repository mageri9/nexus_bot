from redis.asyncio import Redis
from config import settings
from agents import registry

from .classifier import Classifier
from .query import QueryService
from .event_bus import EventBus
from .command import CommandService
from .collector import StateCollector, LogCollector
from .ai import AIService
from .incident import IncidentService
from .pubsub_listener import PubSubListener
from .health_engine import HealthEngine

redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

classifier = Classifier(redis_client=redis_client)

health_engine = HealthEngine()
query_service = QueryService(registry=registry, redis_client=redis_client, health_engine=health_engine)
event_bus = EventBus()
command_service = CommandService(registry=registry, event_bus=event_bus)

state_collector = StateCollector(
    registry=registry,
    redis_client=redis_client,
    event_bus=event_bus,
    debounce_ticks=settings.COLLECTOR_DEBOUNCE_TICKS,
    health_engine=health_engine,
)
# Инициализация сборщика логов
log_collector = LogCollector(registry=registry, redis_client=redis_client)

incident_service = IncidentService(
    redis_client=redis_client,
    query_service=query_service,
    event_bus=event_bus,
    classifier=classifier # <-- Передаем классификатор
)

# Инициализируем слушатель Pub/Sub
pubsub_listener = PubSubListener(redis_client=redis_client, event_bus=event_bus)

event_bus.subscribe("ResourceStopped", incident_service.on_resource_failed)
event_bus.subscribe("ResourceUnhealthy", incident_service.on_resource_failed)
event_bus.subscribe("ResourceRecovered", incident_service.on_resource_recovered)

# Подписываем службу инцидентов на ошибки приложений, пришедшие из Pub/Sub
event_bus.subscribe("app:error", incident_service.on_app_error)

ai_service = AIService(
    query_service=query_service,
    event_bus=event_bus,
    redis_client=redis_client
)

# Подписываем AI Service на события использования токенов (как внутренних, так и внешних через Pub/Sub)
event_bus.subscribe("ai.request", ai_service.on_ai_request)

# Подписываем IncidentService на сборки для логирования в общую ленту событий хоста
event_bus.subscribe("devops:workflow_success", incident_service.on_devops_event)
event_bus.subscribe("devops:workflow_failure", incident_service.on_devops_event)


async def on_app_heartbeat(event_type: str, data: dict) -> None:
    """Сохраняет время последнего полученного пульса от приложения в Redis"""
    project = data.get("project")
    if project:
        timestamp = data.get("timestamp")
        # Если в пакете нет временной метки, используем текущее время сервера
        if not timestamp:
            from datetime import datetime, timezone
            timestamp = datetime.now(timezone.utc).isoformat()
        await redis_client.set(f"nexus:heartbeat:{project}", timestamp)

# Подписываем обработчик на внутренние события шины
event_bus.subscribe("app:heartbeat", on_app_heartbeat)

# Инициализация исторической памяти Nexus Intelligence Engine
from intelligence.storage import SqliteEventStorage
from intelligence.collector import IntelligenceCollector

event_storage = SqliteEventStorage()
intelligence_collector = IntelligenceCollector(
    event_bus=event_bus,
    storage=event_storage,
    classifier=classifier
)
intelligence_collector.register_subscriptions()