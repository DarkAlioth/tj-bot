import asyncio
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
from tj_bot.filters.admin import AdminOnly
from tj_bot.handlers.user import Dlt, category_token
from tj_bot.services.download_watcher import watch_download
from tj_bot.services.formatting import DOWNLOADING_STATES, format_size
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

# Dsc.t special values; real category tokens are 8 hex chars, so no collisions
NO_CATEGORY = "-"
CANCEL = "x"
KIND_TORRENT = "t"
KIND_MAGNET = "m"
KIND_FAVORITE = "f"


def _spawn_watcher(coro: Coroutine[object, object, None]) -> None:
    task = asyncio.create_task(coro)
    _watchers.add(task)
    task.add_done_callback(_watchers.discard)


class Dlc(CallbackData, prefix="dlc"):
    """Control a running download by its tag (pause / resume / delete)."""

    a: str
    tag: str


class Dsc(CallbackData, prefix="dsc"):
    """Category choice for a pending send-to-server download."""

    k: str  # KIND_TORRENT: hash points at torrents, KIND_MAGNET: magnet_links
    t: str  # category token, NO_CATEGORY or CANCEL
    hash: str


def magnet_name(url: str) -> str:
    """Human-readable name from a magnet's dn= parameter, else a placeholder."""
    match = re.search(r"[?&]dn=([^&]+)", url)
    if match:
        return unquote(match.group(1))[:200]
    return "magnet-ссылка"


def _category_names(categories: dict[str, Any], default: str | None) -> list[str]:
    """Menu order: the configured default category first, rest alphabetically.

    The default is always present even before qBittorrent auto-creates it on
    the first download.
    """
    names = sorted(name for name in categories if name != default)
    return [default, *names] if default else names


async def _resolve_category(
    qbit: QbittorrentClient, config: AppConfig, token: str
) -> str | None:
    """Map a callback token back to the category name; None means no category.

    Raises LookupError when the category no longer exists in qBittorrent.
    """
    if token == NO_CATEGORY:
        return None
    names = _category_names(await qbit.categories(), config.settings.qbit_category)
    for name in names:
        if category_token(name) == token:
            return name
    raise LookupError(token)


async def send_category_menu(
    message: Message,
    qbit: QbittorrentClient,
    config: AppConfig,
    kind: str,
    item_hash: str,
    name: str,
    size: int | None,
) -> None:
    categories = await qbit.categories()
    free = await qbit.free_space()
    default = config.settings.qbit_category

    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for category in _category_names(categories, default):
        label = f"⭐ {category}" if category == default else category
        row.append(
            types.InlineKeyboardButton(
                text=label,
                callback_data=Dsc(
                    k=kind, t=category_token(category), hash=item_hash
                ).pack(),
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            types.InlineKeyboardButton(
                text="📂 Без категории",
                callback_data=Dsc(k=kind, t=NO_CATEGORY, hash=item_hash).pack(),
            ),
            types.InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=Dsc(k=kind, t=CANCEL, hash=item_hash).pack(),
            ),
        ]
    )

    lines = [f"📤 <b>{name}</b>"]
    if size is not None:
        lines.append(f"Размер: <code>{format_size(size)}</code>")
    free_text = format_size(free) if free >= 0 else "н/д"
    lines.append(f"💾 Свободно на диске: <code>{free_text}</code>")
    if size is not None and 0 <= free < size:
        lines.append("\n⚠️ <b>Места на диске может не хватить!</b>")
    lines.append("\nВыберите категорию:")
    await message.answer(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _start_progress(
    bot: Bot,
    qbit: QbittorrentClient,
    status: Message,
    tag: str,
    name: str,
    config: AppConfig,
) -> None:
    """Turn ``status`` into the live progress message and start the watcher."""
    try:
        await status.edit_text(
            f"⬇️ Отправлено на сервер: <b>{name}</b>\nОжидаю прогресс…"
        )
    except TelegramAPIError:
        # torrent is already added; progress edits are best-effort like the watcher
        logger.debug("Progress init edit failed for %s", name)
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
    qbit: QbittorrentClient | None,
    config: AppConfig,
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
        await send_category_menu(
            message,
            qbit,
            config,
            kind=KIND_TORRENT,
            item_hash=torrent.hash,
            name=torrent.title,
            size=torrent.size,
        )
    except QbittorrentError:
        logger.exception("Failed to load qBittorrent categories")
        await query.answer("qBittorrent недоступен", show_alert=True)
        return
    await query.answer()


@admin_router.callback_query(Dsc.filter())
async def choose_category(
    query: CallbackQuery,
    callback_data: Dsc,
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
    if not isinstance(message, Message):
        await query.answer()
        return
    if callback_data.t == CANCEL:
        try:
            await message.delete()
        except TelegramAPIError:
            logger.debug("Category menu already gone on cancel")
        await query.answer("Отменено")
        return
    if qbit is None:
        await query.answer()
        return

    try:
        category = await _resolve_category(qbit, config, callback_data.t)
    except QbittorrentError:
        logger.exception("Failed to resolve qBittorrent category")
        await query.answer("qBittorrent недоступен", show_alert=True)
        return
    except LookupError:
        await query.answer(
            "Категория не найдена, откройте меню заново", show_alert=True
        )
        return

    tag = f"tjbot-{secrets.token_hex(6)}"
    if callback_data.k == KIND_MAGNET:
        url = await repo.get_magnet_url(callback_data.hash)
        if url is None:
            await query.answer(
                "Ссылка устарела, отправьте magnet заново", show_alert=True
            )
            return
        name = magnet_name(url)
        try:
            await qbit.add_torrent_url(url, tag=tag, category=category)
        except QbittorrentError:
            logger.exception("Failed to add magnet to qBittorrent")
            await query.answer("qBittorrent недоступен", show_alert=True)
            return
    else:
        if callback_data.k == KIND_FAVORITE:
            # int() guards the boundary: callback data can be forged by clients
            try:
                favorite_id = int(callback_data.hash)
            except ValueError:
                await query.answer()
                return
            favorite = await repo.get_favorite(query.from_user.id, favorite_id)
            if favorite is None:
                await query.answer("Записи больше нет в избранном", show_alert=True)
                return
            name, download_url = favorite.title, favorite.download_url
        else:
            torrent = await repo.get_torrent_by_hash(callback_data.hash)
            if torrent is None:
                await query.answer("Торрент устарел, повторите поиск", show_alert=True)
                return
            name, download_url = torrent.title, torrent.download_url
        try:
            content = await jackett.download(download_url)
        except DownloadTooLargeError:
            await query.answer("Файл слишком большой", show_alert=True)
            return
        except JackettError:
            logger.exception(
                "Failed to fetch %r for server download (%s)", name, callback_data.k
            )
            await query.answer("Не удалось получить файл с трекера", show_alert=True)
            return
        try:
            await qbit.add_torrent_file(
                content, safe_torrent_filename(name), tag=tag, category=category
            )
        except QbittorrentError:
            logger.exception("Failed to add %r to qBittorrent", name)
            await query.answer("qBittorrent недоступен", show_alert=True)
            return

    await repo.record_download(query.from_user.id, name, "server")
    await query.answer("Добавлено в загрузки ⬇️")
    await _start_progress(bot, qbit, message, tag, name, config)


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
) -> None:
    if qbit is None:
        await message.answer("qBittorrent не настроен.")
        return
    url = (message.text or "").strip()
    magnet_hash = await repo.save_magnet(url)
    try:
        await send_category_menu(
            message,
            qbit,
            config,
            kind=KIND_MAGNET,
            item_hash=magnet_hash,
            name=magnet_name(url),
            size=None,
        )
    except QbittorrentError:
        logger.exception("Failed to load qBittorrent categories")
        await message.answer("qBittorrent недоступен 🛠")
