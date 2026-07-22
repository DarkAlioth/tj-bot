from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.db.repo import TorrentRepo


class DatabaseMiddleware(BaseMiddleware):
    """Open a session per update, expose the repo, commit on handler success."""

    def __init__(self, session_pool: async_sessionmaker[AsyncSession]) -> None:
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:  # noqa: ANN401  # signature dictated by aiogram BaseMiddleware
        async with self.session_pool() as session:
            data["repo"] = TorrentRepo(session)
            result = await handler(event, data)
            await session.commit()
            return result
