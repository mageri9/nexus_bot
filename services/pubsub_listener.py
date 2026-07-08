import asyncio
import json
from loguru import logger
from redis.asyncio import Redis
from services.event_bus import EventBus


class PubSubListener:
    def __init__(self, redis_client: Redis, event_bus: EventBus):
        self.redis = redis_client
        self.event_bus = event_bus
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Запускает бесконечный цикл прослушивания Redis Pub/Sub"""
        logger.info("Starting background Redis Pub/Sub DevOps listener...")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Грациозно останавливает фоновую задачу прослушивания"""
        if self._task:
            logger.info("Stopping Redis Pub/Sub DevOps listener...")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe("nexus:pubsub:devops")

        try:
            while True:
                # Опрашиваем сокет Pub/Sub без блокировки основного потока
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message:
                    try:
                        data = json.loads(message["data"])
                        event_type = data.get("event_type")
                        payload = data.get("payload", {})

                        logger.info(
                            f"PubSubListener: Discovered external event '{event_type}'"
                        )

                        # Пересылаем внешнее событие во внутренний EventBus бота
                        await self.event_bus.publish(event_type, payload)
                    except Exception as parse_err:
                        logger.error(
                            f"PubSubListener: Failed to parse Redis message: {parse_err}"
                        )

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"PubSubListener loop encountered error: {e}")
            # Плавный реконнект в случае падения сети Redis
            await asyncio.sleep(5)
            self.start()