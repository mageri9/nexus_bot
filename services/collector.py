import asyncio
import json
from datetime import datetime, timezone
from loguru import logger
from redis.asyncio import Redis
from core import AgentRegistry
from services.event_bus import EventBus
from infra import DockerContainer, HostDiskResource, ProjectStorageResource


class StateCollector:
    def __init__(
        self,
        registry: AgentRegistry,
        redis_client: Redis,
        event_bus: EventBus,
        interval: int = 5,
        debounce_ticks: int = 1,
    ):
        self.registry = registry
        self.redis = redis_client
        self.event_bus = event_bus
        self.interval = interval
        self.debounce_ticks = debounce_ticks
        self._task: asyncio.Task | None = None
        # Хранилище для дребезга переходов состояний:
        # {agent_name: {resource_name: (target_status, count)}}
        self._pending_transitions: dict[str, dict[str, tuple[str, int]]] = {}

    def start(self) -> None:
        """Запускает фоновый цикл сбора данных"""
        logger.info("Starting background StateCollector loop...")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Грациозно останавливает фоновую задачу"""
        if self._task:
            logger.info("Stopping StateCollector loop...")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _get_pending(self, agent_name: str, resource_name: str) -> tuple[str, int] | None:
        return self._pending_transitions.get(agent_name, {}).get(resource_name)

    def _set_pending(self, agent_name: str, resource_name: str, target_status: str, count: int) -> None:
        if agent_name not in self._pending_transitions:
            self._pending_transitions[agent_name] = {}
        self._pending_transitions[agent_name][resource_name] = (target_status, count)

    def _clear_pending(self, agent_name: str, resource_name: str) -> None:
        if agent_name in self._pending_transitions:
            self._pending_transitions[agent_name].pop(resource_name, None)


    async def _check_app_auto_recovery(self) -> None:
        """Проверяет открытые инциденты приложений и автоматически закрывает их при отсутствии новых ошибок"""
        try:
            # Сканируем активные инциденты приложений
            async for key in self.redis.scan_iter("nexus:incident:active:*:app"):
                parts = key.split(":")
                if len(parts) < 5:
                    continue
                project = parts[3]

                incident_id = await self.redis.get(key)
                if not incident_id:
                    continue

                # Извлекаем подробности инцидента
                detail_raw = await self.redis.get(
                    f"nexus:incident:detail:{incident_id}"
                )
                if not detail_raw:
                    continue

                incident_data = json.loads(detail_raw)
                fingerprint = incident_data.get("fingerprint")
                if not fingerprint:
                    continue

                # Извлекаем время последнего проявления ошибки из реестра
                err_data = await self.redis.hgetall(f"nexus:errors:{fingerprint}")
                if not err_data:
                    continue

                last_seen_str = err_data.get("last_seen")
                if not last_seen_str:
                    continue

                last_seen = datetime.fromisoformat(last_seen_str)
                now = datetime.now(timezone.utc)
                gap = (now - last_seen).total_seconds()

                # Сравниваем с порогом из настроек
                from config import settings

                threshold = getattr(settings, "APP_ERROR_AUTO_RECOVERY_THRESHOLD", 60)
                if gap > threshold:
                    logger.info(
                        f"Auto-Recovery: App exception '{fingerprint}' for '{project}' did not repeat for {gap:.1f}s. "
                        f"Resolving incident #{incident_id}."
                    )
                    # Помечаем ошибку как решенную в реестре
                    await self.redis.hset(
                        f"nexus:errors:{fingerprint}", "resolved", "1"
                    )

                    # Публикуем стандартное событие восстановления
                    await self.event_bus.publish(
                        "ResourceRecovered", {"agent": project, "resource": "app"}
                    )
        except Exception as e:
            logger.error(f"Error during app auto-recovery execution: {e}")

    async def _loop(self) -> None:
        while True:
            try:
                agent_names = self.registry.list_agents()
                logger.debug(f"Collector: Polling agents: {agent_names}")

                for agent_name in agent_names:
                    agent = self.registry.get(agent_name)

                    # 1. Получаем предыдущий слепок состояния из Redis
                    key = f"nexus:state:{agent_name}"
                    old_state_raw = await self.redis.get(key)
                    old_state = json.loads(old_state_raw) if old_state_raw else {}

                    # Безопасное извлечение старых статусов ресурсов (совместимость с V1)
                    old_resources_statuses = {}
                    if old_state.get("version") == 2:
                        for res_name, res_data in old_state.get("containers", {}).items():
                            old_resources_statuses[res_name] = res_data.get("status")
                        for res_name, res_data in old_state.get("storage", {}).items():
                            old_resources_statuses[res_name] = res_data.get("status")
                    else:
                        for res_name, res_data in old_state.items():
                            if isinstance(res_data, dict):
                                old_resources_statuses[res_name] = res_data.get("status")

                    # Инициализируем структуру State V2
                    state_v2 = {
                        "version": 2,
                        "agent_name": agent_name,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "containers": {},
                        "storage": {}
                    }

                    # Собираем свежие данные со всех ресурсов проекта
                    for res_name, resource in agent.resources.items():
                        probed_status = await resource.get_status()
                        try:
                            metrics = await resource.get_metrics()
                        except Exception as e:
                            metrics = {"error": str(e)}

                        committed_status = old_resources_statuses.get(res_name)

                        # Применение дебаунса переходов состояний
                        if committed_status is None:
                            # Первое появление ресурса — сохраняем статус без задержки
                            status_to_save = probed_status
                            self._clear_pending(agent_name, res_name)
                        elif probed_status == committed_status:
                            # Текущий статус совпадает с закоммиченным — сбрасываем дребезг
                            status_to_save = committed_status
                            self._clear_pending(agent_name, res_name)
                        else:
                            # Наблюдаем изменение статуса — крутим счётчик
                            pending = self._get_pending(agent_name, res_name)
                            if pending:
                                target_status, count = pending
                            else:
                                target_status, count = probed_status, 0

                            if probed_status == target_status:
                                count += 1
                            else:
                                # Если статус изменился на другой в процессе ожидания, перезапускаем счёт
                                target_status = probed_status
                                count = 1

                            if count >= self.debounce_ticks:
                                # Статус стабильно держится нужный интервал — фиксируем переход
                                status_to_save = probed_status
                                self._clear_pending(agent_name, res_name)
                            else:
                                # Порог ещё не пройден — сохраняем счётчик, в стейте оставляем старый статус
                                self._set_pending(agent_name, res_name, target_status, count)
                                status_to_save = committed_status

                        resource_payload = {
                            "status": status_to_save,
                            "metrics": metrics,
                            "capabilities": getattr(resource, "capabilities", [])
                        }

                        # Сортируем ресурсы по типам для структуры V2
                        if isinstance(resource, DockerContainer):
                            state_v2["containers"][res_name] = resource_payload
                        elif isinstance(resource, (HostDiskResource, ProjectStorageResource)):
                            state_v2["storage"][res_name] = resource_payload
                        else:
                            # Фолбек для кастомных типов ресурсов в будущем
                            if "other" not in state_v2:
                                state_v2["other"] = {}
                            state_v2["other"][res_name] = resource_payload

                        # 2. Анализ переходов состояний
                        old_status = committed_status
                        status = status_to_save

                        payload = {
                            "agent": agent_name,
                            "resource": res_name,
                            "old_status": old_status,
                            "new_status": status,
                            "metrics": metrics,
                        }

                        if old_status is None:
                            # Первое обнаружение ресурса (например, запуск Nexus)
                            event_type = (
                                "ResourceStarted"
                                if status in ("running", "healthy")
                                else "ResourceUnhealthy"
                            )
                            await self.event_bus.publish(event_type, payload)

                        elif old_status != status:
                            # Статус изменился. Определяем характер перехода:
                            was_healthy = old_status in ("running", "healthy")
                            is_healthy = status in ("running", "healthy")

                            event_type = None

                            if was_healthy and not is_healthy:
                                # Упал или стал недоступен
                                if status in ("exited", "stopped", "dead"):
                                    event_type = "ResourceStopped"
                                else:
                                    event_type = "ResourceUnhealthy"

                            elif not was_healthy and is_healthy:
                                # Восстановился
                                event_type = "ResourceRecovered"

                            elif not was_healthy and not is_healthy:
                                # Переход между разными ошибочными статусами (например, unknown -> exited)
                                if status in (
                                    "exited",
                                    "stopped",
                                    "dead",
                                ) and old_status not in ("exited", "stopped", "dead"):
                                    event_type = "ResourceStopped"

                            if event_type:
                                logger.info(
                                    f"Collector: State change detected for {agent_name}:{res_name} "
                                    f"({old_status} -> {status}). Triggering {event_type}."
                                )
                                await self.event_bus.publish(event_type, payload)

                    # 3. Анализ удаленных ресурсов (были в кеше, но исчезли из манифеста)
                    for old_res_name, old_res_status in old_resources_statuses.items():
                        if old_res_name not in agent.resources:
                            deleted_payload = {
                                "agent": agent_name,
                                "resource": old_res_name,
                                "old_status": old_res_status,
                                "new_status": "deleted",
                                "metrics": {},
                            }
                            logger.warning(
                                f"Collector: Resource {agent_name}:{old_res_name} was removed from manifest."
                            )
                            self._clear_pending(agent_name, old_res_name)
                            await self.event_bus.publish(
                                "ResourceDeleted", deleted_payload
                            )

                    # Сохраняем версионированный слепок состояния State V2
                    await self.redis.set(key, json.dumps(state_v2))

                # В конце каждого тика запускаем проверку затухания повторов исключений приложений
                await self._check_app_auto_recovery()

            except Exception as e:
                logger.error(f"Collector loop error: {e}")

            await asyncio.sleep(self.interval)


class LogCollector:
    def __init__(self, registry: AgentRegistry, redis_client: Redis, limit: int = 100):
        self.registry = registry
        self.redis = redis_client
        self.limit = limit
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    def start(self) -> None:
        """Запускает фоновые задачи стриминга логов для всех контейнеров"""
        self._running = True
        agent_names = self.registry.list_agents()
        logger.info(
            f"LogCollector: Starting continuous streaming for agents: {agent_names}"
        )

        for agent_name in agent_names:
            agent = self.registry.get(agent_name)
            for res_name, resource in agent.resources.items():
                if isinstance(resource, DockerContainer):
                    task_key = f"{agent_name}:{res_name}"
                    if task_key not in self._tasks:
                        self._tasks[task_key] = asyncio.create_task(
                            self._stream_container_logs(
                                agent_name, res_name, resource.container_name
                            )
                        )

    async def stop(self) -> None:
        """Грациозно останавливает все стримы логов"""
        self._running = False
        logger.info("LogCollector: Stopping log streaming tasks...")
        for task_key, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _stream_container_logs(
        self, agent_name: str, res_name: str, container_name: str
    ) -> None:
        """Потоковое чтение логов контейнера и сохранение в кольцевой буфер Redis"""
        while self._running:
            try:
                logger.debug(
                    f"LogCollector: Spawning docker logs -f for {agent_name}:{res_name}..."
                )

                # Запускаем 'docker logs -f' и сливаем stderr в stdout для полной картины
                cmd = [
                    "docker",
                    "logs",
                    "-f",
                    "--tail",
                    str(self.limit),
                    container_name,
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                # Построчно читаем поток
                while self._running:
                    line_bytes = await proc.stdout.readline()
                    if not line_bytes:
                        # Поток прервался (контейнер упал или перезапустился)
                        break

                    line = line_bytes.decode(errors="replace").rstrip("\r\n")
                    key = f"nexus:logs:{agent_name}:{res_name}"

                    # Пишем в Redis и обрезаем буфер до лимита
                    async with self.redis.pipeline() as pipe:
                        pipe.rpush(key, line)
                        pipe.ltrim(key, -self.limit, -1)
                        await pipe.execute()

                return_code = await proc.wait()
                logger.warning(
                    f"LogCollector: Stream for {agent_name}:{res_name} exited (code {return_code}). "
                    f"Reconnecting in 5 seconds..."
                )
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                # Мягкое завершение при остановке сервиса
                if "proc" in locals() and proc.returncode is None:
                    try:
                        proc.terminate()
                        await proc.wait()
                    except Exception:
                        pass
                raise
            except Exception as e:
                logger.error(
                    f"LogCollector: Error streaming logs for {agent_name}:{res_name}: {e}. "
                    f"Retrying in 5 seconds..."
                )
                await asyncio.sleep(5)