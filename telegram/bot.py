from aiogram import Bot, Dispatcher
from loguru import logger  # [1]
from config import settings
from .handlers import router


async def start_bot():
    bot = Bot(token=settings.bot_token_str)
    dp = Dispatcher()

    # Регистрируем хэндлеры
    dp.include_router(router)

    logger.info("Nexus telegram terminal starting poll...")
    await dp.start_polling(bot)