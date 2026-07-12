import os
import sqlite3
import json
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional
from intelligence.models import EventRecord, MetricSnapshot  # <-- Импорт MetricSnapshot

class EventStorage(ABC):
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

    @abstractmethod
    async def save_metric_snapshot(self, snapshot: MetricSnapshot) -> None:
        """Сохраняет снимок метрик ресурса."""
        pass

    @abstractmethod
    async def query_metric_snapshots(
        self,
        agent: str,
        resource: str,
        limit: int = 100,
    ) -> List[MetricSnapshot]:
        """Возвращает историю снимков метрик для анализа."""
        pass

    @abstractmethod
    async def query_all_metric_snapshots(
        self, limit: int = 10000
    ) -> List[MetricSnapshot]:
        """Возвращает все накопленные снимки метрик для построения обучающих выборок."""
        pass

class SqliteEventStorage(EventStorage):
    def __init__(self, db_path: str = "data/events.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # WAL вместо дефолтного journal_mode=DELETE: nexus-core (collector +
            # predictor) и scripts/train.py — разные процессы, оба пишут/читают
            # один и тот же файл. Без WAL писатель блокирует читателей сильнее,
            # чем нужно, и train.py, запущенный во время работы бота, легко
            # ловит "database is locked".
            conn.execute("PRAGMA journal_mode=WAL;")

            # Таблица логов событий
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_timestamp ON event_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_project ON event_log(project)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)")

            # Новая таблица снимков метрик (Квест 2)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cpu TEXT,
                    mem_perc TEXT,
                    restarts INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_snapshots_timestamp ON metric_snapshots(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_snapshots_agent_res ON metric_snapshots(agent, resource)")
            conn.commit()

    # --- Существующие синхронные методы ---
    def _save_sync(self, record: EventRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO event_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.event_id, record.timestamp.isoformat(), record.event_type, record.project, record.resource, record.severity, record.source, record.payload_json)
            )
            conn.commit()

    def _query_sync(self, limit: int, project: Optional[str], event_type: Optional[str], severity: Optional[str]) -> List[EventRecord]:
        query_str = "SELECT event_id, timestamp, event_type, project, resource, severity, source, payload_json FROM event_log"
        conditions, params = [], []
        if project: conditions.append("project = ?"); params.append(project)
        if event_type: conditions.append("event_type = ?"); params.append(event_type)
        if severity: conditions.append("severity = ?"); params.append(severity)
        if conditions: query_str += " WHERE " + " AND ".join(conditions)
        query_str += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query_str, tuple(params))
            rows = cursor.fetchall()
        return [EventRecord(event_id=r[0], timestamp=datetime.fromisoformat(r[1]), event_type=r[2], project=r[3], resource=r[4], severity=r[5], source=r[6], payload_json=r[7]) for r in rows]

    # --- Новые синхронные методы ---
    def _save_metric_snapshot_sync(self, snapshot: MetricSnapshot) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO metric_snapshots (snapshot_id, timestamp, agent, resource, status, cpu, mem_perc, restarts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.timestamp.isoformat(),
                    snapshot.agent,
                    snapshot.resource,
                    snapshot.status,
                    snapshot.cpu,
                    snapshot.mem_perc,
                    snapshot.restarts,
                ),
            )
            conn.commit()

    def _query_metric_snapshots_sync(self, agent: str, resource: str, limit: int) -> List[MetricSnapshot]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT snapshot_id, timestamp, agent, resource, status, cpu, mem_perc, restarts
                FROM metric_snapshots
                WHERE agent = ? AND resource = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (agent, resource, limit),
            )
            rows = cursor.fetchall()
        return [
            MetricSnapshot(
                snapshot_id=r[0],
                timestamp=datetime.fromisoformat(r[1]),
                agent=r[2],
                resource=r[3],
                status=r[4],
                cpu=r[5],
                mem_perc=r[6],
                restarts=r[7],
            )
            for r in rows
        ]

    def _query_all_metric_snapshots_sync(self, limit: int) -> List[MetricSnapshot]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # ORDER BY ... ASC LIMIT ? возвращает САМЫЕ СТАРЫЕ N снимков, как только
            # их накопится больше limit — обучающий датасет тогда молча застревает
            # на устаревшем окне и перестаёт видеть свежие инциденты и метрики.
            # Берём последние N по времени (DESC), затем разворачиваем обратно
            # в хронологический порядок для build_dataset()/time-split.
            cursor.execute(
                """
                SELECT snapshot_id, timestamp, agent, resource, status, cpu, mem_perc, restarts
                FROM metric_snapshots
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()[::-1]
        return [
            MetricSnapshot(
                snapshot_id=r[0],
                timestamp=datetime.fromisoformat(r[1]),
                agent=r[2],
                resource=r[3],
                status=r[4],
                cpu=r[5],
                mem_perc=r[6],
                restarts=r[7],
            )
            for r in rows
        ]


    async def save(self, record: EventRecord) -> None:
        await asyncio.to_thread(self._save_sync, record)

    async def query(self, limit: int = 100, project: Optional[str] = None, event_type: Optional[str] = None, severity: Optional[str] = None) -> List[EventRecord]:
        return await asyncio.to_thread(self._query_sync, limit, project, event_type, severity)

    async def save_metric_snapshot(self, snapshot: MetricSnapshot) -> None:
        await asyncio.to_thread(self._save_metric_snapshot_sync, snapshot)

    async def query_metric_snapshots(self, agent: str, resource: str, limit: int = 100) -> List[MetricSnapshot]:
        return await asyncio.to_thread(self._query_metric_snapshots_sync, agent, resource, limit)

    async def query_all_metric_snapshots(self, limit: int = 10000) -> List[MetricSnapshot]:
        return await asyncio.to_thread(self._query_all_metric_snapshots_sync, limit)