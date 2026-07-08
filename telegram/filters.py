from aiogram.filters import Filter
from aiogram.types import Message
from loguru import logger  # [1]
from config import settings


class IsAdmin(Filter):
    async def __call__(self, message: Message) -> bool:
        user_id = message.from_user.id
        username = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else "no_username"
        )

        logger.debug(
            f"Access check: User ID={user_id} ({username}). Allowed: {settings.admin_id_list}"
        )

        is_allowed = user_id in settings.admin_id_list
        if not is_allowed:
            logger.warning(f"Access DENIED for user {user_id} ({username})")

        return is_allowed