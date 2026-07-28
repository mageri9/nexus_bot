"""
agents/__init__.py

Раньше: registry собирался статичным импортом объектов из устаревшего манифеста при старте
процесса — добавление проекта требовало правки файла и рестарта Nexus.

Теперь: registry строится динамически из agents/store.py (SQLite). Если БД пустая
(первый запуск после миграции), она заполняется начальными данными из
agents/seed_from_manifest.py. Новые проекты добавляются через services/onboarding.py
в рантайме, без рестарта (registry.register() работает "живьём" — collector
берёт список агентов на каждом тике, не кеширует).

ВАЖНО: это async-инициализация, поэтому build_registry() нужно await'нуть
в точке входа приложения (main.py) до старта StateCollector/LogCollector,
а не просто импортировать registry как раньше.
"""
from core import AgentRegistry
from core.resource_factory import build_resource
from transports import LocalShellTransport
from agents import store as agent_store

local_transport = LocalShellTransport()

registry = AgentRegistry()


async def build_registry() -> AgentRegistry:
    """Наполняет глобальный registry агентами из БД. Идемпотентно на пустом registry."""
    if await agent_store.is_empty():
        await _seed_from_manifest()

    # The composition root owns the Redis dependency for heartbeat resources.
    from services import redis_client

    agents_data = await agent_store.load_all()
    for agent_name, resource_rows in agents_data.items():
        resources = {}
        for row in resource_rows:
            resources[row["resource_key"]] = build_resource(
                row["resource_type"], row["resource_key"], row["config"], local_transport, redis_client
            )
        from core.agent import ProjectAgent

        registry.register(ProjectAgent(name=agent_name, resources=resources))

    return registry


async def _seed_from_manifest() -> None:
    """Одноразовая загрузка seed-данных в БД. Срабатывает только если БД пустая."""
    from agents.seed_from_manifest import SEED_AGENTS  # см. отдельный файл ниже

    for agent_name, resources in SEED_AGENTS.items():
        await agent_store.save_agent(agent_name, resources)
