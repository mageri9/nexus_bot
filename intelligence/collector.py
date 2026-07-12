import json
from datetime import datetime, timezone
from typing import Any, Dict
from loguru import logger

from services.event_bus import EventBus
from services.classifier import Classifier
from core.signal import Signal
from intelligence.models import EventRecord
from intelligence.storage import EventStorage

# Полный перечень отслеживаемых событий экосистемы
KNOWN_EVENTS = [
    "ResourceStarted",
    "ResourceStopped",
    "ResourceUnhealthy",
    "ResourceRecovered",
    "ResourceDeleted",
    "app:error",
    "app:heartbeat",
    "devops:workflow_success",
    "devops:workflow_failure",
    "action:started",
    "action:success",
    "action:failed",
    "incident:opened",
    "incident:resolved",
    "ai.request",
    "ml:anomaly_detected",
]


class IntelligenceCollector:
    """
    Подписчик шины событий, преобразующий все проходящие события в EventRecord
    и сохраняющий их в долгосрочную память хранилища.
    """
    def __init__(self, event_bus: EventBus, storage: EventStorage, classifier: Classifier):
        self.event_bus = event_bus
        self.storage = storage
        self.classifier = classifier

    def register_subscriptions(self) -> None:
        for event_type in KNOWN_EVENTS:
            self.event_bus.subscribe(event_type, self.on_event)
        logger.info(f"IntelligenceCollector registered subscriptions to {len(KNOWN_EVENTS)} event types.")

    async def on_event(self, event_type: str, data: Any) -> None:
        """
        Перехватчик события из EventBus. Изолирует ошибки сохранения,
        гарантируя бесперебойность основного потока.
        """
        try:
            # Приведение полезной нагрузки к словарю для упрощения разбора
            payload = data if isinstance(data, dict) else {}

            project, resource, severity, source = await self._parse_event_meta(event_type, payload)

            # Сериализация сырых данных
            try:
                payload_json = json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError) as err:
                payload_json = json.dumps({"error": f"Failed to serialize payload: {err}"})

            record = EventRecord(
                event_type=event_type,
                project=project,
                resource=resource,
                severity=severity,
                source=source,
                payload_json=payload_json,
                timestamp=datetime.now(timezone.utc),
            )

            await self.storage.save(record)
            logger.debug(f"IntelligenceCollector successfully saved event '{event_type}' for '{project}:{resource}'")

        except Exception as ex:
            # Ошибка логируется, но не прерывает работу EventBus
            logger.error(
                f"IntelligenceCollector failed to capture event '{event_type}': {ex}"
            )

    async def _parse_event_meta(
        self, event_type: str, payload: Dict[str, Any]
    ) -> tuple[str, str, str, str]:
        """
        Разбирает метаданные события и обогащает их на основе классификатора.
        Возвращает кортеж: (project, resource, severity, source)
        """
        # Обработка события аномалии
        if event_type == "ml:anomaly_detected":
            project = payload.get("project", "unknown")
            resource = payload.get("resource", "unknown")
            return project, resource, "WARNING", "intelligence"

        # 1. Сценарий: События, совместимые с сигналами (проходят классификатор)
        signal_compatible = {
            "ResourceStarted", "ResourceStopped", "ResourceUnhealthy", "ResourceRecovered", "ResourceDeleted",
            "app:error", "app:heartbeat", "devops:workflow_success", "devops:workflow_failure"
        }

        if event_type in signal_compatible:
            if event_type in ("devops:workflow_success", "devops:workflow_failure"):
                project = payload.get("repository", "unknown")
                resource = payload.get("workflow_name", "pipeline")
                source = "devops"
                status = "success" if "success" in event_type else "failure"
            elif event_type in ("app:error", "app:heartbeat"):
                project = payload.get("project", "unknown")
                resource = "app"
                source = "sdk"
                status = "error" if "error" in event_type else "healthy"
            else:
                project = payload.get("agent", "unknown")
                resource = payload.get("resource", "unknown")
                source = "collector"
                status = payload.get("new_status", "unknown")

            # Вызов классификатора для точного расчёта критичности
            severity = "INFO"
            try:
                signal = Signal(
                    project=project,
                    resource=resource,
                    source=source,
                    event_type=event_type,
                    status=status,
                    payload=payload
                )
                classified = await self.classifier.classify(signal)
                severity = classified.severity
            except Exception as ex:
                logger.debug(f"Classification failed for {event_type}, falling back to INFO: {ex}")

            return project, resource, severity, source

        # 2. Сценарий: События выполнения команд
        if event_type in ("action:started", "action:success", "action:failed"):
            project = payload.get("agent", "unknown")
            resource = payload.get("resource", "unknown")
            severity = "INFO"
            return project, resource, severity, "command"

        # 3. Сценарий: Записи инцидентов
        if event_type in ("incident:opened", "incident:resolved"):
            project = payload.get("project", "unknown")
            resource = payload.get("resource", "unknown")
            if event_type == "incident:opened":
                severity = payload.get("severity", "HIGH")
            else:
                severity = "SUCCESS"
            return project, resource, severity, "incident"

        # 4. Сценарий: Метрики использования ИИ
        if event_type == "ai.request":
            project = payload.get("project", "unknown")
            resource = "ai"
            severity = "INFO"
            return project, resource, severity, "ai"

        # Дефолтный фолбек для нетипичных сценариев
        return "unknown", "unknown", "INFO", "event_bus"