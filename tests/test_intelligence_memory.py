import os
import json
import pytest
from datetime import datetime, timezone

from services.event_bus import EventBus
from services.classifier import Classifier
from intelligence.models import EventRecord
from intelligence.storage import SqliteEventStorage
from intelligence.collector import IntelligenceCollector


@pytest.fixture
def temp_db_path(tmp_path):
    return str(tmp_path / "test_events.db")


@pytest.mark.asyncio
async def test_sqlite_event_storage_save_and_query(temp_db_path):
    storage = SqliteEventStorage(db_path=temp_db_path)

    record = EventRecord(
        event_type="ResourceStopped",
        project="imagebot",
        resource="app",
        severity="HIGH",
        source="collector",
        payload_json='{"status": "exited"}',
    )

    await storage.save(record)

    # Запрос данных из базы
    results = await storage.query(limit=5)
    assert len(results) == 1
    retrieved = results[0]

    assert retrieved.event_id == record.event_id
    assert retrieved.event_type == "ResourceStopped"
    assert retrieved.project == "imagebot"
    assert retrieved.resource == "app"
    assert retrieved.severity == "HIGH"
    assert retrieved.source == "collector"
    assert json.loads(retrieved.payload_json) == {"status": "exited"}


@pytest.mark.asyncio
async def test_sqlite_event_storage_query_filtering(temp_db_path):
    storage = SqliteEventStorage(db_path=temp_db_path)

    records = [
        EventRecord(
            event_type="app:error",
            project="tarot_bot",
            resource="app",
            severity="HIGH",
            source="sdk",
            payload_json="{}",
        ),
        EventRecord(
            event_type="app:heartbeat",
            project="tarot_bot",
            resource="app",
            severity="INFO",
            source="sdk",
            payload_json="{}",
        ),
        EventRecord(
            event_type="ResourceRecovered",
            project="chronicle",
            resource="bot",
            severity="SUCCESS",
            source="collector",
            payload_json="{}",
        ),
    ]

    for r in records:
        await storage.save(r)

    # Проверка фильтрации по проекту
    results_project = await storage.query(project="tarot_bot")
    assert len(results_project) == 2

    # Проверка фильтрации по типу события
    results_type = await storage.query(event_type="ResourceRecovered")
    assert len(results_type) == 1
    assert results_type[0].project == "chronicle"


@pytest.mark.asyncio
async def test_collector_captures_events_and_classifies_them(temp_db_path, fake_redis):
    event_bus = EventBus()
    storage = SqliteEventStorage(db_path=temp_db_path)
    classifier = Classifier(redis_client=fake_redis)

    collector = IntelligenceCollector(
        event_bus=event_bus, storage=storage, classifier=classifier
    )
    collector.register_subscriptions()

    # Имитируем падение ресурса, отправляя событие в EventBus
    await event_bus.publish(
        "ResourceStopped", {"agent": "nexus", "resource": "app", "new_status": "exited"}
    )

    # Даем немного времени для фоновой записи в поток
    results = await storage.query(limit=5)
    assert len(results) == 1

    captured = results[0]
    assert captured.event_type == "ResourceStopped"
    assert captured.project == "nexus"
    assert captured.resource == "app"
    assert captured.severity == "HIGH"  # Классификатор определил уровень критичности
    assert captured.source == "collector"


@pytest.mark.asyncio
async def test_collector_survives_storage_exceptions(temp_db_path, fake_redis):
    event_bus = EventBus()
    classifier = Classifier(redis_client=fake_redis)

    # Мок хранилища, которое бросает ошибку при записи
    class BrokenStorage(SqliteEventStorage):
        async def save(self, record: EventRecord) -> None:
            raise RuntimeError("Database physical corruption simulation")

    storage = BrokenStorage(db_path=temp_db_path)
    collector = IntelligenceCollector(
        event_bus=event_bus, storage=storage, classifier=classifier
    )
    collector.register_subscriptions()

    # Вызов публикации не должен бросать исключение наверх и ломать выполнение
    await event_bus.publish(
        "app:error",
        {
            "project": "tarot_bot",
            "message": "unhandled",
            "exception_type": "ValueError",
        },
    )
    # Тест пройден, если исключение успешно погашено внутри `on_event`