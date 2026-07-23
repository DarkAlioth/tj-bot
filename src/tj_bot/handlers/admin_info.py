import datetime
import html
import logging
import shutil
from typing import Any

from aiogram import Bot, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.services import broadcaster
from tj_bot.services.formatting import (
    DOWNLOADING_STATES,
    format_size,
    format_speed,
    padded,
)
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)

admin_info_router = Router()
admin_info_router.message.filter(AdminOnly())

STATS_WINDOW_DAYS = 7


def format_uptime(started_at: datetime.datetime) -> str:
    delta = datetime.datetime.now(datetime.UTC) - started_at
    minutes, _ = divmod(int(delta.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


@admin_info_router.message(Command("stats"))
async def show_stats(
    message: Message, repo: TorrentRepo, started_at: datetime.datetime
) -> None:
    stats = await repo.get_stats(datetime.timedelta(days=STATS_WINDOW_DAYS))
    lines = [
        "📊 <b>Статистика</b>",
        f"Аптайм: {format_uptime(started_at)}",
        f"Торрентов в кэше: {stats.torrents}",
        f"Поисковых выдач: {stats.queries}",
        f"Поисков за {STATS_WINDOW_DAYS}д: {stats.events_window}"
        f" • Пользователей: {stats.users_window}",
    ]
    if stats.top_queries:
        lines.append(f"\n🔥 <b>Топ запросов за {STATS_WINDOW_DAYS}д:</b>")
        lines.extend(
            f"{i}. {html.escape(text)} — {count}"
            for i, (text, count) in enumerate(stats.top_queries, start=1)
        )
    await message.answer(padded("\n".join(lines)), parse_mode=ParseMode.HTML)


async def qbit_summary(qbit: QbittorrentClient | None) -> str:
    if qbit is None:
        return "⬇️ qBittorrent: не настроен"
    try:
        torrents = await qbit.list_torrents()
        transfer = await qbit.transfer_info()
        free = await qbit.free_space()
    except QbittorrentError:
        logger.exception("qBittorrent health probe failed")
        return "⬇️ qBittorrent: недоступен 🛠"
    active = sum(1 for t in torrents if t.get("state") in DOWNLOADING_STATES)
    free_text = format_size(free) if free >= 0 else "н/д"
    return (
        f"⬇️ qBittorrent: {active} акт. / {len(torrents)} всего"
        f" · ↓ {format_speed(transfer.get('dl_info_speed', 0))}"
        f" ↑ {format_speed(transfer.get('up_info_speed', 0))}"
        f" · свободно {free_text}"
    )


def indexer_summary(indexers: list[dict[str, Any]]) -> list[str]:
    healthy = sum(1 for i in indexers if not i.get("Error"))
    lines = [f"🧲 Индексеры: {healthy}/{len(indexers)} в строю"]
    lines.extend(
        f"⚠️ {html.escape(str(i.get('Name', '?')))}" for i in indexers if i.get("Error")
    )
    return lines


@admin_info_router.message(Command("health"))
async def show_health(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    started_at: datetime.datetime,
) -> None:
    """One-message service overview; the slow indexer probe lands via edit."""
    db_size = await repo.db_size()
    disk = shutil.disk_usage("/")
    lines = [
        "🩺 <b>Состояние сервиса</b>",
        f"⏱ Аптайм: {format_uptime(started_at)}",
        f"🗄 БД: ok · {format_size(db_size)}",
        f"💽 Диск бота: свободно {format_size(disk.free)}",
        await qbit_summary(qbit),
    ]
    status = await message.answer(
        padded("\n".join([*lines, "🧲 Индексеры: проверяю…"])),
        parse_mode=ParseMode.HTML,
    )
    try:
        indexers = await jackett.indexers()
    except JackettError:
        logger.exception("Indexer probe failed for /health")
        indexer_lines = ["🧲 Индексеры: Jackett недоступен 🛠"]
    else:
        indexer_lines = (
            indexer_summary(indexers) if indexers else ["🧲 Индексеры: не настроены"]
        )
    await status.edit_text(
        padded("\n".join([*lines, *indexer_lines])), parse_mode=ParseMode.HTML
    )


class Bcast(CallbackData, prefix="bc"):
    """Confirm or cancel a pending broadcast draft."""

    a: str


@admin_info_router.message(Command("broadcast"))
async def broadcast_draft(message: Message, command: CommandObject) -> None:
    """Show the exact message users will get, with confirm/cancel buttons."""
    if not command.args:
        await message.answer(
            padded("Использование: <code>/broadcast текст рассылки</code>"),
            parse_mode=ParseMode.HTML,
        )
        return
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="📢 Отправить всем", callback_data=Bcast(a="go").pack()
                ),
                types.InlineKeyboardButton(
                    text="❌ Отмена", callback_data=Bcast(a="no").pack()
                ),
            ]
        ]
    )
    await message.answer(html.escape(command.args), reply_markup=keyboard)


@admin_info_router.callback_query(Bcast.filter())
async def broadcast_confirm(
    query: CallbackQuery,
    callback_data: Bcast,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> None:
    message = query.message
    if not config.is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    if not isinstance(message, Message) or message.text is None:
        await query.answer()
        return
    if callback_data.a == "no":
        try:
            await message.delete()
        except TelegramAPIError:
            logger.debug("Broadcast draft already gone on cancel")
        await query.answer("Отменено")
        return
    # the draft message body IS the broadcast text — no extra storage needed
    text = message.html_text
    user_ids = await repo.active_user_ids()
    await query.answer("Отправляю…")
    delivered = await broadcaster.broadcast(bot, user_ids, text)
    await message.edit_text(
        padded(f"📢 Доставлено {delivered} из {len(user_ids)}:\n\n{text}"),
        parse_mode=ParseMode.HTML,
    )


@admin_info_router.message(Command("indexers"))
async def show_indexers(message: Message, jackett: JackettClient) -> None:
    status = await message.answer(padded("🧲 Проверяю индексеры…"))
    try:
        indexers = await jackett.indexers()
    except JackettError:
        logger.exception("Failed to fetch indexers")
        await status.edit_text(padded("Jackett недоступен 🛠"))
        return
    if not indexers:
        await status.edit_text(padded("Индексеры не настроены — откройте Jackett UI."))
        return
    healthy = sum(1 for i in indexers if not i.get("Error"))
    lines = [f"🧲 <b>Индексеры Jackett</b> — {healthy}/{len(indexers)} в строю\n"]
    for indexer in sorted(indexers, key=lambda i: bool(i.get("Error")), reverse=True):
        name = html.escape(str(indexer.get("Name", "?")))
        error = indexer.get("Error")
        if error:
            lines.append(f"⚠️ {name} — <i>{html.escape(str(error))[:120]}</i>")
        else:
            lines.append(f"✅ {name} · {indexer.get('Results', 0)}")
    await status.edit_text(padded("\n".join(lines)), parse_mode=ParseMode.HTML)
