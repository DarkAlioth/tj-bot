import datetime
import html
import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    LinkPreviewOptions,
)

from tj_bot.config import AppConfig
from tj_bot.db.models import Torrent
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.user import normalize_query

logger = logging.getLogger(__name__)

inline_router = Router()

MIN_QUERY_LENGTH = 3
RESULTS_LIMIT = 10
# a fresh in-bot search may land within this window — let clients re-ask soon
MISS_CACHE_SECONDS = 10
HIT_CACHE_SECONDS = 300


def inline_article(torrent: Torrent) -> InlineQueryResultArticle:
    """Shareable result card without buttons (callbacks need a chat message)."""
    size_gb = round(torrent.size / 1024 / 1024 / 1024, 2)
    published = torrent.published_at.strftime("%d.%m.%Y")
    # title/category/tracker/details_url are stored HTML-escaped
    lines = [
        f'<b>Название</b>: <a href="{torrent.details_url}">{torrent.title}</a>',
        "<b>Сиды</b> / <b>Пиры</b>: "
        f"<code>{torrent.seeders}</code> / <code>{torrent.peers}</code>",
        f"<b>Размер</b>: <code>{size_gb} GB</code>",
        f"<b>Категория</b>: <code>{torrent.category}</code>",
    ]
    if torrent.tracker:
        lines.append(f"<b>Трекер</b>: <code>{torrent.tracker}</code>")
    lines.append(f"<b>Дата публикации</b>: <code>{published}</code>")
    return InlineQueryResultArticle(
        id=torrent.hash,
        title=html.unescape(torrent.title),
        description=f"{size_gb} GB · 🌱 {torrent.seeders} · "
        f"{html.unescape(torrent.category)}",
        input_message_content=InputTextMessageContent(
            message_text="\n".join(lines),
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        ),
    )


@inline_router.inline_query()
async def inline_search(
    query: InlineQuery, repo: TorrentRepo, config: AppConfig
) -> None:
    """Serve cached search results inline; cold queries go to the bot chat.

    Jackett's aggregate search regularly exceeds Telegram's inline answer
    deadline, so inline mode only reads the cache and never hits trackers.
    """
    text = normalize_query(query.query)
    if len(text) < MIN_QUERY_LENGTH:
        await query.answer(
            [],
            cache_time=MISS_CACHE_SECONDS,
            button=InlineQueryResultsButton(
                text="Введите название (от 3 символов)", start_parameter="inline"
            ),
        )
        return
    cache_window = datetime.timedelta(seconds=config.settings.search_cache_seconds)
    recent = await repo.find_recent_search(text, cache_window)
    if recent is None:
        await query.answer(
            [],
            cache_time=MISS_CACHE_SECONDS,
            button=InlineQueryResultsButton(
                text="Нет в кэше — искать в боте", start_parameter="inline"
            ),
        )
        return
    torrents = await repo.get_result_list(recent.hash, RESULTS_LIMIT)
    await query.answer(
        [inline_article(torrent) for torrent in torrents],
        cache_time=HIT_CACHE_SECONDS,
        is_personal=False,
    )
