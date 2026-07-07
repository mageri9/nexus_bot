from aiogram import Bot
from loguru import logger  # [1]
from config import settings


class TelegramNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot
        # Берем первого админа из списка для отправки системных алертов [1]
        self.admin_id = settings.admin_id_list[0] if settings.admin_id_list else 0

    async def on_action_success(self, event_type: str, data: dict) -> None:
        """Реагирует на успешное выполнение действия"""
        logger.info(f"Notifier: Received success event: {data}")
        text = (
            f"🔔 <b>[NEXUS EVENT]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> completed successfully!"
        )
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Notifier failed to send success message: {e}")

    async def on_action_failed(self, event_type: str, data: dict) -> None:
        """Реагирует на ошибку при выполнении действия"""
        logger.error(f"Notifier: Received failure event: {data}")
        text = (
            f"⚠️ <b>[NEXUS EVENT ERROR]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> <b>failed</b>!\n"
            f"Error: <code>{data['error']}</code>"
        )
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Notifier failed to send failure message: {e}")