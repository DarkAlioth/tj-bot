import asyncio
import logging
import re
import secrets
from collections.abc import Coroutine
from urllib.parse import unquote

from aiogram import Bot, F, Router, types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.handlers.user import Dlt
from tj_bot.services.download_watcher import watch_download
from tj_bot.services.formatting import DOWNLOADING_STATES
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


class Dlc(CallbackData, prefix="dlc"):
    """Control a running download by its tag (pause / resume / delete)."""

    a: str
    tag: str


def magnet_name(url: str) -> str:
    """Human-readable name from a magnet's dn= parameter, else a placeholder."""
    match = re.search(r"[?&]dn=([^&]+)", url)
    if match:
        return unquote(match.group(1))[:200]
    return "magnet-ссылка"


async def _start_progress(
    bot: Bot,
    qbit: QbittorrentClient,
    message: Message,
    tag: str,
    name: str,
    config: AppConfig,
) -> None:
    status = await message.answer(
        f"⬇️ Отправлено на сервер: <b>{name}</b>\nОжидаю прогресс…"
    )
    _spawn_watcher(
        watch_download(
            bot,
            qbit,
            chat_id=status.chat.id,
            message_id=status.message_id,
            tag=tag,
            name=name,
            keyboard_for_state=lambda state: download_keyboard(tag, state),
            poll_interval_seconds=config.settings.qbit_poll_interval_seconds,
            timeout_seconds=config.settings.qbit_watch_timeout_seconds,
        )
    )


def download_keyboard(tag: str, state: str) -> InlineKeyboardMarkup:
    downloading = state in DOWNLOADING_STATES
    toggle = (
        types.InlineKeyboardButton(
            text="⏸ Пауза", callback_data=Dlc(a="pause", tag=tag).pack()
        )
        if downloading
        else types.InlineKeyboardButton(
            text="▶️ Старт", callback_data=Dlc(a="resume", tag=tag).pack()
        )
    )
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                toggle,
                types.InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=Dlc(a="delete", tag=tag).pack()
                ),
            ]
        ]
    )


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
    await _start_progress(bot, qbit, message, tag, torrent.title, config)


@admin_router.callback_query(Dlc.filter())
async def control_download(
    query: CallbackQuery,
    callback_data: Dlc,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    if not config.is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    if qbit is None:
        await query.answer()
        return
    torrents = await qbit.torrents_by_tag(callback_data.tag)
    hashes = "|".join(t["hash"] for t in torrents if t.get("hash"))
    if not hashes:
        await query.answer("Загрузка не найдена")
        return
    try:
        if callback_data.a == "pause":
            await qbit.stop_torrents(hashes)
            await query.answer("Поставлено на паузу")
        elif callback_data.a == "resume":
            await qbit.start_torrents(hashes)
            await query.answer("Возобновлено")
        elif callback_data.a == "delete":
            await qbit.delete_torrents(hashes, delete_files=True)
            await query.answer("Удалено вместе с файлами")
    except QbittorrentError:
        logger.exception("qBittorrent control %r failed", callback_data.a)
        await query.answer("qBittorrent недоступен", show_alert=True)


@admin_router.message(AdminOnly(), F.text.startswith("magnet:"))
async def add_magnet(
    message: Message,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    bot: Bot,
) -> None:
    if qbit is None:
        await message.answer("qBittorrent не настроен.")
        return
    url = (message.text or "").strip()
    name = magnet_name(url)
    tag = f"tjbot-{secrets.token_hex(6)}"
    try:
        await qbit.add_torrent_url(url, tag=tag, category=config.settings.qbit_category)
    except QbittorrentError:
        logger.exception("Failed to add magnet to qBittorrent")
        await message.answer("qBittorrent недоступен 🛠")
        return
    if message.from_user is not None:
        await repo.record_download(message.from_user.id, name, "server")
    await _start_progress(bot, qbit, message, tag, name, config)
