import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from core import ProjectAgent, Resource
from infra.docker import DockerContainer
from services.collector import StateCollector, LogCollector


class FakeResource(Resource):
    """
    Минимальный кастомный Resource (НЕ DockerContainer/HostDiskResource/
    ProjectStorageResource), как явно предусмотрено комментарием "Фолбек для
    кастомных типов ресурсов в будущем" в services/collector.py. Такие
    ресурсы попадают в бакет state_v2["other"].
    """

    def __init__(self, status: str, capabilities=None):
        super().__init__("fake", transport=None)
        self._status = status
        self.capabilities = capabilities or []

    async def get_status(self) -> str:
        return self._status

    async def get_metrics(self) -> dict:
        return {}


async def run_one_collector_tick(collector: StateCollector):
    """
    StateCollector._loop() — бесконечный `while True`. Чтобы протестировать
    ровно одну итерацию БЕЗ правки исходного кода, подменяем asyncio.sleep
    так, чтобы после первого вызова (конец итерации) он поднимал
    CancelledError. Внешний try/except в _loop ловит только `Exception`,
    поэтому CancelledError (BaseException) пробрасывается наружу как и
    задумано при штатной отмене задачи.
    """
    with patch(
        "services.collector.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ):
        with pytest.raises(asyncio.CancelledError):
            await collector._loop()


def make_collector(registry, fake_redis, event_bus):
    return StateCollector(
        registry=registry, redis_client=fake_redis, event_bus=event_bus, interval=5
    )


def make_docker_container(
    scripted_transport, status: str, name="app", container_name="app-1"
):
    c = DockerContainer(name, scripted_transport, container_name)
    scripted_transport.on(
        ["docker", "inspect", "-f", "{{.State.Status}}", container_name], status
    )
    return c


def set_status(scripted_transport, container_name: str, status: str):
    scripted_transport.on(
        ["docker", "inspect", "-f", "{{.State.Status}}", container_name], status
    )


ALL_TRANSITION_EVENTS = [
    "ResourceStarted",
    "ResourceStopped",
    "ResourceUnhealthy",
    "ResourceRecovered",
]


def subscribe_all(event_bus, subscriber):
    for et in ALL_TRANSITION_EVENTS:
        event_bus.subscribe(et, subscriber)


# ---- первое обнаружение ресурса ----


async def test_first_seen_healthy_resource_emits_resource_started(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    subscribe_all(event_bus, recording_subscriber)
    container = make_docker_container(scripted_transport, "running")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)

    assert recording_subscriber.types() == ["ResourceStarted"]


async def test_first_seen_unhealthy_resource_emits_resource_unhealthy(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    subscribe_all(event_bus, recording_subscriber)
    container = make_docker_container(scripted_transport, "exited")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)

    assert recording_subscriber.types() == ["ResourceUnhealthy"]


async def test_no_change_between_ticks_emits_nothing(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    container = make_docker_container(
        scripted_transport, "running", container_name="app-1"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)  # тик 1: засевает "running"

    subscribe_all(event_bus, recording_subscriber)
    await run_one_collector_tick(collector)  # тик 2: статус не менялся

    assert recording_subscriber.types() == []


# ---- полная матрица переходов состояний ----


@pytest.mark.parametrize(
    "old_status,new_status,expected_event",
    [
        ("running", "exited", "ResourceStopped"),
        ("running", "stopped", "ResourceStopped"),
        (
            "healthy",
            "unknown",
            "ResourceUnhealthy",
        ),  # was healthy, стал не-healthy, не stop-статус
        ("unknown", "healthy", "ResourceRecovered"),
        (
            "unknown",
            "exited",
            "ResourceStopped",
        ),  # unhealthy -> stopped-подобный статус
    ],
)
async def test_transition_matrix_emits_expected_event(
    registry,
    fake_redis,
    event_bus,
    scripted_transport,
    recording_subscriber,
    old_status,
    new_status,
    expected_event,
):
    container = make_docker_container(
        scripted_transport, old_status, container_name="app-1"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)  # засевает старый статус

    subscribe_all(event_bus, recording_subscriber)
    set_status(scripted_transport, "app-1", new_status)
    await run_one_collector_tick(collector)

    assert recording_subscriber.types() == [expected_event], (
        f"{old_status} -> {new_status} should emit {expected_event}, "
        f"got {recording_subscriber.types()}"
    )


async def test_transition_between_two_unhealthy_non_stop_statuses_emits_nothing(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    """unknown -> unhealthy: оба состояния не healthy, и ни один не входит в
    ('exited','stopped','dead') - по текущей логике коллектора событие не
    генерируется вовсе (граничный случай матрицы переходов, задокументированное
    поведение, а не баг)."""
    container = make_docker_container(
        scripted_transport, "unknown", container_name="app-1"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)
    await run_one_collector_tick(collector)

    subscribe_all(event_bus, recording_subscriber)
    set_status(scripted_transport, "app-1", "unhealthy")
    await run_one_collector_tick(collector)

    assert recording_subscriber.types() == []


async def test_resource_removed_from_manifest_emits_resource_deleted(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    event_bus.subscribe("ResourceDeleted", recording_subscriber)
    container = make_docker_container(
        scripted_transport, "running", container_name="app-1"
    )
    agent = ProjectAgent(name="nexus", resources={"app": container})
    registry.register(agent)
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)

    # Ресурс "удалён" из манифеста между тиками
    agent.resources.pop("app")
    await run_one_collector_tick(collector)

    assert recording_subscriber.types() == ["ResourceDeleted"]
    payload = recording_subscriber.received[0][1]
    assert payload["resource"] == "app"
    assert payload["new_status"] == "deleted"


async def test_state_is_persisted_to_redis_as_v2(
    registry, fake_redis, event_bus, scripted_transport
):
    container = make_docker_container(scripted_transport, "running")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)

    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert state["version"] == 2
    assert state["containers"]["app"]["status"] == "running"
    assert state["storage"] == {}


async def test_resource_metrics_error_is_captured_without_crashing_tick(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    # docker inspect (status) настроен, но docker stats (metrics) - нет,
    # get_metrics() внутри себя ловит исключение и возвращает {"error": ...}
    subscribe_all(event_bus, recording_subscriber)
    container = make_docker_container(scripted_transport, "running")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)

    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert "error" in state["containers"]["app"]["metrics"]
    assert recording_subscriber.types() == ["ResourceStarted"]


# ---- баг, обнаруженный в процессе написания тестов ----


async def test_custom_other_category_resource_transitions_are_never_tracked_KNOWN_BUG(
    registry, fake_redis, event_bus, recording_subscriber
):
    """
    ВНИМАНИЕ: этот тест фиксирует текущее (некорректное) поведение, а не
    желаемое — аналогично тесту про TTL инцидентов в test_incident_service.py.

    services/collector.py раскладывает ресурсы по трём категориям:
    "containers" (DockerContainer), "storage" (HostDiskResource /
    ProjectStorageResource) и "other" (любой другой Resource — сам
    core/resource.py прямо предусматривает это как "Фолбек для кастомных
    типов ресурсов в будущем").

    Но при восстановлении old_resources_statuses в начале каждого тика
    читаются только "containers" и "storage":

        for res_name, res_data in old_state.get("containers", {}).items(): ...
        for res_name, res_data in old_state.get("storage", {}).items(): ...

    "other" не читается никогда. Следствие: для любого кастомного типа
    ресурса old_status всегда None, поэтому коллектор на КАЖДОМ тике
    считает его "впервые увиденным" — событие ResourceStarted /
    ResourceUnhealthy будет публиковаться повторно каждые `interval` секунд,
    даже если статус ресурса вообще не менялся. Реальные переходы
    (ResourceStopped/ResourceRecovered/ResourceDeleted) для таких ресурсов
    никогда не сработают.

    Сейчас в agents/manifest.py кастомные типы не используются, поэтому баг
    спит. Но он "выстрелит" в момент, когда кто-то добавит новый класс
    Resource помимо DockerContainer/HostDiskResource/ProjectStorageResource.

    Если это когда-нибудь исправят (учтут "other" при восстановлении
    old_resources_statuses), тест ниже должен начать падать на последнем
    ассерте — тогда его нужно инвертировать.
    """
    resource = FakeResource("running")
    registry.register(ProjectAgent(name="nexus", resources={"custom": resource}))
    collector = make_collector(registry, fake_redis, event_bus)

    await run_one_collector_tick(collector)  # тик 1: должен быть "первое обнаружение"

    subscribe_all(event_bus, recording_subscriber)
    # Статус НЕ меняется между тиками
    await run_one_collector_tick(
        collector
    )  # тик 2: ожидаемо - тишина, фактически - снова "первое обнаружение"

    assert recording_subscriber.types() == ["ResourceStarted"], (
        "Если это упало - значит баг с потерей old_status для ресурсов из "
        "категории 'other' починили. Обнови README и удали/инвертируй этот тест."
    )


async def test_debounce_transition_requires_m_ticks(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    subscribe_all(event_bus, recording_subscriber)
    container = make_docker_container(
        scripted_transport, "running", container_name="app-1"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    # Создаем коллектор с debounce_ticks = 3
    collector = StateCollector(
        registry=registry,
        redis_client=fake_redis,
        event_bus=event_bus,
        interval=5,
        debounce_ticks=3,
    )

    # Тик 1: инициализация начального состояния (коммитится сразу при первом обнаружении)
    await run_one_collector_tick(collector)
    assert recording_subscriber.types() == ["ResourceStarted"]
    recording_subscriber.received.clear()

    # Меняем статус контейнера на "exited"
    set_status(scripted_transport, "app-1", "exited")

    # Тик 2 (1-й тик изменения): статус не сохраняется в Redis, событие отсутствует
    await run_one_collector_tick(collector)
    assert recording_subscriber.types() == []
    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert state["containers"]["app"]["status"] == "running"

    # Тик 3 (2-й тик изменения): статус по-прежнему "running" в Redis, события нет
    await run_one_collector_tick(collector)
    assert recording_subscriber.types() == []
    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert state["containers"]["app"]["status"] == "running"

    # Тик 4 (3-й тик изменения — порог достигнут): переход фиксируется, статус "exited", летит событие
    await run_one_collector_tick(collector)
    assert recording_subscriber.types() == ["ResourceStopped"]
    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert state["containers"]["app"]["status"] == "exited"


async def test_debounce_resets_if_status_reverts_before_threshold(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    subscribe_all(event_bus, recording_subscriber)
    container = make_docker_container(
        scripted_transport, "running", container_name="app-1"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    collector = StateCollector(
        registry=registry,
        redis_client=fake_redis,
        event_bus=event_bus,
        interval=5,
        debounce_ticks=3,
    )

    # Тик 1: инициализируем
    await run_one_collector_tick(collector)
    recording_subscriber.received.clear()

    # Кратковременный сбой на 1 тик
    set_status(scripted_transport, "app-1", "exited")
    await run_one_collector_tick(collector)  # 1-й тик сбоя
    assert recording_subscriber.types() == []

    # Возвращаемся в исходное состояние "running" до достижения порога 3 тиков
    set_status(scripted_transport, "app-1", "running")
    await run_one_collector_tick(collector)  # сброс накопленного дребезга
    assert recording_subscriber.types() == []

    # Повторная проверка стабильности
    await run_one_collector_tick(collector)
    assert recording_subscriber.types() == []

    raw = await fake_redis.get("nexus:state:nexus")
    state = json.loads(raw)
    assert state["containers"]["app"]["status"] == "running"


async def test_log_collector_pushes_to_redis(registry, fake_redis):
    container = DockerContainer("app", None, "nexus-core")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    collector = LogCollector(registry, fake_redis, limit=5)
    collector._running = True  # <-- Явно взводим флаг для работы цикла в тесте

    # Мокаем процесс чтения
    mock_proc = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(
        side_effect=[
            b"line 1\n",
            b"line 2\n",
            b"",  # конец стрима логов
        ]
    )
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # Запускаем стрим в отдельном таске
        task = asyncio.create_task(
            collector._stream_container_logs("nexus", "app", "nexus-core")
        )

        # Даем отработать и мягко отменяем
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Проверяем наполнение буфера в Redis
    logs_raw = await fake_redis.lrange("nexus:logs:nexus:app", 0, -1)
    assert logs_raw == ["line 1", "line 2"]


async def test_log_collector_respects_buffer_limit(registry, fake_redis):
    container = DockerContainer("app", None, "nexus-core")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    # Буфер жестко ограничен 3 строками
    collector = LogCollector(registry, fake_redis, limit=3)
    collector._running = True  # <-- Явно взводим флаг для работы цикла в тесте

    mock_proc = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(
        side_effect=[b"1\n", b"2\n", b"3\n", b"4\n", b"5\n", b""]
    )
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        task = asyncio.create_task(
            collector._stream_container_logs("nexus", "app", "nexus-core")
        )
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # В Redis должны остаться только последние 3 строки
        logs_raw = await fake_redis.lrange("nexus:logs:nexus:app", 0, -1)
        assert logs_raw == ["3", "4", "5"]


async def test_app_error_auto_recovery_resolves_stale_incident(
    registry, fake_redis, event_bus, scripted_transport, recording_subscriber
):
    event_bus.subscribe("ResourceRecovered", recording_subscriber)

    collector = StateCollector(
        registry=registry,
        redis_client=fake_redis,
        event_bus=event_bus,
        interval=5,
        debounce_ticks=1,
    )

    # Симулируем открытый инцидент приложения в Redis
    await fake_redis.set("nexus:incident:active:tarot_bot:app", "42")

    incident_details = {
        "id": "42",
        "project": "tarot_bot",
        "resource": "app",
        "severity": "HIGH",
        "status": "open",
        "opened_at": "2026-07-12T00:00:00Z",
        "reason": "Application Exception: KeyError",
        "fingerprint": "mock_fp_1",
    }
    await fake_redis.set("nexus:incident:detail:42", json.dumps(incident_details))

    # Симулируем устаревшую ошибку в Error Registry (время last_seen было 70 секунд назад при пороге 60)
    from datetime import datetime, timezone, timedelta

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=70)

    await fake_redis.hset(
        "nexus:errors:mock_fp_1",
        mapping={
            "project": "tarot_bot",
            "exception_type": "KeyError",
            "last_seen": stale_time.isoformat(),
            "count": "1",
        },
    )

    # Запускаем проверку авто-восстановления
    await collector._check_app_auto_recovery()

    # Проверяем, что событие восстановления для tarot_bot успешно сгенерировано
    assert recording_subscriber.types() == ["ResourceRecovered"]
    payload = recording_subscriber.received[0][1]
    assert payload["agent"] == "tarot_bot"
    assert payload["resource"] == "app"
