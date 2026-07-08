import asyncio
import json
from loguru import logger
from redis.asyncio import Redis
from core import AgentRegistry
from services.event_bus import EventBus


class StateCollector:
    def __init__(
        self,
        registry: AgentRegistry,
        redis_client: Redis,
        event_bus: EventBus,
        interval: int = 5,
    ):
        self.registry = registry
        self.redis = redis_client
        self.event_bus = event_bus
        self.interval = interval
        self._task: asyncio.Task | None = None

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

                    state = {}

                    # Собираем свежие данные со всех ресурсов проекта
                    for res_name, resource in agent.resources.items():
                        status = await resource.get_status()
                        try:
                            metrics = await resource.get_metrics()
                        except Exception as e:
                            metrics = {"error": str(e)}

                        state[res_name] = {"status": status, "metrics": metrics}

                        # 2. Анализ переходов состояний
                        old_res = old_state.get(res_name)
                        old_status = old_res.get("status") if old_res else None

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
                    for old_res_name, old_res_data in old_state.items():
                        if old_res_name not in agent.resources:
                            deleted_payload = {
                                "agent": agent_name,
                                "resource": old_res_name,
                                "old_status": old_res_data.get("status"),
                                "new_status": "deleted",
                                "metrics": old_res_data.get("metrics", {}),
                            }
                            logger.warning(
                                f"Collector: Resource {agent_name}:{old_res_name} was removed from manifest."
                            )
                            await self.event_bus.publish(
                                "ResourceDeleted", deleted_payload
                            )

                    # Сохраняем обновленный слепок состояния
                    await self.redis.set(key, json.dumps(state))

            except Exception as e:
                logger.error(f"Collector loop error: {e}")

            await asyncio.sleep(self.interval)