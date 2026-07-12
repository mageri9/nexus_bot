from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class Signal(BaseModel):
    """Единая структура данных для любых событий экосистемы Nexus (сигналов)"""
    project: str          # Имя агента / проекта
    resource: str         # Имя ресурса (app, postgres, root_disk, etc.)
    source: str           # Источник сигнала: collector, sdk, devops
    event_type: str       # Первоначальный тип события (ResourceStopped, app:error, etc.)
    status: str           # Статус состояния (exited, unhealthy, healthy, error, success)
    payload: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))