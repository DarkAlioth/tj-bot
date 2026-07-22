from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from tj_bot.config import AppConfig


class AdminOnly(BaseFilter):
    async def __call__(self, event: TelegramObject, config: AppConfig) -> bool:
        user = getattr(event, "from_user", None)
        return config.is_admin(user.id if user else None)
