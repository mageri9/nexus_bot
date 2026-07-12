from loguru import logger
from redis.asyncio import Redis
from core.signal import Signal


class ClassifiedSignal:
    """Обогащенный сигнал с решением о дальнейшей обработке"""
    def __init__(self, signal: Signal, severity: str, action: str):
        self.signal = signal
        self.severity = severity  # 'HIGH', 'MEDIUM', 'LOW', 'INFO', 'SUCCESS', 'WARNING'
        self.action = action      # 'process' (обрабатывать), 'ignore' (игнорировать)


class Classifier:
    """Унифицированный классификатор входящих сигналов"""
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def classify(self, signal: Signal) -> ClassifiedSignal:
        # 1. Проверяем режим обслуживания (Maintenance Mode)
        maintenance_key = f"nexus:maintenance:{signal.project}"
        is_maint = await self.redis.exists(maintenance_key) > 0
        if is_maint:
            logger.debug(f"Classifier: Signal for '{signal.project}' ignored due to maintenance mode.")
            return ClassifiedSignal(signal, severity="INFO", action="ignore")

        severity = "INFO"
        action = "process"

        # 2. Определение уровня важности в зависимости от источника и характера события
        if signal.source == "collector":
            if signal.event_type == "ResourceStopped":
                severity = "HIGH"
            elif signal.event_type == "ResourceUnhealthy":
                severity = "MEDIUM"
            elif signal.event_type == "ResourceRecovered":
                severity = "SUCCESS"
            elif signal.event_type == "ResourceDeleted":
                severity = "WARNING"

        elif signal.source == "sdk":
            if signal.event_type == "app:error":
                severity = "HIGH"
            elif signal.event_type == "app:heartbeat":
                severity = "INFO"
                action = "ignore"  # Пульсы обрабатываются отдельно, инцидент создавать не нужно

        elif signal.source == "devops":
            if signal.event_type == "devops:workflow_success":
                severity = "SUCCESS"
            elif signal.event_type == "devops:workflow_failure":
                severity = "HIGH"

        return ClassifiedSignal(signal, severity=severity, action=action)