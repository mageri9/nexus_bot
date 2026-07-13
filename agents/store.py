"""
agents/store.py

Персистентное хранилище манифеста Nexus (агенты + их ресурсы) в SQLite.
Заменяет ручное редактирование agents/manifest.py — тот файл теперь используется
только один раз, как seed-скрипт для первичной миграции текущих 5 проектов в БД
(см. agents/seed_from_manifest.py).

Используем sqlite3 (синхронный) + asyncio.to_thread, а не отдельную async-обвязку —
операций с манифестом мало и они не в горячем пути (в отличие от collector'а),
так что не тянем лишнюю зависимость вроде aiosqlite ради этого модуля.
"""
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime, timezone
import asyncio

DB_PATH = Path("data/agents.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    resource_key TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    config TEXT NOT NULL,
    UNIQUE(agent_name, resource_key)
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def _sync_save_agent(agent_name: str, resources: List[Dict[str, Any]]) -> None:
    """
    Создаёт (или дополняет) агента и его ресурсы. Идемпотентно по (agent_name, resource_key) —
    повторный вызов с тем же resource_key обновит config, а не задублирует строку.
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO agents (name, created_at) VALUES (?, ?)",
            (agent_name, datetime.now(timezone.utc).isoformat()),
        )
        for res in resources:
            conn.execute(
                """
                INSERT INTO resources (agent_name, resource_key, resource_type, config)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_name, resource_key)
                DO UPDATE SET resource_type = excluded.resource_type, config = excluded.config
                """,
                (agent_name, res["resource_key"], res["resource_type"], json.dumps(res["config"])),
            )
        conn.commit()
    finally:
        conn.close()


def _sync_load_all() -> Dict[str, List[Dict[str, Any]]]:
    """Возвращает {agent_name: [{"resource_key":..., "resource_type":..., "config": {...}}, ...]}"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT agent_name, resource_key, resource_type, config FROM resources"
        ).fetchall()
        result: Dict[str, List[Dict[str, Any]]] = {}
        for agent_name, resource_key, resource_type, config_raw in rows:
            result.setdefault(agent_name, []).append(
                {
                    "resource_key": resource_key,
                    "resource_type": resource_type,
                    "config": json.loads(config_raw),
                }
            )
        # Гарантируем, что агенты без ресурсов (маловероятно, но не запрещено) тоже попадут в вывод
        agent_names = [r[0] for r in conn.execute("SELECT name FROM agents").fetchall()]
        for name in agent_names:
            result.setdefault(name, [])
        return result
    finally:
        conn.close()


def _sync_agent_exists(agent_name: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT 1 FROM agents WHERE name = ?", (agent_name,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _sync_is_empty() -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT 1 FROM agents LIMIT 1").fetchone()
        return row is None
    finally:
        conn.close()


async def save_agent(agent_name: str, resources: List[Dict[str, Any]]) -> None:
    await asyncio.to_thread(_sync_save_agent, agent_name, resources)


async def load_all() -> Dict[str, List[Dict[str, Any]]]:
    return await asyncio.to_thread(_sync_load_all)


async def agent_exists(agent_name: str) -> bool:
    return await asyncio.to_thread(_sync_agent_exists, agent_name)


async def is_empty() -> bool:
    return await asyncio.to_thread(_sync_is_empty)