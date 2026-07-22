from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message, TelegramObject

from tj_bot.config import AppConfig
from tj_bot.middlewares.throttling import ThrottlingMiddleware


def make_message(user_id: int, text: str) -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.text = text
    message.answer = AsyncMock()
    return message


def make_data(admin: bool = False) -> dict[str, Any]:
    config = MagicMock(spec=AppConfig)
    config.is_admin = lambda _uid: admin
    return {"config": cast(AppConfig, config)}


async def test_search_quota_blocks_after_limit() -> None:
    middleware = ThrottlingMiddleware(min_interval=0, search_limit=3)
    handler = AsyncMock(return_value="ok")
    data = make_data()

    for _ in range(3):
        message = make_message(1, "/s ubuntu")
        assert await middleware(handler, cast(TelegramObject, message), data) == "ok"

    blocked = make_message(1, "/s ubuntu")
    result = await middleware(handler, cast(TelegramObject, blocked), data)

    assert result is None
    assert handler.await_count == 3
    blocked.answer.assert_awaited_once()


async def test_fast_repeats_are_dropped_silently() -> None:
    middleware = ThrottlingMiddleware(min_interval=100)
    handler = AsyncMock(return_value="ok")
    data = make_data()

    first = make_message(2, "привет")
    second = make_message(2, "привет ещё раз")

    assert await middleware(handler, cast(TelegramObject, first), data) == "ok"
    assert await middleware(handler, cast(TelegramObject, second), data) is None
    second.answer.assert_not_awaited()


async def test_admin_is_exempt() -> None:
    middleware = ThrottlingMiddleware(min_interval=100, search_limit=0)
    handler = AsyncMock(return_value="ok")
    data = make_data(admin=True)

    for _ in range(5):
        message = make_message(3, "/s x")
        assert await middleware(handler, cast(TelegramObject, message), data) == "ok"
