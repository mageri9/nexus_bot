from datetime import datetime, timedelta, timezone

import pytest
from infra.docker import DockerContainer


def make_container(transport, name="app", container_name="nexus-core"):
    return DockerContainer(name, transport, container_name)


async def test_get_status_returns_trimmed_state(scripted_transport):
    scripted_transport.on(
        ["docker", "inspect", "-f", "{{.State.Status}}", "nexus-core"], "running\n"
    )
    c = make_container(scripted_transport)

    assert await c.get_status() == "running"


async def test_get_status_returns_unknown_on_transport_error(scripted_transport):
    scripted_transport.fail(
        ["docker", "inspect", "-f", "{{.State.Status}}", "nexus-core"],
        RuntimeError("no such container"),
    )
    c = make_container(scripted_transport)

    assert await c.get_status() == "unknown"


async def test_capabilities_include_restart_and_logs(scripted_transport):
    c = make_container(scripted_transport)
    assert c.capabilities == ["restart", "logs"]


async def test_get_metrics_happy_path_parses_all_fields(scripted_transport):
    started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%S.000000000Z"
    )

    scripted_transport.on(
        [
            "docker", "stats", "nexus-core", "--no-stream", "--format",
            "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
        ],
        "12.34%|56.78%|100MiB / 512MiB",
    )
    scripted_transport.on(
        [
            "docker", "inspect", "-f", "{{.RestartCount}}|{{.State.StartedAt}}",
            "nexus-core",
        ],
        f"3|{started_at}",
    )

    c = make_container(scripted_transport)
    metrics = await c.get_metrics()

    assert metrics["cpu"]["value"] == "12.34%"
    assert metrics["mem_perc"]["value"] == "56.78%"
    assert metrics["memory"]["value"] == "100MiB / 512MiB"
    assert metrics["restarts"]["value"] == 3
    # Аптайм должен быть примерно 2 часа (7200с), с запасом на выполнение теста
    assert 7100 <= metrics["uptime_seconds"]["value"] <= 7300


async def test_get_metrics_handles_malformed_inspect_output_gracefully(scripted_transport):
    scripted_transport.on(
        [
            "docker", "stats", "nexus-core", "--no-stream", "--format",
            "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
        ],
        "1.00%|2.00%|10MiB / 20MiB",
    )
    # Испорченный inspect output - не сможет распарсить RestartCount/StartedAt
    scripted_transport.on(
        [
            "docker", "inspect", "-f", "{{.RestartCount}}|{{.State.StartedAt}}",
            "nexus-core",
        ],
        "garbage-without-separator",
    )

    c = make_container(scripted_transport)
    metrics = await c.get_metrics()

    # Не должно падать - просто нули по умолчанию
    assert metrics["restarts"]["value"] == 0
    assert metrics["uptime_seconds"]["value"] == 0
    # При этом cpu/mem всё равно должны распарситься нормально
    assert metrics["cpu"]["value"] == "1.00%"


async def test_get_metrics_returns_error_key_on_total_transport_failure(scripted_transport):
    scripted_transport.fail_prefix(["docker", "stats"], RuntimeError("daemon unreachable"))
    c = make_container(scripted_transport)

    metrics = await c.get_metrics()

    assert "error" in metrics


async def test_restart_calls_docker_restart_with_container_name(scripted_transport):
    scripted_transport.on(["docker", "restart", "nexus-core"], "nexus-core\n")
    c = make_container(scripted_transport)

    result = await c.restart()

    assert result == "nexus-core"
    assert scripted_transport.calls[-1] == ["docker", "restart", "nexus-core"]


async def test_get_logs_uses_requested_limit(scripted_transport):
    scripted_transport.on(
        ["docker", "logs", "--tail", "25", "nexus-core"], "line1\nline2"
    )
    c = make_container(scripted_transport)

    logs = await c.get_logs(limit=25)

    assert logs == "line1\nline2"
