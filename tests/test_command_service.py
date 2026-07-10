import pytest
from core import ProjectAgent
from infra.docker import DockerContainer
from infra.disk import HostDiskResource
from services.command import CommandService


@pytest.fixture
def agent_with_container(scripted_transport):
    container = DockerContainer("app", scripted_transport, "nexus-core")
    agent = ProjectAgent(name="nexus", resources={"app": container})
    return agent, container


async def test_restart_resource_success_publishes_started_then_success(
    registry, event_bus, scripted_transport, agent_with_container, recording_subscriber
):
    agent, container = agent_with_container
    registry.register(agent)
    scripted_transport.on(["docker", "restart", "nexus-core"], "abc123\n")

    event_bus.subscribe("action:started", recording_subscriber)
    event_bus.subscribe("action:success", recording_subscriber)

    svc = CommandService(registry, event_bus)
    result = await svc.restart_resource("nexus", "app")

    assert result == "abc123"
    assert recording_subscriber.types() == ["action:started", "action:success"]
    success_payload = recording_subscriber.received[1][1]
    assert success_payload["result"] == "abc123"


async def test_restart_resource_failure_publishes_started_then_failed(
    registry, event_bus, scripted_transport, agent_with_container, recording_subscriber
):
    agent, container = agent_with_container
    registry.register(agent)
    scripted_transport.fail(["docker", "restart", "nexus-core"], RuntimeError("daemon down"))

    event_bus.subscribe("action:started", recording_subscriber)
    event_bus.subscribe("action:failed", recording_subscriber)

    svc = CommandService(registry, event_bus)

    with pytest.raises(RuntimeError, match="daemon down"):
        await svc.restart_resource("nexus", "app")

    assert recording_subscriber.types() == ["action:started", "action:failed"]
    failed_payload = recording_subscriber.received[1][1]
    assert failed_payload["error"] == "daemon down"


async def test_restart_resource_unknown_resource_raises_key_error(registry, event_bus):
    registry.register(ProjectAgent(name="nexus", resources={}))
    svc = CommandService(registry, event_bus)

    with pytest.raises(KeyError):
        await svc.restart_resource("nexus", "nonexistent")


async def test_restart_resource_unsupported_capability_raises_type_error(
    registry, event_bus, scripted_transport
):
    # HostDiskResource не поддерживает restart()
    disk = HostDiskResource("root_disk", scripted_transport, "/host_root")
    registry.register(ProjectAgent(name="nexus", resources={"root_disk": disk}))
    svc = CommandService(registry, event_bus)

    with pytest.raises(TypeError):
        await svc.restart_resource("nexus", "root_disk")


async def test_restart_resource_unknown_agent_raises_key_error(registry, event_bus):
    svc = CommandService(registry, event_bus)

    with pytest.raises(KeyError):
        await svc.restart_resource("does_not_exist", "app")
