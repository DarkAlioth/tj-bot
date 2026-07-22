from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from tgbot.services.broadcaster import broadcast, send_message


def make_bot(side_effect: object = None) -> AsyncMock:
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=side_effect)
    return bot


async def test_send_message_success_returns_true() -> None:
    bot = make_bot()

    assert await send_message(bot, 123, "hello") is True
    bot.send_message.assert_awaited_once()


async def test_send_message_forbidden_returns_false() -> None:
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=123, text="hello"), message="bot was blocked"
    )
    bot = make_bot(side_effect=error)

    assert await send_message(bot, 123, "hello") is False


async def test_send_message_retry_after_retries_and_succeeds() -> None:
    error = TelegramRetryAfter(
        method=SendMessage(chat_id=123, text="hello"),
        message="flood",
        retry_after=0,
    )
    bot = make_bot(side_effect=[error, None])

    assert await send_message(bot, 123, "hello") is True
    assert bot.send_message.await_count == 2


async def test_broadcast_counts_only_delivered() -> None:
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=2, text="hi"), message="bot was blocked"
    )
    bot = make_bot(side_effect=[None, error, None])

    assert await broadcast(bot, [1, 2, 3], "hi") == 2
