import pytest
from services.event_bus import EventBus


async def test_publish_with_no_subscribers_does_not_raise(event_bus):
    # Никто не подписан на событие — publish должен просто тихо завершиться
    await event_bus.publish("nobody:listening", {"foo": "bar"})


async def test_subscriber_receives_event_type_and_payload(event_bus, recording_subscriber):
    event_bus.subscribe("ResourceStopped", recording_subscriber)

    payload = {"agent": "nexus", "resource": "app"}
    await event_bus.publish("ResourceStopped", payload)

    assert recording_subscriber.received == [("ResourceStopped", payload)]


async def test_multiple_subscribers_all_get_notified(event_bus):
    calls = []

    async def sub_a(event_type, data):
        calls.append(("a", event_type))

    async def sub_b(event_type, data):
        calls.append(("b", event_type))

    event_bus.subscribe("incident:opened", sub_a)
    event_bus.subscribe("incident:opened", sub_b)

    await event_bus.publish("incident:opened", {})

    assert ("a", "incident:opened") in calls
    assert ("b", "incident:opened") in calls
    assert len(calls) == 2


async def test_subscribers_are_isolated_per_event_type(event_bus, recording_subscriber):
    event_bus.subscribe("EventA", recording_subscriber)

    await event_bus.publish("EventB", {"x": 1})

    assert recording_subscriber.received == []


async def test_one_subscriber_raising_does_not_prevent_others_from_running(event_bus):
    """
    Критично для надежности EventBus: publish() использует asyncio.gather с
    return_exceptions=True, поэтому падение одного подписчика не должно "съесть"
    вызов остальных (например, IncidentService не должен пострадать, если упал
    TelegramNotifier).
    """
    order = []

    async def broken_subscriber(event_type, data):
        order.append("broken")
        raise ValueError("simulated failure")

    async def healthy_subscriber(event_type, data):
        order.append("healthy")

    event_bus.subscribe("ResourceUnhealthy", broken_subscriber)
    event_bus.subscribe("ResourceUnhealthy", healthy_subscriber)

    # publish не должен пробрасывать исключение наружу
    await event_bus.publish("ResourceUnhealthy", {})

    assert "broken" in order
    assert "healthy" in order


async def test_publish_awaits_all_subscribers_before_returning(event_bus):
    import asyncio

    finished = []

    async def slow_subscriber(event_type, data):
        await asyncio.sleep(0.01)
        finished.append("slow")

    event_bus.subscribe("SlowEvent", slow_subscriber)
    await event_bus.publish("SlowEvent", {})

    assert finished == ["slow"]
