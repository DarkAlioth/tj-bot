import asyncio
import logging
from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

from tj_bot.services.formatting import padded, torrent_progress_text
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)


async def _edit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.edit_message_text(
            padded(text),
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except TelegramAPIError:
        # message unchanged / deleted / not modified — safe to ignore
        logger.debug("Progress edit skipped for chat %s", chat_id)


async def watch_download(
    bot: Bot,
    qbit: QbittorrentClient,
    chat_id: int,
    message_id: int,
    tag: str,
    name: str,
    keyboard_for_state: Callable[[str], InlineKeyboardMarkup],
    poll_interval_seconds: int,
    timeout_seconds: int,
) -> None:
    """Edit the status message with live progress until the download finishes.

    Best-effort and transient: watchers do not survive a bot restart.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        await asyncio.sleep(poll_interval_seconds)
        try:
            torrents = await qbit.torrents_by_tag(tag)
        except QbittorrentError:
            logger.warning("Progress poll failed for %s", name)
            continue
        if not torrents:
            await _edit(bot, chat_id, message_id, f"🗑 Удалён: <b>{name}</b>")
            return
        torrent = torrents[0]
        if torrent.get("progress", 0) >= 1.0:
            await _edit(
                bot, chat_id, message_id, f"✅ Загрузка завершена: <b>{name}</b>"
            )
            return
        await _edit(
            bot,
            chat_id,
            message_id,
            torrent_progress_text(torrent, name),
            keyboard_for_state(torrent.get("state", "")),
        )
    logger.info("Stopped watching %s after timeout", name)
