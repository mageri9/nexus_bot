from infra.disk import HostDiskResource, ProjectStorageResource


# ---- HostDiskResource ----

async def test_host_disk_status_healthy_when_path_exists(scripted_transport):
    scripted_transport.on(["test", "-d", "/host_root"], "")
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")

    assert await r.get_status() == "healthy"


async def test_host_disk_status_unhealthy_when_path_missing(scripted_transport):
    scripted_transport.fail(["test", "-d", "/host_root"], RuntimeError("not found"))
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")

    assert await r.get_status() == "unhealthy"


async def test_host_disk_has_no_capabilities(scripted_transport):
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")
    assert r.capabilities == []


async def test_host_disk_get_metrics_parses_df_output(scripted_transport):
    df_output = (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/sda1        50G   30G   18G  63% /host_root"
    )
    scripted_transport.on(["df", "-h", "/host_root"], df_output)
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")

    metrics = await r.get_metrics()

    part = metrics["partition_size"]["value"]
    assert part == {"size": "50G", "used": "30G", "avail": "18G", "use_percent": "63%"}


async def test_host_disk_get_metrics_returns_error_on_unparseable_output(scripted_transport):
    scripted_transport.on(["df", "-h", "/host_root"], "unexpected single line")
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")

    metrics = await r.get_metrics()

    assert "error" in metrics


async def test_host_disk_get_metrics_returns_error_on_transport_failure(scripted_transport):
    scripted_transport.fail(["df", "-h", "/host_root"], RuntimeError("permission denied"))
    r = HostDiskResource("root_disk", scripted_transport, "/host_root")

    metrics = await r.get_metrics()

    assert "error" in metrics


# ---- ProjectStorageResource ----

async def test_project_storage_status_healthy(scripted_transport):
    scripted_transport.on(["test", "-d", "/data/tarot"], "")
    r = ProjectStorageResource("data_disk", scripted_transport, "/data/tarot")

    assert await r.get_status() == "healthy"


async def test_project_storage_has_backup_capability(scripted_transport):
    r = ProjectStorageResource("data_disk", scripted_transport, "/data/tarot")
    assert r.capabilities == ["backup_db"]


async def test_project_storage_get_metrics_converts_kb_to_bytes(scripted_transport):
    scripted_transport.on(["du", "-s", "/data/tarot"], "2048\t/data/tarot")
    r = ProjectStorageResource("data_disk", scripted_transport, "/data/tarot")

    metrics = await r.get_metrics()

    assert metrics["directory_size"]["value"] == 2048 * 1024
    assert metrics["directory_size"]["unit"] == "bytes"


async def test_project_storage_get_metrics_error_on_empty_output(scripted_transport):
    scripted_transport.on(["du", "-s", "/data/tarot"], "")
    r = ProjectStorageResource("data_disk", scripted_transport, "/data/tarot")

    metrics = await r.get_metrics()

    assert "error" in metrics
