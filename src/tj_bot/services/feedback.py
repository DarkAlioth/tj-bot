import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from tj_bot.services.formatting import padded

logger = logging.getLogger(__name__)


async def ack_silent(query: CallbackQuery, text: str | None = None) -> None:
    """Answer the callback (stop the spinner), ignoring an expired query."""
    try:
        await query.answer(text)
    except TelegramAPIError:
        logger.debug("Callback ack skipped (query expired)")


async def alert_or_message(query: CallbackQuery, text: str) -> None:
    """Error feedback as an alert popup, falling back to a chat message.

    Alerts keep the chat clean, but Telegram rejects a callback answered
    later than ~15s — after slow tracker/qBittorrent calls the popup may no
    longer be deliverable, so the text is posted to the chat instead.
    """
    try:
        await query.answer(text, show_alert=True)
    except TelegramAPIError:
        logger.debug("Error alert undeliverable, falling back to the chat")
    else:
        return
    message = query.message
    if not isinstance(message, Message):
        return
    try:
        await message.answer(padded(text))
    except TelegramAPIError:
        logger.warning("Failed to deliver callback feedback: %r", text)
