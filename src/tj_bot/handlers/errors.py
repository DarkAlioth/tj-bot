import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, Message

logger = logging.getLogger(__name__)

errors_router = Router()

USER_FACING_ERROR = "Что-то пошло не так, попробуйте ещё раз позже 🛠"


@errors_router.errors()
async def on_error(event: ErrorEvent) -> None:
    logger.exception(
        "Unhandled error while processing update", exc_info=event.exception
    )
    message: Message | None = None
    if event.update.message is not None:
        message = event.update.message
    elif event.update.callback_query is not None and isinstance(
        event.update.callback_query.message, Message
    ):
        message = event.update.callback_query.message
    if message is None:
        return
    try:
        await message.answer(USER_FACING_ERROR)
    except TelegramAPIError:
        logger.warning("Failed to deliver the error notice to the chat")
