import datetime
import html
import logging
import shutil
from typing import Any

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.services.formatting import DOWNLOADING_STATES, format_size, format_speed
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
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


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
        "\n".join([*lines, "🧲 Индексеры: проверяю…"]), parse_mode=ParseMode.HTML
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
        "\n".join([*lines, *indexer_lines]), parse_mode=ParseMode.HTML
    )


@admin_info_router.message(Command("indexers"))
async def show_indexers(message: Message, jackett: JackettClient) -> None:
    status = await message.answer("🧲 Проверяю индексеры…")
    try:
        indexers = await jackett.indexers()
    except JackettError:
        logger.exception("Failed to fetch indexers")
        await status.edit_text("Jackett недоступен 🛠")
        return
    if not indexers:
        await status.edit_text("Индексеры не настроены — откройте Jackett UI.")
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
    await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
