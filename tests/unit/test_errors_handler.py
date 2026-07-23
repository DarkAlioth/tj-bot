from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import ErrorEvent, Message

from tj_bot.handlers.errors import USER_FACING_ERROR, on_error
from tj_bot.services.formatting import padded


def make_event(
    message: Message | None,
    callback: AsyncMock | None,
    exception: Exception | None = None,
) -> ErrorEvent:
    update = MagicMock()
    update.message = message
    update.callback_query = callback
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = exception or RuntimeError("boom")
    return cast(ErrorEvent, event)


async def test_error_answered_on_message_update() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()

    await on_error(make_event(message, None))

    message.answer.assert_awaited_once_with(padded(USER_FACING_ERROR))


async def test_error_on_callback_shows_alert_not_chat_message() -> None:
    callback = AsyncMock()

    await on_error(make_event(None, callback))

    callback.answer.assert_awaited_once_with(USER_FACING_ERROR, show_alert=True)


async def test_noop_edit_is_ignored_silently() -> None:
    callback = AsyncMock()
    exc = TelegramBadRequest(
        method=EditMessageText(chat_id=1, message_id=1, text="x"),
        message="Bad Request: message is not modified",
    )

    await on_error(make_event(None, callback, exc))

    callback.answer.assert_not_awaited()


async def test_error_without_deliverable_target_is_silent() -> None:
    await on_error(make_event(None, None))


async def test_error_notice_delivery_failure_swallowed() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=1, text="x"), message="blocked"
        )
    )

    await on_error(make_event(message, None))


async def test_alert_delivery_failure_swallowed() -> None:
    callback = AsyncMock()
    callback.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=1, text="x"), message="too old"
        )
    )

    await on_error(make_event(None, callback))
