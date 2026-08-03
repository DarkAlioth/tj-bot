from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Message

from tj_bot.services.feedback import ack_silent, alert_or_message
from tj_bot.services.formatting import padded


def expired() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="query is too old"
    )


def make_query(message: Message | MagicMock | None) -> AsyncMock:
    query = AsyncMock()
    query.message = message
    return query


async def test_alert_delivered_without_chat_fallback() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    query = make_query(message)

    await alert_or_message(query, "oops")

    query.answer.assert_awaited_once_with("oops", show_alert=True)
    message.answer.assert_not_awaited()


async def test_expired_query_falls_back_to_chat() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    query = make_query(message)
    query.answer.side_effect = expired()

    await alert_or_message(query, "oops")

    message.answer.assert_awaited_once_with(padded("oops"))


async def test_fallback_without_message_is_silent() -> None:
    query = make_query(None)
    query.answer.side_effect = expired()

    await alert_or_message(query, "oops")


async def test_chat_fallback_failure_swallowed() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock(side_effect=expired())
    query = make_query(message)
    query.answer.side_effect = expired()

    await alert_or_message(query, "oops")


async def test_ack_silent_swallows_expired_query() -> None:
    query = make_query(None)
    query.answer.side_effect = expired()

    await ack_silent(query)


async def test_ack_silent_passes_text() -> None:
    query = make_query(None)

    await ack_silent(query, "done")

    query.answer.assert_awaited_once_with("done")
