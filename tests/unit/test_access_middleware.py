from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message, TelegramObject

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.middlewares.access import AccessMiddleware


def make_event(user_id: int) -> AsyncMock:
    event = AsyncMock(spec=Message)
    event.from_user = MagicMock()
    event.from_user.id = user_id
    event.from_user.username = "u"
    event.from_user.full_name = "Full Name"
    return event


def make_data(repo: AsyncMock, admin: bool = False) -> dict[str, Any]:
    config = MagicMock(spec=AppConfig)
    config.is_admin = lambda _uid: admin
    return {"repo": repo, "config": cast(AppConfig, config)}


async def test_tracks_user_and_passes() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.is_blocked.return_value = False
    handler = AsyncMock(return_value="ok")

    result = await AccessMiddleware()(
        handler, cast(TelegramObject, make_event(1)), make_data(repo)
    )

    assert result == "ok"
    repo.touch_user.assert_awaited_once_with(1, "u", "Full Name")


async def test_blocked_user_dropped() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.is_blocked.return_value = True
    handler = AsyncMock(return_value="ok")

    result = await AccessMiddleware()(
        handler, cast(TelegramObject, make_event(2)), make_data(repo)
    )

    assert result is None
    handler.assert_not_awaited()


async def test_admin_never_blocked() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.is_blocked.return_value = True
    handler = AsyncMock(return_value="ok")

    result = await AccessMiddleware()(
        handler, cast(TelegramObject, make_event(3)), make_data(repo, admin=True)
    )

    assert result == "ok"
    repo.is_blocked.assert_not_awaited()
