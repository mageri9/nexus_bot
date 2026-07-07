from aiogram import Bot, Dispatcher
from loguru import logger  # [1]

from config import settings
from services import event_bus  # Наша шина событий
from .handlers import router
from .notifier import TelegramNotifier  # Оповещатель


async def start_bot():
    # Чистая инициализация без прокси-сессий
    bot = Bot(token=settings.bot_token_str)
    dp = Dispatcher()

    dp.include_router(router)

    # --- НАСТРОЙКА ШИНЫ СОБЫТИЙ ---
    notifier = TelegramNotifier(bot=bot)

    # Подписываем notifier на события шины
    event_bus.subscribe("action:success", notifier.on_action_success)
    event_bus.subscribe("action:failed", notifier.on_action_failed)

    logger.info("Clearing potential webhook conflicts and dropping pending updates...")
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Nexus telegram terminal starting poll...")
    await dp.start_polling(bot)