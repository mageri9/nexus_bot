from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime, timezone


def extract_metric_value(raw: Any) -> float | None:
    """Safely parse a numeric metric value from a metric object or raw value."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.replace("%", "").strip())
        except (ValueError, TypeError):
            return None
    return None


class Metric(BaseModel):
    key: str  # Идентификатор метрики (например, "directory_size")
    value: Any  # Сырое значение (число, строка или словарь)
    unit: Optional[str] = None  # Единица измерения ("bytes", "percent", "seconds")
    source: str  # Утилита-источник ("du", "df", "docker_inspect")

    # Использование default_factory предотвращает вычисление времени в момент импорта файла
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
