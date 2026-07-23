import html
import logging
import math
from typing import Any

from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message

from tj_bot.filters.admin import AdminOnly
from tj_bot.services.formatting import (  # noqa: I001
    format_eta,
    format_size,
    format_speed,
    padded,
    progress_bar,
    state_view,
)
from tj_bot.services.qbittorrent import (
    QbittorrentClient,
    QbittorrentError,
    QueueingDisabledError,
)

logger = logging.getLogger(__name__)

qbit_router = Router()

PAGE_SIZE = 6
NAME_LIMIT = 34
UNAVAILABLE_TEXT = "qBittorrent недоступен 🛠"


qbit_router.message.filter(AdminOnly())
qbit_router.callback_query.filter(AdminOnly())


class Qbm(CallbackData, prefix="qbm"):
    a: str
    h: str
    p: int
    f: str


FILTER_LABELS = {"all": "Все", "dl": "⬇️", "up": "⬆️", "stop": "⏸"}

DL_STATES = {
    "downloading",
    "stalledDL",
    "queuedDL",
    "metaDL",
    "forcedDL",
    "checkingDL",
    "allocating",
}
UP_STATES = {"uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP"}
STOP_STATES = {"stoppedDL", "stoppedUP", "pausedDL", "pausedUP"}


def matches_filter(torrent: dict[str, Any], key: str) -> bool:
    state = torrent.get("state", "")
    if key == "dl":
        return state in DL_STATES
    if key == "up":
        return state in UP_STATES
    if key == "stop":
        return state in STOP_STATES
    return True


def short_name(name: str) -> str:
    return name if len(name) <= NAME_LIMIT else name[: NAME_LIMIT - 1] + "…"


def build_list_view(
    torrents: list[dict[str, Any]],
    transfer: dict[str, Any],
    alt_on: bool,
    page: int,
    fkey: str,
) -> tuple[str, types.InlineKeyboardMarkup, int]:
    filtered = [t for t in torrents if matches_filter(t, fkey)]
    pages_total = max(1, math.ceil(len(filtered) / PAGE_SIZE))
    page = max(0, min(page, pages_total - 1))
    visible = filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    counts = {
        key: sum(1 for t in torrents if matches_filter(t, key)) for key in FILTER_LABELS
    }
    dl_speed = format_speed(transfer.get("dl_info_speed", 0))
    up_speed = format_speed(transfer.get("up_info_speed", 0))
    text_lines = [
        "🖥 <b>qBittorrent</b>",
        "⠀",
        f"⬇️ {dl_speed} • ⬆️ {up_speed} • 🐢 {'вкл' if alt_on else 'выкл'}",
        f"Торрентов: {counts['all']} • Загружается: {counts['dl']} "
        f"• Раздаётся: {counts['up']} • Пауза: {counts['stop']}",
    ]
    if not filtered:
        text_lines.append("\nСписок пуст.")
    elif pages_total > 1:
        text_lines.append(f"\nСтраница {page + 1}/{pages_total}")

    rows: list[list[types.InlineKeyboardButton]] = []
    for torrent in visible:
        emoji, _ = state_view(torrent.get("state", ""))
        label = (
            f"{emoji} {torrent.get('progress', 0) * 100:.0f}% "
            f"{short_name(torrent.get('name', '?'))}"
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=label,
                    callback_data=Qbm(
                        a="card", h=torrent.get("hash", ""), p=page, f=fkey
                    ).pack(),
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text=("· " + label + " ·") if key == fkey else label,
                callback_data=Qbm(a="ls", h="", p=0, f=key).pack(),
            )
            for key, label in FILTER_LABELS.items()
        ]
    )
    nav = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="⬅", callback_data=Qbm(a="ls", h="", p=page - 1, f=fkey).pack()
            )
        )
    nav.append(
        types.InlineKeyboardButton(
            text="↻", callback_data=Qbm(a="ls", h="", p=page, f=fkey).pack()
        )
    )
    if page + 1 < pages_total:
        nav.append(
            types.InlineKeyboardButton(
                text="➡", callback_data=Qbm(a="ls", h="", p=page + 1, f=fkey).pack()
            )
        )
    rows.append(nav)
    rows.append(
        [
            types.InlineKeyboardButton(
                text="⏸ Все",
                callback_data=Qbm(a="stopall", h="", p=page, f=fkey).pack(),
            ),
            types.InlineKeyboardButton(
                text="▶️ Все",
                callback_data=Qbm(a="startall", h="", p=page, f=fkey).pack(),
            ),
            types.InlineKeyboardButton(
                text="🐢 Лимит", callback_data=Qbm(a="alt", h="", p=page, f=fkey).pack()
            ),
        ]
    )
    return "\n".join(text_lines), types.InlineKeyboardMarkup(inline_keyboard=rows), page


def build_card_view(
    torrent: dict[str, Any], page: int, fkey: str
) -> tuple[str, types.InlineKeyboardMarkup]:
    state = torrent.get("state", "")
    emoji, state_name = state_view(state)
    name = html.escape(torrent.get("name", "?"))
    size = torrent.get("size", 0)
    done = torrent.get("completed", int(torrent.get("progress", 0) * size))
    text = "\n".join(
        [
            f"📄 <b>{name}</b>",
            "⠀",
            "",
            progress_bar(torrent.get("progress", 0)),
            f"Статус: {emoji} {state_name} • ETA: {format_eta(torrent.get('eta', -1))}",
            f"Размер: {format_size(size)} • Готово: {format_size(done)}",
            f"⬇️ {format_speed(torrent.get('dlspeed', 0))} "
            f"• ⬆️ {format_speed(torrent.get('upspeed', 0))}",
            f"Сиды/Пиры: {torrent.get('num_seeds', 0)}/{torrent.get('num_leechs', 0)} "
            f"• Ratio: {torrent.get('ratio', 0):.2f}",
            f"Категория: {html.escape(torrent.get('category') or '—')}",
        ]
    )
    torrent_hash = torrent.get("hash", "")
    stopped = state in STOP_STATES

    def btn(text_label: str, action: str) -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton(
            text=text_label,
            callback_data=Qbm(a=action, h=torrent_hash, p=page, f=fkey).pack(),
        )

    rows = [
        [
            btn("▶️ Старт", "start") if stopped else btn("⏸ Пауза", "stop"),
            btn("🚀 Force", "force"),
        ],
        [btn("⏫", "ptop"), btn("🔼", "pup"), btn("🔽", "pdn"), btn("⏬", "pbot")],
        [btn("🗑 Удалить", "del")],
        [btn("↻", "card"), btn("◀️ К списку", "ls")],
    ]
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


async def render_list(
    message: Message, qbit: QbittorrentClient, page: int, fkey: str, edit: bool
) -> None:
    torrents = await qbit.list_torrents()
    transfer = await qbit.transfer_info()
    alt_on = await qbit.alt_speed_enabled()
    text, keyboard, _ = build_list_view(torrents, transfer, alt_on, page, fkey)
    text = padded(text)
    if edit:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def render_card(
    message: Message, qbit: QbittorrentClient, torrent_hash: str, page: int, fkey: str
) -> bool:
    torrent = await qbit.torrent_info(torrent_hash)
    if torrent is None:
        return False
    text, keyboard = build_card_view(torrent, page, fkey)
    await message.edit_text(
        padded(text), parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
    return True


@qbit_router.message(Command("dl"))
async def qbit_menu(message: Message, qbit: QbittorrentClient | None) -> None:
    if qbit is None:
        await message.answer(padded("qBittorrent не настроен (см. QBIT_* в .env)."))
        return
    try:
        await render_list(message, qbit, 0, "all", edit=False)
    except QbittorrentError:
        logger.exception("Failed to render qBittorrent list")
        await message.answer(padded(UNAVAILABLE_TEXT))


@qbit_router.callback_query(Qbm.filter())
async def qbit_actions(
    query: CallbackQuery, callback_data: Qbm, qbit: QbittorrentClient | None
) -> None:
    message = query.message
    if qbit is None or not isinstance(message, Message):
        await query.answer()
        return
    action = callback_data.a
    torrent_hash = callback_data.h
    page, fkey = callback_data.p, callback_data.f
    try:
        if action == "ls":
            await query.answer()
            await render_list(message, qbit, page, fkey, edit=True)
        elif action == "card":
            await query.answer()
            if not await render_card(message, qbit, torrent_hash, page, fkey):
                await render_list(message, qbit, page, fkey, edit=True)
        elif action in {"stop", "start", "force"}:
            if action == "stop":
                await qbit.stop_torrents(torrent_hash)
            elif action == "start":
                await qbit.start_torrents(torrent_hash)
            else:
                await qbit.set_force_start(torrent_hash, True)
            await query.answer("Готово")
            await render_card(message, qbit, torrent_hash, page, fkey)
        elif action in {"ptop", "pup", "pdn", "pbot"}:
            api_action = {
                "ptop": "topPrio",
                "pup": "increasePrio",
                "pdn": "decreasePrio",
                "pbot": "bottomPrio",
            }[action]
            try:
                await qbit.change_priority(api_action, torrent_hash)
            except QueueingDisabledError:
                await query.answer(
                    "Очередь отключена в настройках qBittorrent", show_alert=True
                )
                return
            await query.answer("Готово")
            await render_card(message, qbit, torrent_hash, page, fkey)
        elif action == "del":
            torrent = await qbit.torrent_info(torrent_hash)
            if torrent is None:
                await query.answer()
                await render_list(message, qbit, page, fkey, edit=True)
                return
            name = html.escape(torrent.get("name", "?"))
            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="🗑 С файлами",
                            callback_data=Qbm(
                                a="delf", h=torrent_hash, p=page, f=fkey
                            ).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="📄 Только торрент",
                            callback_data=Qbm(
                                a="delt", h=torrent_hash, p=page, f=fkey
                            ).pack(),
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="◀️ Отмена",
                            callback_data=Qbm(
                                a="card", h=torrent_hash, p=page, f=fkey
                            ).pack(),
                        )
                    ],
                ]
            )
            await query.answer()
            await message.edit_text(
                padded(f"Удалить <b>{name}</b>?"),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        elif action in {"delf", "delt"}:
            await qbit.delete_torrents(torrent_hash, delete_files=action == "delf")
            await query.answer("Удалено")
            await render_list(message, qbit, page, fkey, edit=True)
        elif action == "stopall":
            await qbit.stop_torrents("all")
            await query.answer("Все на паузе")
            await render_list(message, qbit, page, fkey, edit=True)
        elif action == "startall":
            await qbit.start_torrents("all")
            await query.answer("Все запущены")
            await render_list(message, qbit, page, fkey, edit=True)
        elif action == "alt":
            await qbit.toggle_alt_speed()
            await query.answer("Лимит скорости переключён")
            await render_list(message, qbit, page, fkey, edit=True)
        else:
            await query.answer()
    except QbittorrentError:
        logger.exception("qBittorrent console action %r failed", action)
        await query.answer(UNAVAILABLE_TEXT, show_alert=True)
