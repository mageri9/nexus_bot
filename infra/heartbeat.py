from datetime import datetime, timezone
from typing import Dict, Any
from core.resource import Resource
from core.telemetry import Metric


class ApplicationHeartbeat(Resource):
    """Мониторинг работоспособности приложения на основе регулярных Heartbeat-сигналов"""

    def __init__(self, name: str, project_name: str, max_gap_seconds: int = 30):
        # Ресурс пульса виртуальный, транспорт ему не требуется
        super().__init__(name, transport=None)
        self.project_name = project_name
        self.max_gap_seconds = max_gap_seconds
        self.capabilities = []

    async def get_status(self) -> str:
        # Импортируем лениво, исключая циклическую зависимость при сборке манифеста
        from services import redis_client

        key = f"nexus:heartbeat:{self.project_name}"
        ts_raw = await redis_client.get(key)
        if not ts_raw:
            return "unhealthy"

        try:
            last_hb = datetime.fromisoformat(ts_raw)
            now = datetime.now(timezone.utc)
            gap = (now - last_hb).total_seconds()

            if gap > self.max_gap_seconds:
                return "unhealthy"
            return "healthy"
        except Exception:
            return "unhealthy"

    async def get_metrics(self) -> Dict[str, Any]:
        from services import redis_client

        key = f"nexus:heartbeat:{self.project_name}"
        ts_raw = await redis_client.get(key)

        now = datetime.now(timezone.utc)
        gap = 999999.0  # Значение по умолчанию, если пульса никогда не было

        if ts_raw:
            try:
                last_hb = datetime.fromisoformat(ts_raw)
                gap = (now - last_hb).total_seconds()
            except Exception:
                pass

        now_ts = datetime.now(timezone.utc)
        return {
            "heartbeat_gap": Metric(
                key="heartbeat_gap",
                value=round(gap, 1),
                unit="seconds",
                source="nexus_heartbeat",
                timestamp=now_ts,
            ).model_dump(mode="json")
        }