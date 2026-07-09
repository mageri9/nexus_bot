import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from redis.asyncio import Redis
from loguru import logger

from services.query import QueryService


class Incident(BaseModel):
    id: str  # Уникальный порядковый номер инцидента
    project: str  # Имя агента (проекта)
    resource: str  # Имя ресурса
    severity: str  # HIGH, MEDIUM, LOW
    status: str  # open, resolved
    opened_at: datetime  # Время фиксации сбоя
    resolved_at: Optional[datetime] = None  # Время восстановления
    duration: Optional[float] = None  # Длительность простоя в секундах
    reason: str  # Описание причины / статуса перехода
    logs: Optional[str] = None  # Слепок логов на момент падения
    ai_report: Optional[str] = None  # ИИ-анализ (заполняется позже)
    restart_count: int = 0  # Количество перезапусков контейнера


class IncidentService:
    def __init__(self, redis_client: Redis, query_service: QueryService, event_bus):
        self.redis = redis_client
        self.query_service = query_service
        self.event_bus = event_bus

    async def add_to_timeline(self, text: str, severity: str = "INFO") -> None:
        """Добавляет событие в хронологическую ленту хоста (Datadog-lite)"""
        try:
            now = datetime.now(timezone.utc)
            payload = {"timestamp": now.isoformat(), "text": text, "severity": severity}
            score = now.timestamp()

            # Добавляем в Sorted Set в Redis
            await self.redis.zadd(
                "nexus:timeline", {json.dumps(payload, ensure_ascii=False): score}
            )

            # Ротируем ленту: оставляем только последние 50 записей для экономии памяти
            await self.redis.zremrangebyrank("nexus:timeline", 0, -51)

            logger.debug(f"Timeline: Added [{severity}] event: {text}")
        except Exception as e:
            logger.error(f"Failed to add event to timeline: {e}")

    async def get_timeline(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Возвращает N последних событий из хронологической ленты"""
        try:
            # Запрашиваем элементы в обратном порядке (от свежих к старым)
            raw_elements = await self.redis.zrevrange("nexus:timeline", 0, limit - 1)
            return [json.loads(el) for el in raw_elements]
        except Exception as e:
            logger.error(f"Failed to fetch timeline: {e}")
            return []

    async def on_devops_event(self, event_type: str, data: dict) -> None:
        """Обработчик успешных/упавших CI/CD пайплайнов для фиксации в ленте"""
        repo = data.get("repository", "unknown")
        workflow = data.get("workflow_name", "pipeline")
        author = data.get("author", "unknown")

        if event_type == "devops:workflow_success":
            text = f"🚀 CI/CD: Сборка {repo} ({workflow}) успешно завершена. Автор: @{author}"
            await self.add_to_timeline(text, "SUCCESS")
        elif event_type == "devops:workflow_failure":
            text = f"❌ CI/CD: Пайплайн {repo} ({workflow}) упал! Автор: @{author}"
            await self.add_to_timeline(text, "WARNING")

    async def on_resource_failed(self, event_type: str, data: dict) -> None:
        project = data["agent"]
        resource = data["resource"]
        new_status = data["new_status"]

        active_key = f"nexus:incident:active:{project}:{resource}"

        incident_num = await self.redis.incr("nexus:incident:counter")
        incident_id = f"{incident_num}"

        # Атомарная блокировка (SET NX с TTL 1 час)
        acquired = await self.redis.set(active_key, incident_id, nx=True)
        if not acquired:
            logger.debug(
                f"Incident for {project}:{resource} is already open. Skipping."
            )
            return

        logs = None
        try:
            logs = await self.query_service.get_resource_logs(
                project, resource, limit=30
            )
        except Exception as e:
            logs = f"Не удалось извлечь логи: {str(e)}"

        restart_count = 0
        try:
            agent_obj = self.query_service.registry.get(project)
            res_obj = agent_obj.resources.get(resource)
            if hasattr(res_obj, "container_name") and hasattr(res_obj, "transport"):
                raw_restarts = await res_obj.transport.run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.RestartCount}}",
                        res_obj.container_name,
                    ]
                )
                restart_count = int(raw_restarts.strip())
        except Exception as ex:
            logger.debug(
                f"Failed to fetch restart count via inspect for {project}:{resource}. Error: {ex}"
            )

        severity = "HIGH" if event_type == "ResourceStopped" else "MEDIUM"

        incident = Incident(
            id=incident_id,
            project=project,
            resource=resource,
            severity=severity,
            status="open",
            opened_at=datetime.now(timezone.utc),
            reason=f"Resource transitioned to state: {new_status}",
            logs=logs,
            restart_count=restart_count,
        )

        await self.redis.set(
            f"nexus:incident:detail:{incident_id}", incident.model_dump_json()
        )
        await self.redis.lpush("nexus:incidents:history", incident_id)

        # Пишем инцидент в ленту событий хоста
        await self.add_to_timeline(
            f"🚨 Сбой ресурса {project}:{resource} (рестарты: {restart_count})",
            severity=severity,
        )

        logger.info(
            f"🚨 IncidentService: Created Incident #{incident_id} for {project}:{resource}"
        )

        await self.event_bus.publish("incident:opened", incident.model_dump())

    async def on_resource_recovered(self, event_type: str, data: dict) -> None:
        """Обработчик события восстановления ресурса (ResourceRecovered)"""
        project = data["agent"]
        resource = data["resource"]

        active_key = f"nexus:incident:active:{project}:{resource}"
        incident_id = await self.redis.get(active_key)
        if not incident_id:
            logger.debug(
                f"IncidentService: No active incident found for {project}:{resource} to resolve."
            )
            return

        # Закрываем активный указатель в Redis
        await self.redis.delete(active_key)

        # Считываем детальные данные инцидента
        detail_key = f"nexus:incident:detail:{incident_id}"
        raw_incident = await self.redis.get(detail_key)
        if not raw_incident:
            logger.warning(
                f"IncidentService: Active record #{incident_id} exists but details were not found."
            )
            return

        incident = Incident.model_validate_json(raw_incident)

        # Расчет длительности простоя
        resolved_at = datetime.now(timezone.utc)
        duration_seconds = (resolved_at - incident.opened_at).total_seconds()

        incident.status = "resolved"
        incident.resolved_at = resolved_at
        incident.duration = round(duration_seconds, 2)

        # Сохраняем обновленный инцидент
        await self.redis.set(detail_key, incident.model_dump_json())

        # Пишем восстановление в ленту событий хоста
        duration_str = (
            f"{incident.duration:.1f}с"
            if incident.duration < 60
            else f"{int(incident.duration // 60)}м {int(incident.duration % 60)}с"
        )
        await self.add_to_timeline(
            f"✅ Восстановлен ресурс {project}:{resource} (простой: {duration_str})",
            severity="SUCCESS",
        )

        logger.info(
            f"✅ IncidentService: Incident #{incident_id} resolved. Outage duration: {incident.duration}s"
        )

        # Публикуем событие завершения инцидента
        await self.event_bus.publish("incident:resolved", incident.model_dump())

    async def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Возвращает детальную модель инцидента по его ID"""
        raw = await self.redis.get(f"nexus:incident:detail:{incident_id}")
        if raw:
            return Incident.model_validate_json(raw)
        return None

    async def list_recent_incidents(self, limit: int = 10) -> List[Incident]:
        """Возвращает список последних N инцидентов из истории"""
        ids = await self.redis.lrange("nexus:incidents:history", 0, limit - 1)
        incidents = []
        for inc_id in ids:
            inc = await self.get_incident(inc_id)
            if inc:
                incidents.append(inc)
        return incidents