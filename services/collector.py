import asyncio
import json
from loguru import logger  # [1]
from redis.asyncio import Redis  # [1]
from core import AgentRegistry


class StateCollector:
    def __init__(self, registry: AgentRegistry, redis_client: Redis, interval: int = 5):
        self.registry = registry
        self.redis = redis_client
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
                    state = {}

                    # Собираем данные со всех ресурсов проекта
                    for res_name, resource in agent.resources.items():
                        status = await resource.get_status()
                        try:
                            metrics = await resource.get_metrics()
                        except Exception as e:
                            metrics = {"error": str(e)}

                        state[res_name] = {"status": status, "metrics": metrics}

                    # Записываем сырой JSON-слепок состояния агента в Redis [1]
                    key = f"nexus:state:{agent_name}"
                    await self.redis.set(key, json.dumps(state))
                    logger.debug(
                        f"Collector: State for '{agent_name}' cached in Redis."
                    )

            except Exception as e:
                logger.error(f"Collector loop error: {e}")

            await asyncio.sleep(self.interval)