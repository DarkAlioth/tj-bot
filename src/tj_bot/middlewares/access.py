from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo


class AccessMiddleware(BaseMiddleware):
    """Track each user's identity and drop updates from blocked users.

    Runs after the database middleware so it has a repo/session. Admins are
    never blocked.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        user = getattr(event, "from_user", None)
        repo = data.get("repo")
        config = data.get("config")
        if user is None or not isinstance(repo, TorrentRepo):
            return await handler(event, data)
        await repo.touch_user(user.id, user.username, user.full_name)
        if isinstance(config, AppConfig) and not config.is_admin(user.id):
            if await repo.is_blocked(user.id):
                return None
        # release the bot_users row lock before the handler: a slow handler
        # (search, download) would otherwise block every other update from
        # the same user on their touch_user upsert until it finishes
        await repo.commit()
        return await handler(event, data)
