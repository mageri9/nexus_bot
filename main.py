import asyncio
import sys
from loguru import logger  # [1]
from config import settings
from agents import registry


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
    logger.info("--- Testing Registry Layer ---")

    # 1. Получаем список всех доступных проектов в экосистеме
    available_agents = registry.list_agents()
    logger.info(f"Discovered registered agents: {available_agents}")

    # 2. Динамически опрашиваем каждый обнаруженный проект
    for agent_name in available_agents:
        logger.info(f"Dynamically resolving state for '{agent_name}'...")

        # Запрашиваем агента из реестра по имени
        agent = registry.get(agent_name)

        # Получаем здоровье
        health = await agent.get_health()
        logger.success(f"[{agent_name.upper()}] Health state: {health}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()