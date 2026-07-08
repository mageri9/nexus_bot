from aiogram import Bot, Dispatcher
from loguru import logger  # [1]

from config import settings
from services import event_bus, state_collector  # Импортируем сборщик
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

    # Регистрация подписчиков на события инцидентов
    event_bus.subscribe("incident:opened", notifier.on_incident_opened)
    event_bus.subscribe("incident:resolved", notifier.on_incident_resolved)

    # --- ЗАПУСК ФОНОВОГО СБОРЩИКА ---
    state_collector.start()

    logger.info("Clearing potential webhook conflicts and dropping pending updates...")
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Nexus telegram terminal starting poll...")
    try:
        await dp.start_polling(bot)
    finally:
        # Грациозно завершаем задачу сборщика при выходе из бота [1]
        await state_collector.stop()