import json
import re
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from redis.asyncio import Redis
from loguru import logger

from config.settings import settings
from services.query import QueryService
from core.signal import Signal


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
    fingerprint: Optional[str] = None # Уникальный фингерпринт ошибки для реестра дедупликации


class IncidentService:

    def __init__(self, redis_client: Redis, query_service: QueryService, event_bus, classifier=None):
        self.redis = redis_client
        self.query_service = query_service
        self.event_bus = event_bus
        # Лениво импортируем для полной совместимости со старыми вызовами в фикстурах тестов
        from services.classifier import Classifier
        self.classifier = classifier or Classifier(redis_client)

    @staticmethod
    def generate_fingerprint(
        project: str, exception_type: str, traceback_str: str
    ) -> str:
        """Анализирует стек traceback снизу вверх, возвращая 16-значный хэш первого non-framework фрейма"""
        # Поиск фреймов вида: File "filepath", line line_num, in func_name
        frames = re.findall(r'File "([^"]+)", line (\d+), in (\w+)', traceback_str)

        user_frame = "unknown_frame"
        # Перебираем стек ошибок в обратном порядке (снизу вверх к источнику вызова)
        for filepath, line_num, func_name in reversed(frames):
            # Пропускаем библиотеки Python, виртуальные окружения и фреймворки
            if any(
                p in filepath
                for p in [
                    "site-packages",
                    "lib/python",
                    "aiogram",
                    "asyncio",
                    "importlib",
                ]
            ):
                continue
            user_frame = f"{filepath}:{line_num}:{func_name}"
            break

        # Если пользовательских файлов нет, берем нижний фрейм
        if user_frame == "unknown_frame" and frames:
            filepath, line_num, func_name = frames[-1]
            user_frame = f"{filepath}:{line_num}:{func_name}"

        raw_str = f"{project}:{exception_type}:{user_frame}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:16]

    async def is_maintenance(self, project: str) -> bool:
        """Проксирует проверку обслуживания в классификатор (сохранение совместимости)"""
        return await self.redis.exists(f"nexus:maintenance:{project}") > 0

    async def add_to_timeline(self, text: str, severity: str = "INFO") -> None:
        """Добавляет событие в хронологическую ленту хоста"""
        try:
            now = datetime.now(timezone.utc)
            payload = {"timestamp": now.isoformat(), "text": text, "severity": severity}
            score = now.timestamp()

            await self.redis.zadd(
                "nexus:timeline", {json.dumps(payload, ensure_ascii=False): score}
            )
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
        """Преобразует событие DevOps в Signal и маршрутизирует через Classifier"""
        signal = Signal(
            project=data.get("repository", "unknown"),
            resource=data.get("workflow_name", "pipeline"),
            source="devops",
            event_type=event_type,
            status="success" if "success" in event_type else "failure",
            payload=data
        )

        cs = await self.classifier.classify(signal)
        if cs.action == "ignore":
            return

        repo = signal.project
        workflow = signal.resource
        author = data.get("author", "unknown")

        if cs.severity == "SUCCESS":
            text = f"🚀 CI/CD: Сборка {repo} ({workflow}) успешно завершена. Автор: @{author}"
            await self.add_to_timeline(text, "SUCCESS")
        else:
            text = f"❌ CI/CD: Пайплайн {repo} ({workflow}) упал! Автор: @{author}"
            await self.add_to_timeline(text, "WARNING")

    async def on_resource_failed(self, event_type: str, data: dict) -> None:
        """Преобразует событие сбоя ресурса в Signal и маршрутизирует через Classifier"""
        signal = Signal(
            project=data["agent"],
            resource=data["resource"],
            source="collector",
            event_type=event_type,
            status=data["new_status"],
            payload=data
        )

        cs = await self.classifier.classify(signal)
        if cs.action == "ignore":
            return

        project = signal.project
        resource = signal.resource
        severity = cs.severity

        active_key = f"nexus:incident:active:{project}:{resource}"

        incident_num = await self.redis.incr("nexus:incident:counter")
        incident_id = f"{incident_num}"

        acquired = await self.redis.set(
            active_key, incident_id, nx=True, ex=settings.INCIDENT_LOCK_TTL_SECONDS
        )
        if not acquired:
            logger.debug(f"Incident for {project}:{resource} is already open. Skipping.")
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
            logger.debug(f"Failed to fetch restart count for {project}:{resource}: {ex}")

        incident = Incident(
            id=incident_id,
            project=project,
            resource=resource,
            severity=severity,
            status="open",
            opened_at=datetime.now(timezone.utc),
            reason=f"Resource transitioned to state: {signal.status}",
            logs=logs,
            restart_count=restart_count,
        )

        await self.redis.set(
            f"nexus:incident:detail:{incident_id}", incident.model_dump_json()
        )
        await self.redis.lpush("nexus:incidents:history", incident_id)

        await self.add_to_timeline(
            f"🚨 Сбой ресурса {project}:{resource} (рестарты: {restart_count})",
            severity=severity,
        )

        logger.info(f"🚨 IncidentService: Created Incident #{incident_id} for {project}:{resource}")
        await self.event_bus.publish("incident:opened", incident.model_dump())

    async def on_resource_recovered(self, event_type: str, data: dict) -> None:
        """Преобразует событие восстановления в Signal и маршрутизирует через Classifier"""
        signal = Signal(
            project=data["agent"],
            resource=data["resource"],
            source="collector",
            event_type=event_type,
            status="healthy",
            payload=data
        )

        cs = await self.classifier.classify(signal)
        if cs.action == "ignore":
            return

        project = signal.project
        resource = signal.resource

        active_key = f"nexus:incident:active:{project}:{resource}"
        incident_id = await self.redis.get(active_key)
        if not incident_id:
            # The lock may have expired before the recovery event arrived.
            # Locate the still-open incident so it can be closed normally.
            incident_id = None
            incident_ids = await self.redis.lrange("nexus:incidents:history", 0, -1)
            for candidate_id in incident_ids:
                raw_candidate = await self.redis.get(
                    f"nexus:incident:detail:{candidate_id}"
                )
                if not raw_candidate:
                    continue
                candidate = Incident.model_validate_json(raw_candidate)
                if (
                    candidate.project == project
                    and candidate.resource == resource
                    and candidate.status == "open"
                ):
                    incident_id = candidate_id
                    break

            if not incident_id:
                logger.debug(f"IncidentService: No active incident found for {project}:{resource} to resolve.")
                return
        else:
            await self.redis.delete(active_key)

        detail_key = f"nexus:incident:detail:{incident_id}"
        raw_incident = await self.redis.get(detail_key)
        if not raw_incident:
            logger.warning(f"IncidentService: Active record #{incident_id} exists but details were not found.")
            return

        incident = Incident.model_validate_json(raw_incident)

        resolved_at = datetime.now(timezone.utc)
        duration_seconds = (resolved_at - incident.opened_at).total_seconds()

        incident.status = "resolved"
        incident.resolved_at = resolved_at
        incident.duration = round(duration_seconds, 2)

        await self.redis.set(detail_key, incident.model_dump_json())

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
        await self.event_bus.publish("incident:resolved", incident.model_dump())

    async def on_app_error(self, event_type: str, data: dict) -> None:
        """Регистрирует аварию приложения на основе сигнала от SDK, дедуплицируя ее в Error Registry"""
        signal = Signal(
            project=data["project"],
            resource="app",
            source="sdk",
            event_type=event_type,
            status="error",
            payload=data
        )

        cs = await self.classifier.classify(signal)
        if cs.action == "ignore":
            return

        project = signal.project
        resource = signal.resource
        severity = cs.severity

        # Генерация фингерпринта ошибки по traceback
        traceback_str = data.get("traceback") or ""
        exception_type = data.get("exception_type") or "Exception"
        fingerprint = self.generate_fingerprint(project, exception_type, traceback_str)

        now_str = datetime.now(timezone.utc).isoformat()
        err_key = f"nexus:errors:{fingerprint}"

        # 1. Запись в Error Registry в Redis
        exists = await self.redis.exists(err_key)
        if not exists:
            # Первая регистрация ошибки
            await self.redis.hset(err_key, mapping={
                "project": project,
                "exception_type": exception_type,
                "first_seen": now_str,
                "last_seen": now_str,
                "count": "1",
                "last_message": data.get("message") or "",
                "resolved": "0"
            })
            await self.redis.sadd("nexus:errors:all", fingerprint)
        else:
            # Обновление счетчиков повторений ошибки
            async with self.redis.pipeline() as pipe:
                pipe.hincrby(err_key, "count", 1)
                pipe.hset(err_key, "last_seen", now_str)
                pipe.hset(err_key, "last_message", data.get("message") or "")
                pipe.hset(err_key, "resolved", "0")  # Сбрасываем флаг завершения, если ошибка вернулась
                await pipe.execute()

        # 2. Создание инцидента
        active_key = f"nexus:incident:active:{project}:{resource}"
        incident_num = await self.redis.incr("nexus:incident:counter")
        incident_id = f"{incident_num}"

        acquired = await self.redis.set(
            active_key, incident_id, nx=True, ex=settings.INCIDENT_LOCK_TTL_SECONDS
        )
        if not acquired:
            logger.debug(f"Incident for {project}:{resource} is already open. Skipping.")
            return

        logs = traceback_str or "No traceback provided."
        reason = f"Application Exception: {exception_type} - {data.get('message')}"

        incident = Incident(
            id=incident_id,
            project=project,
            resource=resource,
            severity=severity,
            status="open",
            opened_at=datetime.now(timezone.utc),
            reason=reason,
            logs=logs,
            restart_count=0,
            fingerprint=fingerprint
        )

        await self.redis.set(
            f"nexus:incident:detail:{incident_id}", incident.model_dump_json()
        )
        await self.redis.lpush("nexus:incidents:history", incident_id)

        await self.add_to_timeline(
            f"🚨 Ошибка приложения {project.upper()}: {exception_type}",
            severity=severity,
        )

        logger.info(f"🚨 IncidentService: Created app Incident #{incident_id} for {project}:{resource}")
        await self.event_bus.publish("incident:opened", incident.model_dump())

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
