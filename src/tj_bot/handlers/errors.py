import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import ErrorEvent

from tj_bot.services.feedback import alert_or_message
from tj_bot.services.formatting import padded

logger = logging.getLogger(__name__)

errors_router = Router()

USER_FACING_ERROR = "Что-то пошло не так, попробуйте ещё раз позже 🛠"


@errors_router.errors()
async def on_error(event: ErrorEvent) -> None:
    exception = event.exception
    if isinstance(exception, TelegramBadRequest) and (
        "message is not modified" in str(exception)
    ):
        # benign: a repeated tap on a stale keyboard re-rendered identical
        # content — nothing to report
        logger.debug("Ignored a no-op edit from a repeated tap")
        return
    logger.exception("Unhandled error while processing update", exc_info=exception)
    callback = event.update.callback_query
    if callback is not None:
        # popup while the query is alive; expired queries (slow handler)
        # degrade to a chat message so the user is never left in the dark
        await alert_or_message(callback, USER_FACING_ERROR)
        return
    message = event.update.message
    if message is None:
        return
    try:
        await message.answer(padded(USER_FACING_ERROR))
    except TelegramAPIError:
        logger.warning("Failed to deliver the error notice to the chat")
