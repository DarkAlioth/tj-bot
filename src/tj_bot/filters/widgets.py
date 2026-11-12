from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from tj_bot.db.repo import TorrentRepo


class WidgetUser(BaseFilter):
    """Users who have not opted out of widgets (``bot_users.classic_ui``).

    Widget routers go before the classic ones: when this filter fails, the
    classic text handlers take the command.
    """

    async def __call__(self, event: TelegramObject, repo: TorrentRepo) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and not await repo.uses_classic_ui(user.id)
