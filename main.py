import asyncio
import sys
from loguru import logger  # [1]
from config import settings
from services import query_service


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
    logger.info("--- Testing Query Service Layer ---")

    # 1. Быстрый опрос статуса всей системы
    logger.info("Fetching global system status summary...")
    system_status = await query_service.get_system_status()
    logger.success(f"System Status: {system_status}")

    # 2. Подробные данные по агенту ImageBot (метрики контейнера + диска)
    logger.info("Fetching details for 'imagebot'...")
    imagebot_details = await query_service.get_agent_details("imagebot")
    logger.success(f"ImageBot Details:\n{imagebot_details}")

    # 3. Чтение логов через сервисный слой
    logger.info("Requesting logs for 'imagebot.app'...")
    try:
        logs = await query_service.get_resource_logs("imagebot", "app", limit=3)
        logger.success(f"Successfully retrieved logs:\n{logs}")
    except Exception as e:
        logger.error(f"Failed to fetch logs: {e}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()