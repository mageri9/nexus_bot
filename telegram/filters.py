from aiogram.filters import Filter
from aiogram.types import Message
from config import settings


class IsAdmin(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in settings.admin_id_list