import os
import json
import pytest
from datetime import datetime, timezone

from services.event_bus import EventBus
from services.classifier import Classifier
from intelligence.models import EventRecord
from intelligence.storage import SqliteEventStorage
from intelligence.collector import IntelligenceCollector
from intelligence.anomaly import check_anomaly, parse_float_metric


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


@pytest.mark.asyncio
async def test_collector_saves_metric_snapshots(
    temp_db_path, fake_redis, event_bus, scripted_transport, registry
):
    import asyncio
    from core import ProjectAgent
    from services.collector import StateCollector
    from infra.docker import DockerContainer

    # 1. Настраиваем окружение
    container = DockerContainer("app", scripted_transport, "nexus-core")
    scripted_transport.on(
        ["docker", "inspect", "-f", "{{.State.Status}}", "nexus-core"], "running"
    )
    scripted_transport.on(
        [
            "docker",
            "stats",
            "nexus-core",
            "--no-stream",
            "--format",
            "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
        ],
        "15.5%|45.2%|200MiB / 1024MiB",
    )
    scripted_transport.on(
        [
            "docker",
            "inspect",
            "-f",
            "{{.RestartCount}}|{{.State.StartedAt}}",
            "nexus-core",
        ],
        "2|2026-07-12T00:00:00.000000000Z",
    )

    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    storage = SqliteEventStorage(db_path=temp_db_path)

    # 2. Инициализируем StateCollector
    collector = StateCollector(
        registry=registry,
        redis_client=fake_redis,
        event_bus=event_bus,
        interval=5,
        debounce_ticks=1,
        event_storage=storage,
    )

    # 3. Выполняем ровно один тик коллектора
    from unittest.mock import AsyncMock, patch

    with patch(
        "services.collector.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await collector._loop()

    # 4. Проверяем, что в базе появился снимок
    snapshots = await storage.query_metric_snapshots(agent="nexus", resource="app")
    assert len(snapshots) == 1
    snap = snapshots[0]

    assert snap.agent == "nexus"
    assert snap.resource == "app"
    assert snap.status == "running"
    assert snap.cpu == "15.5%"
    assert snap.mem_perc == "45.2%"
    assert snap.restarts == 2


def test_parse_float_metric():
    assert parse_float_metric("12.34%") == 12.34
    assert parse_float_metric(55) == 55.0
    assert parse_float_metric(None) is None
    assert parse_float_metric("invalid") is None


def test_check_anomaly_math():
    # Стабильная история без колебаний
    history = [10.0, 10.0, 10.0, 10.0, 10.0]
    is_anom, _, _ = check_anomaly(100.0, history)
    assert (
        is_anom is False
    )  # Стандартное отклонение слишком мало, тест отклонен во избежание шума

    # Нормальные рабочие колебания
    history_fluct = [10.0, 12.0, 11.0, 9.0, 10.0]
    # Среднее = 10.4, Стандартное отклонение (std) ~ 1.14

    # Значение в пределах нормы (Z-score < 3.0)
    is_anom_normal, _, _ = check_anomaly(12.0, history_fluct)
    assert is_anom_normal is False

    # Аномальный всплеск (current=25.0, отклонение больше чем в 10 раз)
    is_anom_high, mean, std = check_anomaly(25.0, history_fluct)
    assert is_anom_high is True
    assert mean == 10.4


@pytest.mark.asyncio
async def test_collector_detects_and_publishes_anomaly(temp_db_path, fake_redis, event_bus, registry):
    from services.collector import StateCollector
    from intelligence.collector import IntelligenceCollector
    from intelligence.models import MetricSnapshot

    # Инициализируем чистое хранилище и классификатор для теста
    storage = SqliteEventStorage(db_path=temp_db_path)
    classifier = Classifier(redis_client=fake_redis)

    # 1. Предварительно записываем в базу историю нагрузки с небольшими колебаниями (std > 0)
    for i in range(10):
        cpu_val = "10.0%" if i % 2 == 0 else "11.0%"
        snap = MetricSnapshot(
            agent="nexus",
            resource="app",
            status="running",
            cpu=cpu_val,
            mem_perc="10.0%",
            restarts=0
        )
        await storage.save_metric_snapshot(snap)

    # 2. Инициализируем StateCollector
    collector = StateCollector(
        registry=registry,
        redis_client=fake_redis,
        event_bus=event_bus,
        interval=5,
        debounce_ticks=1,
        event_storage=storage
    )

    # 3. Инициализируем и регистрируем в шине IntelligenceCollector
    # Он должен поймать опубликованную аномалию и записать её в event_log
    intel_collector = IntelligenceCollector(
        event_bus=event_bus,
        storage=storage,
        classifier=classifier
    )
    intel_collector.register_subscriptions()

    # Настраиваем локальный слушатель для верификации отправки в шину
    published_anomalies = []
    async def sub_anomaly(et, data):
        published_anomalies.append(data)
    event_bus.subscribe("ml:anomaly_detected", sub_anomaly)

    # 4. Вызываем проверку аномалий напрямую с текущей пиковой нагрузкой в 95.0%
    metrics = {
        "cpu": {
            "key": "cpu",
            "value": "95.0%",
            "unit": "percent",
            "source": "docker_stats"
        }
    }
    await collector._check_and_publish_anomalies("nexus", "app", metrics)

    # 5. Проверяем, что событие аномалии было успешно опубликовано в EventBus
    assert len(published_anomalies) == 1
    anomaly_payload = published_anomalies[0]
    assert anomaly_payload["project"] == "nexus"
    assert anomaly_payload["resource"] == "app"
    assert anomaly_payload["metric"] == "cpu"
    assert anomaly_payload["current_value"] == "95.0%"

    # 6. Проверяем, что аномалия автоматически записалась в историческую ленту событий базы данных
    events = await storage.query(event_type="ml:anomaly_detected")
    assert len(events) == 1
    assert events[0].severity == "WARNING"
    assert events[0].source == "intelligence"


@pytest.mark.asyncio
async def test_telegram_notifier_sends_anomaly_alert(fake_redis):
    from unittest.mock import AsyncMock
    from telegram.notifier import TelegramNotifier
    import telegram.notifier

    # Подменяем реальный клиент Redis в модуле на фейковый для изоляции теста
    telegram.notifier.redis_client = fake_redis

    # Настраиваем мок-объект бота Telegram
    mock_bot = AsyncMock()
    notifier = TelegramNotifier(bot=mock_bot)
    notifier.admin_ids = [99999]

    data = {
        "project": "tarot_bot",
        "resource": "app",
        "metric": "cpu",
        "current_value": "95.0%",
        "mean": "10.50%",
        "std": "0.53",
    }

    # Сценарий 1: Обычная отправка (режим тишины выключен)
    await notifier.on_anomaly_detected("ml:anomaly_detected", data)
    assert mock_bot.send_message.call_count == 1

    args, kwargs = mock_bot.send_message.call_args
    assert kwargs["chat_id"] == 99999
    assert "TAROT_BOT" in kwargs["text"]
    assert "95.0%" in kwargs["text"]
    assert "Процессор" in kwargs["text"]

    # Сценарий 2: Подавление уведомления (режим тишины включен)
    mock_bot.send_message.reset_mock()
    await fake_redis.set("nexus:silence:tarot_bot:app", "1")

    await notifier.on_anomaly_detected("ml:anomaly_detected", data)
    assert mock_bot.send_message.call_count == 0  # Сообщение не должно быть отправлено