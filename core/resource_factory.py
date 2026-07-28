"""
core/resource_factory.py

Единая точка сборки Resource-объектов из (resource_type, config) — используется
и при загрузке манифеста из БД на старте, и визардом онбординга в рантайме.

Явный dict вместо динамического импорта по строке класса — чтобы набор
допустимых типов был виден целиком в одном месте и не расширялся магией.
"""
from typing import Any, Dict, Callable
from core.resource import Resource
from transports.base import Transport
from infra import DockerContainer, HostDiskResource, ProjectStorageResource, ApplicationHeartbeat


def _build_docker_container(key: str, cfg: Dict[str, Any], transport: Transport) -> Resource:
    return DockerContainer(key, transport, cfg["container_name"])


def _build_project_storage(key: str, cfg: Dict[str, Any], transport: Transport) -> Resource:
    return ProjectStorageResource(key, transport, cfg["path"])


def _build_host_disk(key: str, cfg: Dict[str, Any], transport: Transport) -> Resource:
    return HostDiskResource(key, transport, cfg["path"])


def _build_heartbeat(key: str, cfg: Dict[str, Any], transport: Transport) -> Resource:
    # ApplicationHeartbeat не принимает transport в seed-данных (см. agents/seed_from_manifest.py) —
    # transport здесь игнорируется, оставлен в сигнатуре только чтобы фабрика была единообразной.
    return ApplicationHeartbeat(key, cfg["project"], max_gap_seconds=cfg.get("max_gap_seconds", 30))


RESOURCE_FACTORY: Dict[str, Callable[[str, Dict[str, Any], Transport], Resource]] = {
    "docker_container": _build_docker_container,
    "project_storage": _build_project_storage,
    "host_disk": _build_host_disk,
    "heartbeat": _build_heartbeat,
}


def build_resource(resource_type: str, key: str, config: Dict[str, Any], transport: Transport) -> Resource:
    factory = RESOURCE_FACTORY.get(resource_type)
    if factory is None:
        raise ValueError(
            f"Unknown resource_type '{resource_type}' for resource '{key}'. "
            f"Known types: {list(RESOURCE_FACTORY.keys())}"
        )
    return factory(key, config, transport)
