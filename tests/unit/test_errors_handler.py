from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import ErrorEvent, Message

from tj_bot.handlers.errors import USER_FACING_ERROR, on_error


def make_event(message: Message | None, callback_message: Message | None) -> ErrorEvent:
    update = MagicMock()
    update.message = message
    update.callback_query = (
        MagicMock(message=callback_message) if callback_message else None
    )
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = RuntimeError("boom")
    return cast(ErrorEvent, event)


async def test_error_answered_on_message_update() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()

    await on_error(make_event(message, None))

    message.answer.assert_awaited_once_with(USER_FACING_ERROR)


async def test_error_answered_on_callback_update() -> None:
    callback_message = AsyncMock(spec=Message)
    callback_message.answer = AsyncMock()

    await on_error(make_event(None, callback_message))

    callback_message.answer.assert_awaited_once_with(USER_FACING_ERROR)


async def test_error_without_deliverable_target_is_silent() -> None:
    # neither message nor callback message: nothing to answer, must not raise
    await on_error(make_event(None, None))


async def test_error_notice_delivery_failure_swallowed() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=1, text="x"), message="blocked"
        )
    )

    # must not propagate the secondary failure
    await on_error(make_event(message, None))
