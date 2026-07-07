import asyncio
import sys
from loguru import logger  # [1]
from config import settings
from telegram import start_bot


def init_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="DEBUG" if settings.DEBUG else "INFO",
    )


async def main_async():
    init_logging()
    logger.info("Initializing Nexus Core...")

    try:
        # Передаем управление циклом асинхронному поллингу бота [1]
        await start_bot()
    except Exception as e:
        logger.critical(f"Nexus bot stopped with fatal error: {e}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()