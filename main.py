import asyncio
import sys
from loguru import logger  # [1]
from config.settings import settings
from transports.local_shell import LocalShellTransport
from infra.docker import DockerContainer


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

    transport = LocalShellTransport()

    # Инициализируем наш первый ресурс, связав его с транспортом
    test_container = DockerContainer(
        name="test_service", transport=transport, container_name="nexus-test"
    )

    logger.info("--- Testing Resource Layer ---")

    # Шаг 1: Проверяем статус
    status = await test_container.get_status()
    logger.info(f"Container status: {status}")

    if status == "unknown":
        logger.error("Test container 'nexus-test' is not found. Please run:")
        logger.error("docker run -d --name nexus-test alpine sleep 1000")
        return

    # Шаг 2: Получаем метрики
    metrics = await test_container.get_metrics()
    logger.success(f"Fetched metrics: {metrics}")

    # Шаг 3: Читаем последние строки логов
    logs = await test_container.get_logs(limit=3)
    logger.info(f"Container logs:\n{logs if logs else '[Empty]'}")

    # Шаг 4: Тестируем мутирующее действие (restart)
    logger.info("Restarting container...")
    restart_result = await test_container.restart()
    logger.success(f"Container successfully restarted! ID: {restart_result}")

    # Перепроверяем статус после перезагрузки
    new_status = await test_container.get_status()
    logger.success(f"Post-restart status: {new_status}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()