from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime, timezone


class Metric(BaseModel):
    key: str  # Идентификатор метрики (например, "directory_size")
    value: Any  # Сырое значение (число, строка или словарь)
    unit: Optional[str] = None  # Единица измерения ("bytes", "percent", "seconds")
    source: str  # Утилита-источник ("du", "df", "docker_inspect")

    # Использование default_factory предотвращает вычисление времени в момент импорта файла
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))