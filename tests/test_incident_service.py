import asyncio
import pytest
from core import ProjectAgent
from infra.docker import DockerContainer
from services.incident import IncidentService
from services.query import QueryService
from services.health_engine import HealthEngine


@pytest.fixture
def query_service(registry, fake_redis):
    return QueryService(
        registry=registry, redis_client=fake_redis, health_engine=HealthEngine()
    )


@pytest.fixture
def incident_service(fake_redis, query_service, event_bus):
    return IncidentService(
        redis_client=fake_redis, query_service=query_service, event_bus=event_bus
    )


def failed_payload(agent="nexus", resource="app", new_status="exited"):
    return {
        "agent": agent,
        "resource": resource,
        "old_status": "running",
        "new_status": new_status,
    }


# ---- открытие инцидента ----


async def test_on_resource_failed_creates_incident_with_incrementing_id(
    incident_service, fake_redis
):
    await incident_service.on_resource_failed(
        "ResourceStopped", failed_payload(resource="a")
    )
    await incident_service.on_resource_failed(
        "ResourceStopped", failed_payload(resource="b")
    )

    ids = await fake_redis.lrange("nexus:incidents:history", 0, -1)
    assert ids == ["2", "1"]  # lpush -> самый свежий первым


async def test_on_resource_failed_publishes_incident_opened_event(
    incident_service, event_bus, recording_subscriber
):
    event_bus.subscribe("incident:opened", recording_subscriber)

    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    assert recording_subscriber.types() == ["incident:opened"]
    payload = recording_subscriber.received[0][1]
    assert payload["project"] == "nexus"
    assert payload["resource"] == "app"
    assert payload["status"] == "open"


@pytest.mark.parametrize(
    "event_type,expected_severity",
    [("ResourceStopped", "HIGH"), ("ResourceUnhealthy", "MEDIUM")],
)
async def test_severity_depends_on_event_type(
    incident_service, event_type, expected_severity
):
    await incident_service.on_resource_failed(event_type, failed_payload())

    incident = await incident_service.get_incident("1")
    assert incident.severity == expected_severity


async def test_duplicate_failure_event_does_not_create_second_incident(
    incident_service, fake_redis
):
    """
    Ключевая гарантия IncidentService: атомарная блокировка через SET NX не
    должна давать создавать дублирующиеся инциденты для одного и того же
    ресурса, пока первый инцидент ещё открыт.
    """
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())
    await incident_service.on_resource_failed("ResourceUnhealthy", failed_payload())

    ids = await fake_redis.lrange("nexus:incidents:history", 0, -1)
    assert ids == ["1"]


async def test_concurrent_failure_events_create_only_one_incident(
    incident_service, fake_redis
):
    """То же самое, но при гонке нескольких почти одновременных событий."""
    await asyncio.gather(
        *[
            incident_service.on_resource_failed("ResourceStopped", failed_payload())
            for _ in range(10)
        ]
    )

    ids = await fake_redis.lrange("nexus:incidents:history", 0, -1)
    assert ids == ["1"]


async def test_incident_captures_logs_from_resource(
    incident_service, registry, scripted_transport, fake_redis
):
    container = DockerContainer("app", scripted_transport, "nexus-core")
    scripted_transport.on(
        ["docker", "logs", "--tail", "30", "nexus-core"], "boom: segfault"
    )
    scripted_transport.on(
        ["docker", "inspect", "-f", "{{.State.RestartCount}}", "nexus-core"], "4"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    incident = await incident_service.get_incident("1")
    assert incident.logs == "boom: segfault"
    assert incident.restart_count == 4


async def test_incident_survives_log_collection_failure(incident_service, registry):
    """Ресурс не найден в registry вообще -> get_resource_logs кинет KeyError,
    но инцидент всё равно должен быть создан с сообщением об ошибке в logs."""
    registry.register(ProjectAgent(name="nexus", resources={}))

    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    incident = await incident_service.get_incident("1")
    assert incident is not None
    assert "Не удалось извлечь логи" in incident.logs


async def test_incident_writes_timeline_entry(incident_service):
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    timeline = await incident_service.get_timeline(5)
    assert len(timeline) == 1
    assert timeline[0]["severity"] == "HIGH"
    assert "app" in timeline[0]["text"]


# ---- восстановление ----


async def test_on_resource_recovered_closes_incident_and_computes_duration(
    incident_service,
):
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    await incident_service.on_resource_recovered(
        "ResourceRecovered", {"agent": "nexus", "resource": "app"}
    )

    incident = await incident_service.get_incident("1")
    assert incident.status == "resolved"
    assert incident.resolved_at is not None
    assert incident.duration is not None
    assert incident.duration >= 0


async def test_on_resource_recovered_publishes_incident_resolved_event(
    incident_service, event_bus, recording_subscriber
):
    event_bus.subscribe("incident:resolved", recording_subscriber)
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    await incident_service.on_resource_recovered(
        "ResourceRecovered", {"agent": "nexus", "resource": "app"}
    )

    assert recording_subscriber.types() == ["incident:resolved"]


async def test_on_resource_recovered_with_no_active_incident_is_a_noop(
    incident_service, event_bus, recording_subscriber
):
    event_bus.subscribe("incident:resolved", recording_subscriber)

    # Восстановление без предшествующего сбоя - ничего не должно случиться
    await incident_service.on_resource_recovered(
        "ResourceRecovered", {"agent": "nexus", "resource": "app"}
    )

    assert recording_subscriber.received == []


async def test_after_recovery_a_new_failure_opens_a_fresh_incident(
    incident_service, fake_redis
):
    """После штатного восстановления лок снимается явным DELETE, и следующий
    сбой того же ресурса должен завести новый (второй) инцидент."""
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())
    await incident_service.on_resource_recovered(
        "ResourceRecovered", {"agent": "nexus", "resource": "app"}
    )
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    ids = await fake_redis.lrange("nexus:incidents:history", 0, -1)
    assert ids == ["2", "1"]


# ---- известная проблема, найденная при ревью кода ----


async def test_active_incident_lock_has_no_ttl_KNOWN_BUG(incident_service, fake_redis):
    """
    ВНИМАНИЕ: этот тест фиксирует текущее (некорректное) поведение, а не
    желаемое.

    В коде `on_resource_failed` ставит блокировку через:
        await self.redis.set(active_key, incident_id, nx=True)
    без параметра `ex=...`. Комментарий в коде и README утверждают, что лок
    ставится "с TTL 1 час", но фактически TTL не выставляется вовсе — ключ
    живёт вечно, пока не будет явно удалён в `on_resource_recovered`.

    Практическое следствие: если ресурс не пришлёт штатное событие
    ResourceRecovered (например, был удалён из манифеста, а не восстановлен),
    блокировка останется в Redis навсегда и заблокирует создание новых
    инцидентов для ресурса с тем же именем.

    Если это поведение когда-нибудь исправят (добавят `ex=3600`), этот тест
    должен начать падать — тогда его нужно удалить или инвертировать вместе
    с обновлением README/комментариев в коде.
    """
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    active_key = "nexus:incident:active:nexus:app"
    ttl = await fake_redis.ttl(active_key)

    # -1 в Redis означает "ключ существует, но TTL не установлен"
    assert ttl == -1, (
        "Похоже, TTL для блокировки инцидента теперь установлен - отлично! "
        "Обнови README (раздел про SET NX) и удали/инвертируй этот тест."
    )


async def test_resource_deleted_event_does_not_release_incident_lock_KNOWN_BUG(
    incident_service, fake_redis
):
    """
    Второе следствие того же бага: IncidentService не подписан на
    ResourceDeleted (эмитится StateCollector'ом, когда ресурс исчез из
    манифеста). Поэтому даже "удаление" ресурса не освобождает блокировку -
    только явный ResourceRecovered это делает.
    """
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    active_key = "nexus:incident:active:nexus:app"
    assert await fake_redis.get(active_key) == "1"

    # У IncidentService просто нет обработчика для ResourceDeleted -
    # соответственно, событие некому обработать, и лок не может быть снят.
    assert not hasattr(incident_service, "on_resource_deleted")
    assert await fake_redis.get(active_key) == "1"


# ---- вспомогательные методы ----


async def test_list_recent_incidents_returns_most_recent_first(incident_service):
    await incident_service.on_resource_failed(
        "ResourceStopped", failed_payload(resource="a")
    )
    await incident_service.on_resource_failed(
        "ResourceStopped", failed_payload(resource="b")
    )

    recent = await incident_service.list_recent_incidents(limit=10)

    assert [i.resource for i in recent] == ["b", "a"]


async def test_get_timeline_respects_limit_and_recency_order(incident_service):
    for i in range(5):
        await incident_service.add_to_timeline(f"event-{i}", "INFO")

    timeline = await incident_service.get_timeline(limit=2)

    assert len(timeline) == 2
    assert timeline[0]["text"] == "event-4"
    assert timeline[1]["text"] == "event-3"


async def test_on_resource_failed_ignored_under_maintenance(
    incident_service, fake_redis
):
    # Включаем режим обслуживания для агента "nexus"
    await fake_redis.set("nexus:maintenance:nexus", "1")

    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    # Инцидент не заносится в историю
    ids = await fake_redis.lrange("nexus:incidents:history", 0, -1)
    assert ids == []


async def test_on_resource_recovered_ignored_under_maintenance(
    incident_service, fake_redis
):
    # Сначала фиксируем инцидент без режима обслуживания
    await incident_service.on_resource_failed("ResourceStopped", failed_payload())

    # Включаем режим обслуживания
    await fake_redis.set("nexus:maintenance:nexus", "1")

    # Посылаем сигнал о восстановлении ресурса
    await incident_service.on_resource_recovered(
        "ResourceRecovered", {"agent": "nexus", "resource": "app"}
    )

    # Инцидент должен остаться в статусе "open" (событие восстановления проигнорировано)
    incident = await incident_service.get_incident("1")
    assert incident.status == "open"


def test_generate_fingerprint_filters_framework_frames():
    from services.incident import IncidentService

    # Имитируем traceback, где верхний фрейм (место падения) находится во фреймворке,
    # а реальный код вызова — в пользовательском handlers.py
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/app/tarot_bot/handlers.py", line 45, in cmd_start\n'
        '    await bot.send_message(chat_id, "foo")\n'
        '  File "/usr/local/lib/python3.11/site-packages/aiogram/dispatcher/dispatcher.py", line 123, in feed_update\n'
        "    await self._feed_update(bot, update)\n"
        "ValueError: Mock connection issue"
    )

    fp = IncidentService.generate_fingerprint("tarot_bot", "ValueError", tb)
    assert len(fp) == 16

    # Убеждаемся, что изменение в файле пользователя порождает новый фингерпринт
    tb2 = tb.replace("handlers.py", "handlers_new.py")
    fp2 = IncidentService.generate_fingerprint("tarot_bot", "ValueError", tb2)
    assert fp != fp2


async def test_on_app_error_updates_error_registry_counts(incident_service, fake_redis):
    payload = {
        "project": "tarot_bot",
        "exception_type": "KeyError",
        "message": "missing key 'foo'",
        "traceback": 'File "/app/tarot_bot/bot.py", line 10, in main\n  x = d["foo"]',
    }

    # Первая регистрация ошибки
    await incident_service.on_app_error("app:error", payload)

    fp = incident_service.generate_fingerprint(
        "tarot_bot", "KeyError", payload["traceback"]
    )
    err_key = f"nexus:errors:{fp}"

    # Заменяем строгий 'is True' на проверку истинности возвращаемого значения
    assert await fake_redis.sismember("nexus:errors:all", fp)

    err_data = await fake_redis.hgetall(err_key)
    assert err_data["count"] == "1"
    assert err_data["last_message"] == "missing key 'foo'"

    # Имитируем прохождение времени и закрытие инцидента, чтобы открыть следующий
    await fake_redis.delete("nexus:incident:active:tarot_bot:app")

    # Повторная регистрация аналогичной ошибки
    await incident_service.on_app_error("app:error", payload)

    err_data2 = await fake_redis.hgetall(err_key)
    assert err_data2["count"] == "2"  # Счетчик инкрементировался
