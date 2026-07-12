import json
import pytest
from core import ProjectAgent
from infra.docker import DockerContainer
from services.query import QueryService
from services.health_engine import HealthEngine


@pytest.fixture
def query_service(registry, fake_redis):
    return QueryService(
        registry=registry, redis_client=fake_redis, health_engine=HealthEngine()
    )


async def test_get_system_status_no_cached_data_reports_placeholder(
    registry, query_service
):
    registry.register(ProjectAgent(name="nexus", resources={}))

    status = await query_service.get_system_status()

    assert "error" in status["nexus"]


async def test_get_system_status_v2_flattens_containers_and_storage(
    registry, query_service, fake_redis
):
    registry.register(ProjectAgent(name="nexus", resources={}))
    state_v2 = {
        "version": 2,
        "containers": {"app": {"status": "running"}},
        "storage": {"root_disk": {"status": "healthy"}},
    }
    await fake_redis.set("nexus:state:nexus", json.dumps(state_v2))

    status = await query_service.get_system_status()

    assert status["nexus"] == {"app": "running", "root_disk": "healthy"}


async def test_get_system_status_v1_legacy_format_still_supported(
    registry, query_service, fake_redis
):
    registry.register(ProjectAgent(name="nexus", resources={}))
    legacy_state = {"app": {"status": "running"}, "redis": {"status": "healthy"}}
    await fake_redis.set("nexus:state:nexus", json.dumps(legacy_state))

    status = await query_service.get_system_status()

    assert status["nexus"] == {"app": "running", "redis": "healthy"}


async def test_get_agent_details_returns_error_dict_when_missing(query_service):
    details = await query_service.get_agent_details("ghost")
    assert "error" in details


async def test_get_agent_details_returns_raw_cached_json(query_service, fake_redis):
    state_v2 = {"version": 2, "containers": {}, "storage": {}}
    await fake_redis.set("nexus:state:nexus", json.dumps(state_v2))

    details = await query_service.get_agent_details("nexus")

    assert details == state_v2


async def test_get_resource_logs_unknown_resource_raises_key_error(
    registry, query_service
):
    registry.register(ProjectAgent(name="nexus", resources={}))

    with pytest.raises(KeyError):
        await query_service.get_resource_logs("nexus", "missing")


async def test_get_resource_logs_unsupported_capability_raises_type_error(
    registry, query_service, scripted_transport
):
    from infra.disk import HostDiskResource

    disk = HostDiskResource("root_disk", scripted_transport, "/host_root")
    registry.register(ProjectAgent(name="nexus", resources={"root_disk": disk}))

    with pytest.raises(TypeError):
        await query_service.get_resource_logs("nexus", "root_disk")


async def test_get_resource_logs_delegates_to_resource(
    registry, query_service, scripted_transport
):
    container = DockerContainer("app", scripted_transport, "nexus-core")
    scripted_transport.on(
        ["docker", "logs", "--tail", "50", "nexus-core"], "hello logs"
    )
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    logs = await query_service.get_resource_logs("nexus", "app")

    assert logs == "hello logs"


def test_calculate_health_score_delegates_to_health_engine(query_service):
    score = query_service.calculate_health_score(
        "nexus", {"version": 2, "containers": {}, "storage": {}}
    )
    assert score == 100


async def test_get_resource_logs_from_redis_buffer_first(
    registry, query_service, fake_redis, scripted_transport
):
    # Предварительно наполняем буфер в Redis
    await fake_redis.rpush("nexus:logs:nexus:app", "buffered log 1", "buffered log 2")

    container = DockerContainer("app", scripted_transport, "nexus-core")
    registry.register(ProjectAgent(name="nexus", resources={"app": container}))

    # Запрашиваем логи через QueryService
    logs = await query_service.get_resource_logs("nexus", "app")

    # Должны получить данные из кэша без задействования транспорта
    assert logs == "buffered log 1\nbuffered log 2"
    assert len(scripted_transport.calls) == 0  # Скрипт транспорта не вызывался вовсе


async def test_get_agent_health_history_retrieves_sorted_set(query_service, fake_redis):
    key = "nexus:health:history:nexus"

    # Имитируем запись трех хронологических замеров здоровья
    await fake_redis.zadd(
        key,
        {
            json.dumps(
                {"score": 80, "timestamp": "2026-07-12T05:00:00Z"}
            ): 1718100000.0,
            json.dumps(
                {"score": 90, "timestamp": "2026-07-12T05:01:00Z"}
            ): 1718100060.0,
            json.dumps(
                {"score": 95, "timestamp": "2026-07-12T05:02:00Z"}
            ): 1718100120.0,
        },
    )

    history = await query_service.get_agent_health_history("nexus", limit=10)
    assert len(history) == 3
    assert history[0]["score"] == 80
    assert history[1]["score"] == 90
    assert history[2]["score"] == 95


async def test_get_health_trend_directions(query_service, fake_redis):
    key = "nexus:health:history:nexus"

    # 1. Сценарий: Улучшение здоровья (тренд вверх)
    await fake_redis.zadd(
        key,
        {
            json.dumps({"score": 85, "timestamp": "2026-07-12T05:00:00Z"}): 1.0,
            json.dumps({"score": 95, "timestamp": "2026-07-12T05:01:00Z"}): 2.0,
        },
    )
    assert await query_service.get_health_trend("nexus") == " 📈"

    # 2. Сценарий: Деградация здоровья (тренд вниз)
    await fake_redis.delete(key)
    await fake_redis.zadd(
        key,
        {
            json.dumps({"score": 95, "timestamp": "2026-07-12T05:00:00Z"}): 1.0,
            json.dumps({"score": 85, "timestamp": "2026-07-12T05:01:00Z"}): 2.0,
        },
    )
    assert await query_service.get_health_trend("nexus") == " 📉"

    # 3. Сценарий: Стабильность (изменений нет)
    await fake_redis.delete(key)
    await fake_redis.zadd(
        key,
        {
            json.dumps({"score": 90, "timestamp": "2026-07-12T05:00:00Z"}): 1.0,
            json.dumps({"score": 90, "timestamp": "2026-07-12T05:01:00Z"}): 2.0,
        },
    )
    assert await query_service.get_health_trend("nexus") == " ➡️"
