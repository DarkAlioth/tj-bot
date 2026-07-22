import asyncio
import logging
from collections.abc import Sequence

from aiogram import Bot, exceptions
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

MAX_FLOOD_RETRIES = 3


async def send_message(
    bot: Bot,
    user_id: int | str,
    text: str,
    disable_notification: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Send a message swallowing delivery errors; return delivery success."""
    # bounded retry loop (not recursion) so a pathological RetryAfter sequence
    # cannot grow the stack
    for _ in range(MAX_FLOOD_RETRIES + 1):
        try:
            await bot.send_message(
                user_id,
                text,
                disable_notification=disable_notification,
                reply_markup=reply_markup,
            )
        except exceptions.TelegramBadRequest:
            logger.error("Target [ID:%s]: chat not found", user_id)
            return False
        except exceptions.TelegramForbiddenError:
            logger.error("Target [ID:%s]: bot is blocked by the user", user_id)
            return False
        except exceptions.TelegramRetryAfter as e:
            logger.error(
                "Target [ID:%s]: flood limit exceeded, retrying in %s seconds",
                user_id,
                e.retry_after,
            )
            await asyncio.sleep(e.retry_after)
            continue
        except exceptions.TelegramAPIError:
            logger.exception("Target [ID:%s]: failed to deliver", user_id)
            return False
        else:
            return True
    logger.error(
        "Target [ID:%s]: giving up after %s retries", user_id, MAX_FLOOD_RETRIES
    )
    return False


async def broadcast(
    bot: Bot,
    users: Sequence[int | str],
    text: str,
    disable_notification: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    """Send a message to every user in the list; return delivered count."""
    count = 0
    try:
        for user_id in users:
            if await send_message(
                bot, user_id, text, disable_notification, reply_markup
            ):
                count += 1
            await asyncio.sleep(0.05)  # stay under Telegram's 30 msg/sec limit
    finally:
        logger.info("%s messages delivered", count)
    return count
