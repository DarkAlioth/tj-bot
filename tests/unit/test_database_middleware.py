from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.db.repo import TorrentRepo
from tj_bot.middlewares.database import DatabaseMiddleware


def make_pool(session: AsyncMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock(return_value=context)
    return pool


async def test_repo_provided_and_committed_on_success() -> None:
    session = AsyncMock()
    pool = make_pool(session)
    middleware = DatabaseMiddleware(cast(async_sessionmaker[AsyncSession], pool))
    handler = AsyncMock(return_value="handled")
    data: dict[str, Any] = {}

    result = await middleware(handler, cast(TelegramObject, object()), data)

    assert result == "handled"
    assert isinstance(data["repo"], TorrentRepo)
    assert data["repo"].session is session
    session.commit.assert_awaited_once()


async def test_no_commit_when_handler_raises() -> None:
    session = AsyncMock()
    pool = make_pool(session)
    middleware = DatabaseMiddleware(cast(async_sessionmaker[AsyncSession], pool))
    handler = AsyncMock(side_effect=RuntimeError("boom"))

    try:
        await middleware(handler, cast(TelegramObject, object()), {})
    except RuntimeError:
        pass

    session.commit.assert_not_awaited()
