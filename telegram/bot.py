from aiogram import Bot, Dispatcher
from loguru import logger

from config import settings
from services import (
    event_bus,
    state_collector,
    pubsub_listener,
    log_collector,
)
from .handlers import router
from .notifier import TelegramNotifier


async def start_bot():
    bot = Bot(token=settings.bot_token_str)
    dp = Dispatcher()

    dp.include_router(router)

    # Настройка шины событий
    notifier = TelegramNotifier(bot=bot)
    event_bus.subscribe("action:success", notifier.on_action_success)
    event_bus.subscribe("action:failed", notifier.on_action_failed)

    event_bus.subscribe("incident:opened", notifier.on_incident_opened)
    event_bus.subscribe("incident:resolved", notifier.on_incident_resolved)

    # Подписываем нотификатор на DevOps события, пришедшие из Redis Pub/Sub
    event_bus.subscribe("devops:workflow_success", notifier.on_devops_workflow_success)
    event_bus.subscribe("devops:workflow_failure", notifier.on_devops_workflow_failure)

    # --- ЗАПУСК ФОНОВЫХ СЛУЖБ ---
    state_collector.start()
    log_collector.start()
    pubsub_listener.start()  # Запускаем прослушивание Redis Pub/Sub

    logger.info("Clearing potential webhook conflicts and dropping pending updates...")
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Nexus telegram terminal starting poll...")
    try:
        await dp.start_polling(bot)
    finally:
        # Грациозно останавливаем фоновые процессы при завершении работы бота
        await state_collector.stop()
        await log_collector.stop()
        await pubsub_listener.stop()