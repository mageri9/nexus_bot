"""
services/onboarding.py

Бизнес-логика визарда подключения нового проекта. Ничего telegram-специфичного
здесь нет — хендлеры в telegram/handlers_onboarding.py дергают эти функции
и сами рисуют клавиатуры/состояния FSM.
"""
from typing import List, Dict, Any
from loguru import logger

from agents import store as agent_store, local_transport, registry
from core.agent import ProjectAgent
from core.resource_factory import build_resource
from services import log_collector, redis_client  # уже существующий инстанс из services/__init__.py


async def discover_containers(project_name_hint: str) -> List[str]:
    """
    Возвращает список имён docker-контейнеров, потенциально относящихся к проекту.
    Мягкая эвристика (вхождение hint в имя) — финальный выбор всегда за юзером
    в inline-мультиселекте, это просто сортировка кандидатов наверх списка.
    """
    raw = await local_transport.run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    all_names = [n.strip() for n in raw.strip().split("\n") if n.strip()]

    hint = project_name_hint.lower().replace("-", "_")
    matched = [n for n in all_names if hint in n.lower().replace("-", "_")]
    others = [n for n in all_names if n not in matched]
    # Кандидаты по эвристике — первыми, остальные — ниже, на случай нестандартного нейминга
    return matched + others


async def classify_container(container_name: str) -> str:
    """
    Грубая классификация контейнера по имени образа: используется только чтобы
    предзаполнить дефолтный resource_key в превью визарда (postgres/redis узнаются
    сразу), юзер может поправить перед сохранением.
    """
    try:
        image = await local_transport.run(
            ["docker", "inspect", "-f", "{{.Config.Image}}", container_name]
        )
        image = image.strip().lower()
    except Exception as e:
        logger.warning(f"Onboarding: failed to inspect image for {container_name}: {e}")
        return "app"

    if "postgres" in image:
        return "postgres"
    if "redis" in image:
        return "redis"
    return "app"


async def get_container_mount_path(container_name: str) -> str | None:
    """Пытается вытащить первый bind-mount контейнера как дефолтный disk path."""
    try:
        raw = await local_transport.run(
            ["docker", "inspect", "-f", "{{range .Mounts}}{{.Source}}\n{{end}}", container_name]
        )
        paths = [p.strip() for p in raw.strip().split("\n") if p.strip()]
        return paths[0] if paths else None
    except Exception as e:
        logger.warning(f"Onboarding: failed to inspect mounts for {container_name}: {e}")
        return None


async def build_resource_rows(
    project_name: str,
    selected_containers: List[str],
    disk_path: str | None,
    enable_heartbeat: bool,
) -> List[Dict[str, Any]]:
    """
    Собирает список ресурсов (в формате agent_store, resource_key/resource_type/config)
    из выбранных пользователем контейнеров + опциональных disk/heartbeat.
    resource_key для контейнеров — либо распознанный тип (postgres/redis), либо
    "app"/"app_2" при коллизии имён, чтобы не перетереть друг друга.
    """
    rows: List[Dict[str, Any]] = []
    used_keys: set[str] = set()

    for container_name in selected_containers:
        key = await classify_container(container_name)
        final_key = key
        suffix = 2
        while final_key in used_keys:
            final_key = f"{key}_{suffix}"
            suffix += 1
        used_keys.add(final_key)

        rows.append(
            {
                "resource_key": final_key,
                "resource_type": "docker_container",
                "config": {"container_name": container_name},
            }
        )

    if disk_path:
        rows.append(
            {
                "resource_key": "data_disk",
                "resource_type": "project_storage",
                "config": {"path": disk_path},
            }
        )

    if enable_heartbeat:
        rows.append(
            {
                "resource_key": "heartbeat",
                "resource_type": "heartbeat",
                "config": {"project": project_name, "max_gap_seconds": 30},
            }
        )

    return rows


async def commit_new_project(project_name: str, resource_rows: List[Dict[str, Any]]) -> ProjectAgent:
    """
    Финальный шаг: пишет в БД, собирает живой ProjectAgent, регистрирует его
    в общем registry и подключает LogCollector — всё без рестарта Nexus.
    """
    if await agent_store.agent_exists(project_name):
        raise ValueError(f"Проект '{project_name}' уже зарегистрирован в Nexus.")

    await agent_store.save_agent(project_name, resource_rows)

    resources = {
        row["resource_key"]: build_resource(
            row["resource_type"], row["resource_key"], row["config"], local_transport, redis_client
        )
        for row in resource_rows
    }
    agent = ProjectAgent(name=project_name, resources=resources)

    registry.register(agent)
    log_collector.add_agent(agent)

    logger.info(f"Onboarding: project '{project_name}' registered live with resources: {list(resources.keys())}")
    return agent
