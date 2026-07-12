import os
import sqlite3
import json
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional
from intelligence.models import EventRecord

class EventStorage(ABC):
    """
    Абстрактный интерфейс долгосрочного хранилища событий.
    """
    @abstractmethod
    async def save(self, record: EventRecord) -> None:
        pass

    @abstractmethod
    async def query(
        self,
        limit: int = 100,
        project: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> List[EventRecord]:
        pass


class SqliteEventStorage(EventStorage):
    """
    Реализация хранилища на базе SQLite.
    Использует asyncio.to_thread для изоляции блокирующего ввода-вывода.
    """
    def __init__(self, db_path: str = "data/events.db"):
        self.db_path = db_path
        # Создаем директорию, если она отсутствует
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Синхронная инициализация структуры базы данных при старте."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    project TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            # Индексы для оптимизации аналитических выборок
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_timestamp ON event_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_project ON event_log(project)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)")
            conn.commit()

    def _save_sync(self, record: EventRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO event_log (event_id, timestamp, event_type, project, resource, severity, source, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.timestamp.isoformat(),
                    record.event_type,
                    record.project,
                    record.resource,
                    record.severity,
                    record.source,
                    record.payload_json,
                ),
            )
            conn.commit()

    def _query_sync(
        self,
        limit: int,
        project: Optional[str],
        event_type: Optional[str],
        severity: Optional[str],
    ) -> List[EventRecord]:
        query_str = "SELECT event_id, timestamp, event_type, project, resource, severity, source, payload_json FROM event_log"
        conditions = []
        params = []

        if project:
            conditions.append("project = ?")
            params.append(project)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if conditions:
            query_str += " WHERE " + " AND ".join(conditions)

        query_str += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query_str, tuple(params))
            rows = cursor.fetchall()

        records = []
        for row in rows:
            # Парсинг временной метки с поддержкой таймзоны UTC
            ts_str = row[1]
            try:
                dt = datetime.fromisoformat(ts_str)
            except ValueError:
                dt = datetime.now(timezone.utc)

            records.append(
                EventRecord(
                    event_id=row[0],
                    timestamp=dt,
                    event_type=row[2],
                    project=row[3],
                    resource=row[4],
                    severity=row[5],
                    source=row[6],
                    payload_json=row[7],
                )
            )
        return records

    async def save(self, record: EventRecord) -> None:
        await asyncio.to_thread(self._save_sync, record)

    async def query(
        self,
        limit: int = 100,
        project: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> List[EventRecord]:
        return await asyncio.to_thread(self._query_sync, limit, project, event_type, severity)