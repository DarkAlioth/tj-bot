import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)


async def watch_completion(
    bot: Bot,
    qbit: QbittorrentClient,
    chat_id: int,
    tag: str,
    name: str,
    poll_interval_seconds: int,
    timeout_seconds: int,
) -> None:
    """Poll qBittorrent until the tagged torrent finishes, then notify the admin.

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
        if torrents and all(t.get("progress", 0) >= 1.0 for t in torrents):
            try:
                await bot.send_message(chat_id, f"✅ Загрузка завершена: {name}")
            except TelegramAPIError:
                logger.exception("Failed to send completion notice for %s", name)
            return
    logger.info("Stopped watching %s after timeout", name)
