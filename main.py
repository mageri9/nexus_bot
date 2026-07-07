import asyncio
import sys
from loguru import logger  # [1]
from config.settings import settings
from transports.local_shell import LocalShellTransport


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

    # Инициализируем локальный транспорт
    transport = LocalShellTransport()

    logger.info("--- Testing Transport Layer ---")

    try:
        # Тест 1: Простая безопасная команда echo
        echo_result = await transport.run(["echo", "Nexus transport is alive!"])
        logger.success(f"Test 1 (echo) success: '{echo_result}'")

        # Тест 2: Проверим, доступен ли Docker CLI
        logger.info("Test 2: Requesting running Docker containers...")
        docker_result = await transport.run(["docker", "ps", "--format", "{{.Names}}"])

        if docker_result:
            logger.success(f"Test 2 success! Running containers:\n{docker_result}")
        else:
            logger.success(
                "Test 2 success! Docker is running, but no active containers found."
            )

    except RuntimeError as e:
        logger.warning(
            f"Sys-call test finished with controlled exception (e.g. Docker is offline): {e}"
        )
    except Exception as e:
        logger.exception(f"Unexpected transport failure: {e}")


def main():
    # Запускаем асинхронный цикл [1]
    asyncio.run(main_async())


if __name__ == "__main__":
    main()