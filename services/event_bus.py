import asyncio
from typing import Dict, List, Callable, Coroutine, Any
from loguru import logger  # [1]

# Типизация подписчика: асинхронная функция, принимающая имя события и данные payload
EventSubscriber = Callable[[str, Any], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[EventSubscriber]] = {}

    def subscribe(self, event_type: str, subscriber: EventSubscriber) -> None:
        """Регистрирует подписчика на определенный тип событий"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(subscriber)
        logger.debug(f"EventBus: Subscribed to '{event_type}'")

    async def publish(self, event_type: str, data: Any) -> None:
        """Рассылает событие всем зарегистрированным подписчикам параллельно"""
        logger.debug(f"EventBus: Publishing event '{event_type}' with payload: {data}")
        subscribers = self._subscribers.get(event_type, [])

        # Создаем задачи для параллельного уведомления всех подписчиков [1]
        tasks = [sub(event_type, data) for sub in subscribers]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)