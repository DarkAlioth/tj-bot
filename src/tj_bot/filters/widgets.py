from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from tj_bot.db.repo import TorrentRepo
from tj_bot.widgets.api import WidgetQuery


class WidgetUser(BaseFilter):
    """Users who have not opted out of widgets (``bot_users.classic_ui``).

    Widget routers go before the classic ones: when this filter fails, the
    classic text handlers take the command.
    """

    async def __call__(self, event: TelegramObject, repo: TorrentRepo) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and not await repo.uses_classic_ui(user.id)


class WidgetKind(BaseFilter):
    """The query comes from one of our widgets of these kinds; passes ``widget``."""

    def __init__(self, *kinds: str) -> None:
        self.kinds = frozenset(kinds)

    async def __call__(
        self, query: WidgetQuery, repo: TorrentRepo
    ) -> bool | dict[str, Any]:
        if query.chat is None or query.message_id is None:
            return False
        widget = await repo.get_widget_message(query.chat.id, query.message_id)
        if widget is None or widget.kind not in self.kinds:
            return False
        return {"widget": widget}
