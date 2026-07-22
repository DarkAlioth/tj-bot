import datetime
import html
import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.services.jackett import JackettClient, JackettError

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


@admin_info_router.message(Command("indexers"))
async def show_indexers(message: Message, jackett: JackettClient) -> None:
    try:
        indexers = await jackett.indexers()
    except JackettError:
        logger.exception("Failed to fetch indexers")
        await message.answer("Jackett недоступен 🛠")
        return
    if not indexers:
        await message.answer("Индексеры не настроены — откройте Jackett UI.")
        return
    lines = ["🧲 <b>Индексеры Jackett</b>"]
    for indexer in indexers:
        name = html.escape(str(indexer.get("name", "?")))
        error = indexer.get("last_error") or ""
        if error:
            error_text = html.escape(str(error))[:120]
            lines.append(f"⚠️ {name} — <i>{error_text}</i>")
        else:
            lines.append(f"✅ {name}")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
