import pytest
from datetime import datetime, timedelta, timezone
from infra.heartbeat import ApplicationHeartbeat
from core.resource_factory import build_resource
from transports import LocalShellTransport


async def test_heartbeat_resource_unhealthy_by_default(fake_redis):
    # Если пульс от бота никогда не поступал
    r = ApplicationHeartbeat("heartbeat", "tarot_bot", max_gap_seconds=10)

    import services

    services.redis_client = fake_redis

    assert await r.get_status() == "unhealthy"
    metrics = await r.get_metrics()
    assert metrics["heartbeat_gap"]["value"] == 999999.0


async def test_heartbeat_resource_healthy_when_timestamp_fresh(fake_redis):
    r = ApplicationHeartbeat("heartbeat", "tarot_bot", max_gap_seconds=10)

    import services

    services.redis_client = fake_redis

    # Пишем свежий пульс
    now = datetime.now(timezone.utc)
    await fake_redis.set("nexus:heartbeat:tarot_bot", now.isoformat())

    assert await r.get_status() == "healthy"
    metrics = await r.get_metrics()
    assert metrics["heartbeat_gap"]["value"] < 2.0  # Разница во времени минимальна


async def test_heartbeat_resource_unhealthy_when_timestamp_stale(fake_redis):
    r = ApplicationHeartbeat("heartbeat", "tarot_bot", max_gap_seconds=10)

    import services

    services.redis_client = fake_redis

    # Пишем устаревший пульс (15 секунд назад при лимите 10)
    stale = datetime.now(timezone.utc) - timedelta(seconds=15)
    await fake_redis.set("nexus:heartbeat:tarot_bot", stale.isoformat())

    assert await r.get_status() == "unhealthy"
    metrics = await r.get_metrics()
    assert 14.0 <= metrics["heartbeat_gap"]["value"] <= 16.0


async def test_heartbeat_factory_injects_redis_client(fake_redis):
    heartbeat = build_resource(
        "heartbeat",
        "heartbeat",
        {"project": "tarot_bot"},
        LocalShellTransport(),
        fake_redis,
    )

    assert heartbeat.redis_client is fake_redis
