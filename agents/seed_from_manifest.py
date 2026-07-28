"""
agents/seed_from_manifest.py

Статичный снимок прежнего манифеста в виде данных (не объектов),
для одноразовой миграции в SQLite при первом запуске. После первого успешного
build_registry() эта миграция больше не выполняется — источник правды дальше
только agent_store.

Если нужно добавить или поправить мигрированные данные вручную "в БД
напрямую" — используй services.onboarding напрямую или SQL, а не правь этот файл:
он существует только как исторический seed и не перечитывается повторно.
"""

SEED_AGENTS = {
    "imagebot": [
        {"resource_key": "app", "resource_type": "docker_container", "config": {"container_name": "m9_imagebot"}},
        {"resource_key": "data_disk", "resource_type": "project_storage", "config": {"path": "/host_root/home/mageri9/apps/m9_imagebot/data"}},
        {"resource_key": "heartbeat", "resource_type": "heartbeat", "config": {"project": "imagebot", "max_gap_seconds": 30}},
    ],
    "tarot_bot": [
        {"resource_key": "bot", "resource_type": "docker_container", "config": {"container_name": "tarot_bot"}},
        {"resource_key": "postgres", "resource_type": "docker_container", "config": {"container_name": "tarot_bot_postgres"}},
        {"resource_key": "data_disk", "resource_type": "project_storage", "config": {"path": "/host_root/home/mageri9/apps/tarot_bot"}},
        {"resource_key": "heartbeat", "resource_type": "heartbeat", "config": {"project": "tarot_bot", "max_gap_seconds": 30}},
    ],
    "chronicle": [
        {"resource_key": "bot", "resource_type": "docker_container", "config": {"container_name": "chronicle_bot"}},
        {"resource_key": "worker", "resource_type": "docker_container", "config": {"container_name": "chronicle_worker"}},
        {"resource_key": "redis", "resource_type": "docker_container", "config": {"container_name": "chronicle_redis"}},
        {"resource_key": "data_disk", "resource_type": "project_storage", "config": {"path": "/host_root/home/mageri9/apps/commit_chronicle/data"}},
        {"resource_key": "heartbeat", "resource_type": "heartbeat", "config": {"project": "chronicle", "max_gap_seconds": 30}},
    ],
    "nexus": [
        {"resource_key": "app", "resource_type": "docker_container", "config": {"container_name": "nexus-core"}},
        {"resource_key": "webhook", "resource_type": "docker_container", "config": {"container_name": "nexus-webhook"}},
        {"resource_key": "redis", "resource_type": "docker_container", "config": {"container_name": "nexus-redis"}},
        {"resource_key": "root_disk", "resource_type": "host_disk", "config": {"path": "/host_root"}},
    ],
    "binance_quant_bot": [
        {"resource_key": "bot", "resource_type": "docker_container", "config": {"container_name": "binance_quant_bot"}},
        {"resource_key": "redis", "resource_type": "docker_container", "config": {"container_name": "bot-redis"}},
        {"resource_key": "data_disk", "resource_type": "project_storage", "config": {"path": "/host_root/home/mageri9/apps/binance_quant_bot/src/db"}},
        {"resource_key": "heartbeat", "resource_type": "heartbeat", "config": {"project": "binance_quant_bot", "max_gap_seconds": 30}},
    ],
}
