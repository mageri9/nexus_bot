"""
services/diagnostics.py

Автодиагностика ресурсов при аномалиях. Пока поддерживает только postgres-
контейнеры: по CPU-аномалии снимает pg_stat_activity (что выполнялось в
момент снимка) и, если установлено расширение pg_stat_statements, топ
запросов по числу вызовов — чтобы не гоняться за скачком руками через
docker exec + psql каждый раз.

Всё асимметрично отказоустойчиво: любая неудача (нет прав, не postgres,
расширение не установлено) возвращает None или частичный результат,
а не бросает исключение — вызывающий код (notifier) должен просто
отправить алерт без блока диагностики, а не упасть.
"""
from typing import Optional, Dict
from loguru import logger
from agents import local_transport


async def _get_postgres_env(container_name: str) -> Optional[Dict[str, str]]:
    try:
        raw = await local_transport.run(["docker", "exec", container_name, "env"])
    except Exception as e:
        logger.warning(f"Diagnostics: failed to read env for {container_name}: {e}")
        return None

    env: Dict[str, str] = {}
    for line in raw.split("\n"):
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value

    user = env.get("POSTGRES_USER")
    db = env.get("POSTGRES_DB")
    if not user or not db:
        return None
    return {"user": user, "db": db}


async def _run_psql(container_name: str, user: str, db: str, query: str) -> Optional[str]:
    try:
        # -t убирает шапку колонок, -A убирает выравнивание — компактнее для телеграма
        return await local_transport.run(
            ["docker", "exec", container_name, "psql", "-U", user, "-d", db, "-t", "-A", "-c", query]
        )
    except Exception as e:
        logger.warning(f"Diagnostics: psql query failed on {container_name}: {e}")
        return None


async def get_postgres_snapshot(container_name: str) -> Optional[str]:
    """
    Компактный текстовый снимок состояния postgres в момент вызова.
    Возвращает None, если не удалось снять ничего полезного вообще
    (нет доступа/креды не нашлись) — тогда notifier просто не покажет блок.
    """
    creds = await _get_postgres_env(container_name)
    if not creds:
        return None

    blocks = []

    active = await _run_psql(
        container_name, creds["user"], creds["db"],
        "SELECT state || ' | ' || (now()-query_start)::text || ' | ' || left(query,70) "
        "FROM pg_stat_activity WHERE state != 'idle' AND pid != pg_backend_pid() "
        "ORDER BY query_start ASC LIMIT 5;",
    )
    if active and active.strip():
        blocks.append("Активные запросы (state | duration | query):\n" + active.strip())
    else:
        blocks.append("Активных запросов в момент снимка нет — скачок короче интервала опроса.")

    top_calls = await _run_psql(
        container_name, creds["user"], creds["db"],
        "SELECT calls || ' calls, avg ' || round(mean_exec_time::numeric,1) || 'ms | ' || left(query,70) "
        "FROM pg_stat_statements ORDER BY calls DESC LIMIT 5;",
    )
    if top_calls and top_calls.strip():
        blocks.append("Топ запросов по числу вызовов (с момента сброса статистики):\n" + top_calls.strip())
    # Если pg_stat_statements не установлено — psql просто вернёт ошибку в stderr,
    # run() бросит RuntimeError, мы его ловим внутри _run_psql и получаем None здесь.
    # Молча пропускаем блок, а не пугаем алертом про отсутствующее расширение —
    # это отдельная задача по настройке, не относится к конкретному инциденту.

    return "\n\n".join(blocks) if blocks else None