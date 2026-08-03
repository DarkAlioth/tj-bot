"""Send-to-server flow: add stopped, tick the wanted files, then start.

The torrent is added to qBittorrent stopped, with the default category from
the settings applied silently. The file picker state lives in qBittorrent
itself (file priorities of the stopped torrent), so the bot stays stateless
and the picker keeps working across bot restarts and redeploys.
"""

import asyncio
import html
import logging
import re
import secrets
from collections.abc import Coroutine
from typing import Any
from urllib.parse import unquote

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.services.download_watcher import watch_download
from tj_bot.services.feedback import ack_silent, alert_or_message
from tj_bot.services.formatting import DOWNLOADING_STATES, format_size, padded
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    safe_torrent_filename,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)

server_download_router = Router()

# Keep strong references to running watchers so the event loop does not GC
# them mid-flight (asyncio holds only weak references to tasks).
_watchers: set[asyncio.Task[None]] = set()

FILE_PAGE_SIZE = 8
# a stopped magnet never fetches metadata, so it is added running with this
# stop condition and force-stopped once the file list appears
STOP_AFTER_METADATA = "MetadataReceived"
METADATA_TIMEOUT_SECONDS = 90
METADATA_POLL_SECONDS = 2.0
TAG_PREFIX = "tjbot-"

QBIT_DOWN_TEXT = "qBittorrent недоступен"
GONE_TEXT = "Загрузка не найдена — возможно, удалена из qBittorrent"


class Fs(CallbackData, prefix="fs"):
    """File picker actions; ``h`` is the qBittorrent torrent hash."""

    a: str  # t=toggle file, p=page, all=select all/none, go=start, x=cancel
    h: str
    v: int  # file index for t, page for p, 1/0 for all
    p: int  # page the button was rendered on


class Dlc(CallbackData, prefix="dlc"):
    """Control a running download by its tag (pause / resume / delete)."""

    a: str
    tag: str


def _spawn_watcher(coro: Coroutine[object, object, None]) -> None:
    task = asyncio.create_task(coro)
    _watchers.add(task)
    task.add_done_callback(_watchers.discard)


def magnet_name(url: str) -> str:
    """Human-readable name from a magnet's dn= parameter, else a placeholder."""
    match = re.search(r"[?&]dn=([^&]+)", url)
    if match:
        return unquote(match.group(1))[:200]
    return "magnet-ссылка"


def new_tag() -> str:
    return f"{TAG_PREFIX}{secrets.token_hex(6)}"


def tag_of(info: dict[str, Any]) -> str | None:
    """Our watcher tag from a torrent's comma-separated tag list."""
    for tag in str(info.get("tags") or "").split(","):
        if tag.strip().startswith(TAG_PREFIX):
            return tag.strip()
    return None


def file_display_name(path: str) -> str:
    name = html.unescape(path).rsplit("/", 1)[-1]
    if len(name) > 40:
        name = name[:19] + "…" + name[-20:]
    return name


def build_selection_view(
    torrent_hash: str,
    name: str,
    files: list[dict[str, Any]],
    free_space: int,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """Pure renderer of the file picker screen for a stopped torrent."""
    pages = max(1, -(-len(files) // FILE_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    selected = [f for f in files if int(f.get("priority") or 0) > 0]
    total_size = sum(int(f.get("size") or 0) for f in files)
    selected_size = sum(int(f.get("size") or 0) for f in selected)

    def cb(action: str, value: int) -> str:
        return Fs(a=action, h=torrent_hash, v=value, p=page).pack()

    lines = [
        f"📤 <b>{html.escape(name)}</b>",
        "⠀",
        f"Выбрано: <b>{len(selected)}</b> из {len(files)} файлов · "
        f"<code>{format_size(selected_size)}</code> из "
        f"<code>{format_size(total_size)}</code>",
    ]
    free_text = format_size(free_space) if free_space >= 0 else "н/д"
    lines.append(f"💾 Свободно на диске: <code>{free_text}</code>")
    if 0 <= free_space < selected_size:
        lines.append("\n⚠️ <b>Места на диске может не хватить!</b>")
    lines.append("⠀")
    window = files[page * FILE_PAGE_SIZE : (page + 1) * FILE_PAGE_SIZE]
    for offset, file in enumerate(window):
        index = page * FILE_PAGE_SIZE + offset
        mark = "✅" if int(file.get("priority") or 0) > 0 else "▫️"
        display = html.escape(file_display_name(str(file.get("name") or "?")))
        lines.append(
            f"{mark} {index + 1}. {display} — {format_size(int(file.get('size') or 0))}"
        )

    toggle_rows = [
        [
            types.InlineKeyboardButton(
                text=("✅" if int(window[i].get("priority") or 0) > 0 else "▫️")
                + f" {page * FILE_PAGE_SIZE + i + 1}",
                callback_data=cb("t", page * FILE_PAGE_SIZE + i),
            )
            for i in range(start, min(start + 4, len(window)))
        ]
        for start in range(0, len(window), 4)
    ]
    rows = [*toggle_rows]
    rows.append(
        [
            types.InlineKeyboardButton(text="✅ Все", callback_data=cb("all", 1)),
            types.InlineKeyboardButton(text="▫️ Ничего", callback_data=cb("all", 0)),
        ]
    )
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(text="⬅", callback_data=cb("p", page - 1))
            )
        nav.append(
            types.InlineKeyboardButton(
                text=f"{page + 1}/{pages}", callback_data=cb("p", page)
            )
        )
        if page + 1 < pages:
            nav.append(
                types.InlineKeyboardButton(text="➡", callback_data=cb("p", page + 1))
            )
        rows.append(nav)
    rows.append(
        [
            types.InlineKeyboardButton(text="▶️ Начать", callback_data=cb("go", 0)),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data=cb("x", 0)),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def render_selection(
    target: Message,
    qbit: QbittorrentClient,
    torrent_hash: str,
    page: int,
) -> None:
    """Read the live picker state from qBittorrent and (re)draw the screen."""
    try:
        info = await qbit.torrent_info(torrent_hash)
        files = await qbit.torrent_files(torrent_hash)
        free = await qbit.free_space()
    except QbittorrentError:
        logger.exception("File picker state fetch failed")
        await _safe_edit(target, padded(QBIT_DOWN_TEXT + " 🛠"))
        return
    if info is None:
        await _safe_edit(target, padded(GONE_TEXT))
        return
    text, keyboard = build_selection_view(
        torrent_hash, str(info.get("name") or "?"), files, free, page
    )
    await _safe_edit(target, padded(text), keyboard)


async def _safe_edit(
    target: Message, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await target.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except TelegramAPIError:
        # an identical re-render (double tap) or a deleted message
        logger.debug("File picker edit skipped")


async def begin_torrent_send(
    query: CallbackQuery,
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient,
    config: AppConfig,
    name: str,
    download_url: str,
) -> None:
    """Fetch the .torrent, add it stopped and open the file picker."""
    # release the per-update transaction before slow tracker/qbit calls
    await repo.commit()
    try:
        content = await jackett.download(download_url)
    except DownloadTooLargeError:
        await alert_or_message(query, "Файл слишком большой")
        return
    except JackettError:
        logger.exception("Failed to fetch %r for the server download", name)
        await alert_or_message(query, "Не удалось получить файл с трекера")
        return
    tag = new_tag()
    try:
        await qbit.add_torrent_file(
            content,
            safe_torrent_filename(name),
            tag=tag,
            category=config.settings.qbit_category,
            paused=True,
        )
        torrents = await qbit.torrents_by_tag(tag)
    except QbittorrentError:
        logger.exception("Failed to add %r to qBittorrent", name)
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    if not torrents or not torrents[0].get("hash"):
        await alert_or_message(query, "qBittorrent не принял торрент")
        return
    await ack_silent(query)
    status = await message.answer(padded("Загружаю список файлов…"))
    await render_selection(status, qbit, str(torrents[0]["hash"]), page=0)


async def begin_magnet_send(
    message: Message,
    repo: TorrentRepo,
    qbit: QbittorrentClient,
    config: AppConfig,
    url: str,
) -> None:
    """Add a magnet, wait for its metadata and open the file picker."""
    tag = new_tag()
    await repo.commit()
    try:
        await qbit.add_torrent_url(
            url,
            tag=tag,
            category=config.settings.qbit_category,
            stop_condition=STOP_AFTER_METADATA,
        )
    except QbittorrentError:
        logger.exception("Failed to add a magnet to qBittorrent")
        await message.answer(padded(QBIT_DOWN_TEXT + " 🛠"))
        return
    status = await message.answer(
        padded(f"🧲 <b>{html.escape(magnet_name(url))}</b>\n⠀\nПолучаю метаданные…")
    )
    torrent_hash = await _wait_for_metadata(qbit, tag)
    if torrent_hash is None:
        await _safe_edit(
            status,
            padded("Не удалось получить метаданные magnet-ссылки, попробуйте позже."),
        )
        return
    try:
        # stopCondition already parked it; force-stop for older qBittorrent
        await qbit.stop_torrents(torrent_hash)
    except QbittorrentError:
        logger.debug("Force-stop after metadata failed", exc_info=True)
    await render_selection(status, qbit, torrent_hash, page=0)


async def _wait_for_metadata(qbit: QbittorrentClient, tag: str) -> str | None:
    """The magnet's torrent hash once its file list exists, None on timeout."""
    torrent_hash: str | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + METADATA_TIMEOUT_SECONDS
    while loop.time() < deadline:
        try:
            if torrent_hash is None:
                torrents = await qbit.torrents_by_tag(tag)
                if torrents and torrents[0].get("hash"):
                    torrent_hash = str(torrents[0]["hash"])
            if torrent_hash is not None:
                if await qbit.torrent_files(torrent_hash):
                    return torrent_hash
        except QbittorrentError:
            logger.debug("Metadata poll failed", exc_info=True)
        await asyncio.sleep(METADATA_POLL_SECONDS)
    if torrent_hash is not None:
        try:
            await qbit.delete_torrents(torrent_hash, delete_files=True)
        except QbittorrentError:
            logger.debug("Cleanup of a stale magnet failed", exc_info=True)
    return None


def _picker_context(
    query: CallbackQuery, config: AppConfig, qbit: QbittorrentClient | None
) -> tuple[QbittorrentClient, Message] | None:
    """Admin + configured qbit + editable message, or None to ignore the tap."""
    message = query.message
    if (
        not config.is_admin(query.from_user.id)
        or qbit is None
        or not isinstance(message, Message)
    ):
        return None
    return qbit, message


@server_download_router.callback_query(Fs.filter(F.a == "t"))
async def toggle_file(
    query: CallbackQuery,
    callback_data: Fs,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    ctx = _picker_context(query, config, qbit)
    if ctx is None:
        await query.answer()
        return
    client, message = ctx
    try:
        files = await client.torrent_files(callback_data.h)
        current = next(
            (
                int(f.get("priority") or 0)
                for f in files
                if int(f.get("index", -1)) == callback_data.v
            ),
            None,
        )
        if current is None:
            await query.answer(GONE_TEXT, show_alert=True)
            return
        await client.set_file_priority(
            callback_data.h, str(callback_data.v), 0 if current > 0 else 1
        )
    except QbittorrentError:
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    await query.answer()
    await render_selection(message, client, callback_data.h, callback_data.p)


@server_download_router.callback_query(Fs.filter(F.a == "all"))
async def select_all_files(
    query: CallbackQuery,
    callback_data: Fs,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    ctx = _picker_context(query, config, qbit)
    if ctx is None:
        await query.answer()
        return
    client, message = ctx
    try:
        files = await client.torrent_files(callback_data.h)
        if files:
            ids = "|".join(str(f.get("index", i)) for i, f in enumerate(files))
            await client.set_file_priority(
                callback_data.h, ids, 1 if callback_data.v else 0
            )
    except QbittorrentError:
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    await query.answer()
    await render_selection(message, client, callback_data.h, callback_data.p)


@server_download_router.callback_query(Fs.filter(F.a == "p"))
async def turn_file_page(
    query: CallbackQuery,
    callback_data: Fs,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    ctx = _picker_context(query, config, qbit)
    if ctx is None:
        await query.answer()
        return
    client, message = ctx
    await query.answer()
    await render_selection(message, client, callback_data.h, callback_data.v)


@server_download_router.callback_query(Fs.filter(F.a == "x"))
async def cancel_download(
    query: CallbackQuery,
    callback_data: Fs,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    ctx = _picker_context(query, config, qbit)
    if ctx is None:
        await query.answer()
        return
    client, message = ctx
    try:
        await client.delete_torrents(callback_data.h, delete_files=True)
    except QbittorrentError:
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    await query.answer("Отменено")
    await _safe_edit(message, padded("❌ Загрузка отменена."))


@server_download_router.callback_query(Fs.filter(F.a == "go"))
async def start_download(
    query: CallbackQuery,
    callback_data: Fs,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    bot: Bot,
) -> None:
    ctx = _picker_context(query, config, qbit)
    if ctx is None:
        await query.answer()
        return
    client, message = ctx
    try:
        info = await client.torrent_info(callback_data.h)
        files = await client.torrent_files(callback_data.h)
    except QbittorrentError:
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    if info is None:
        await query.answer(GONE_TEXT, show_alert=True)
        return
    if not any(int(f.get("priority") or 0) > 0 for f in files):
        await query.answer("Выберите хотя бы один файл", show_alert=True)
        return
    name = str(info.get("name") or "?")
    try:
        await client.start_torrents(callback_data.h)
    except QbittorrentError:
        await alert_or_message(query, QBIT_DOWN_TEXT)
        return
    await repo.record_download(query.from_user.id, name, "server")
    await ack_silent(query, "Загрузка запущена ⬇️")
    tag = tag_of(info)
    if tag is None:
        await _safe_edit(message, padded(f"⬇️ Запущено: <b>{html.escape(name)}</b>"))
        return
    await _start_progress(bot, client, message, tag, name, config)


async def _start_progress(
    bot: Bot,
    qbit: QbittorrentClient,
    status: Message,
    tag: str,
    name: str,
    config: AppConfig,
) -> None:
    """Turn ``status`` into the live progress message and start the watcher."""
    await _safe_edit(
        status, padded(f"⬇️ Отправлено на сервер: <b>{name}</b>\n⠀\nОжидаю прогресс…")
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                toggle,
                types.InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=Dlc(a="delete", tag=tag).pack()
                ),
            ]
        ]
    )


@server_download_router.callback_query(Dlc.filter())
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
        await alert_or_message(query, QBIT_DOWN_TEXT)
