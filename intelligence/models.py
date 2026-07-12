from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class EventRecord(BaseModel):
    """
    Модель записи события для долгосрочного хранения в базе данных.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    project: str
    resource: str
    severity: str = "INFO"
    source: str
    payload_json: str