from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
import uuid

class EventRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    project: str
    resource: str
    severity: str = "INFO"
    source: str
    payload_json: str


class MetricSnapshot(BaseModel):
    """
    Модель снимка метрик конкретного ресурса в определенный момент времени.
    """
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: str
    resource: str
    status: str
    cpu: Optional[str] = None          # Например, "12.5%" (или None для дисков)
    mem_perc: Optional[str] = None     # Например, "4.2%" (или None для дисков)
    restarts: Optional[int] = None     # Число рестартов