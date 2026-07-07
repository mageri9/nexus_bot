import asyncio
import sys
from loguru import logger  # [1]
from config import settings
from agents import imagebot_agent


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

    logger.info("--- Testing Agent Layer ---")

    # Спрашиваем здоровье всего проекта ОДНОЙ командой
    logger.info(f"Checking project health: '{imagebot_agent.name}'...")
    health_report = await imagebot_agent.get_health()

    logger.success(f"Aggregated health report: {health_report}")

    # Точечно достаем метрики конкретного ресурса через Агента
    logger.info("Requesting detailed metrics via Agent resources...")
    app_metrics = await imagebot_agent.resources["app"].get_metrics()
    storage_metrics = await imagebot_agent.resources["storage"].get_metrics()

    logger.success(f"App container metrics: {app_metrics}")
    logger.success(f"Storage metrics: {storage_metrics}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()