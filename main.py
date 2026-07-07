import sys
from loguru import logger  # [1]
from config.settings import settings


def init_logging():
    # Очищаем дефолтные обработчики логгера
    logger.remove()

    # Лог в консоль с красивым форматированием
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="DEBUG" if settings.DEBUG else "INFO",
    )

    # Дублируем логи в файл
    logger.add("logs/nexus.log", rotation="10 MB", retention="10 days", level="INFO")


def main():
    init_logging()
    logger.info("Initializing Nexus Core...")
    logger.debug("Debug mode enabled")
    logger.info(f"Target Redis: {settings.REDIS_URL}")
    logger.success("Nexus Foundation successfully initialized.")


if __name__ == "__main__":
    main()