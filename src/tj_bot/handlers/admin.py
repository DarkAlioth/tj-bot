import asyncio
import logging
import secrets
from collections.abc import Coroutine

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.user import Dlt
from tj_bot.services.download_watcher import watch_completion
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    safe_torrent_filename,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)

admin_router = Router()

# Keep strong references to running watchers so the event loop does not GC them
# mid-flight (asyncio holds only weak references to tasks).
_watchers: set[asyncio.Task[None]] = set()


def _spawn_watcher(coro: Coroutine[object, object, None]) -> None:
    task = asyncio.create_task(coro)
    _watchers.add(task)
    task.add_done_callback(_watchers.discard)


@admin_router.callback_query(Dlt.filter(F.type == "server"))
async def send_to_server(
    query: CallbackQuery,
    callback_data: Dlt,
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    bot: Bot,
) -> None:
    message = query.message
    if not config.is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    if qbit is None or not isinstance(message, Message):
        await query.answer()
        return

    torrent = await repo.get_torrent_by_hash(callback_data.hash)
    if torrent is None:
        await query.answer()
        return

    try:
        content = await jackett.download(torrent.download_url)
    except DownloadTooLargeError:
        await query.answer("Файл слишком большой", show_alert=True)
        return
    except JackettError:
        logger.exception("Failed to fetch torrent %s for server download", torrent.hash)
        await query.answer("Не удалось получить файл с трекера", show_alert=True)
        return

    tag = f"tjbot-{secrets.token_hex(6)}"
    filename = safe_torrent_filename(torrent.title)
    try:
        await qbit.add_torrent_file(
            content, filename, tag=tag, category=config.settings.qbit_category
        )
    except QbittorrentError:
        logger.exception("Failed to add torrent %s to qBittorrent", torrent.hash)
        await query.answer("qBittorrent недоступен", show_alert=True)
        return

    await repo.record_download(query.from_user.id, torrent.title, "server")
    await query.answer("Добавлено в загрузки ⬇️")
    await message.answer(
        f"⬇️ Отправлено на сервер: <b>{torrent.title}</b>\n"
        "Уведомлю, когда загрузка завершится."
    )
    _spawn_watcher(
        watch_completion(
            bot,
            qbit,
            chat_id=message.chat.id,
            tag=tag,
            name=torrent.title,
            poll_interval_seconds=config.settings.qbit_poll_interval_seconds,
            timeout_seconds=config.settings.qbit_watch_timeout_seconds,
        )
    )
