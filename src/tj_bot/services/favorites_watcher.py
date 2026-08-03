"""Watch favorited torrents for tracker-side updates (new episodes etc.).

A favorite stores a snapshot of the tracker topic. The watcher re-searches
the topic's own tracker, matches the result by ``details_url`` and, when the
size, date or title changed, refreshes the snapshot and notifies every owner
with download buttons.
"""

import asyncio
import html
import logging
import re

from aiogram import Bot, types
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.config import AppConfig
from tj_bot.db.models import Favorite
from tj_bot.db.repo import TorrentData, TorrentRepo
from tj_bot.handlers.favorites import Fav
from tj_bot.services import broadcaster
from tj_bot.services.formatting import format_size, padded
from tj_bot.services.jackett import JackettClient, JackettError

logger = logging.getLogger(__name__)

# pause between tracker searches so the watcher never hammers anyone
REQUEST_GAP_SECONDS = 5.0


def search_query_for(title: str) -> str:
    """Search text for a topic: unescape and drop bracketed counters.

    Trackers rename topics when episodes arrive ("[08/16]" -> "[09/16]"),
    so bracketed chunks cannot be part of the query.
    """
    text = re.sub(r"\[[^\]]*\]", " ", html.unescape(title))
    return " ".join(text.split())[:150]


def has_changed(favorite: Favorite, fresh: TorrentData) -> bool:
    return (
        fresh.size != favorite.size
        or fresh.published_at != favorite.published_at
        or fresh.title != favorite.title
    )


def build_notification(
    favorite: Favorite, fresh: TorrentData, is_admin: bool
) -> tuple[str, types.InlineKeyboardMarkup]:
    lines = [
        "⭐ <b>Обновление в избранном</b>",
        "⠀",
        f'<a href="{fresh.details_url}">{fresh.title}</a>',
        "⠀",
        f"Было: <code>{format_size(favorite.size)}</code>"
        f" · {favorite.published_at.strftime('%d.%m.%Y')}",
        f"Стало: <code>{format_size(fresh.size)}</code>"
        f" · {fresh.published_at.strftime('%d.%m.%Y')}",
    ]
    row = [
        types.InlineKeyboardButton(
            text="💾 Скачать", callback_data=Fav(a="dl", id=favorite.id).pack()
        )
    ]
    if is_admin:
        row.append(
            types.InlineKeyboardButton(
                text="⬇️ На сервер", callback_data=Fav(a="sv", id=favorite.id).pack()
            )
        )
    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=[row])


async def check_favorites_once(
    bot: Bot,
    jackett: JackettClient,
    session_pool: async_sessionmaker[AsyncSession],
    config: AppConfig,
) -> int:
    """One pass over all favorites; returns the number of updated topics."""
    async with session_pool() as session:
        favorites = await TorrentRepo(session).list_all_favorites()
    topics: dict[tuple[str, str], list[Favorite]] = {}
    for favorite in favorites:
        if not favorite.tracker or favorite.details_url in ("", "None"):
            continue
        key = (favorite.tracker, favorite.details_url)
        topics.setdefault(key, []).append(favorite)
    updated = 0
    for index, ((tracker, details_url), owners) in enumerate(topics.items()):
        if index:
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        try:
            items = await jackett.search_indexer(
                tracker, search_query_for(owners[0].title)
            )
        except JackettError:
            logger.debug("Favorites check search failed on %s", tracker, exc_info=True)
            continue
        fresh = next((item for item in items if item.details_url == details_url), None)
        if fresh is None or not has_changed(owners[0], fresh):
            continue
        try:
            async with session_pool() as session:
                repo = TorrentRepo(session)
                for favorite in owners:
                    await repo.update_favorite_snapshot(favorite.id, fresh)
                await session.commit()
        except SQLAlchemyError:
            logger.exception("Favorites snapshot update failed")
            continue
        updated += 1
        for favorite in owners:
            text, keyboard = build_notification(
                favorite, fresh, config.is_admin(favorite.user_id)
            )
            await broadcaster.send_message(
                bot, favorite.user_id, padded(text), reply_markup=keyboard
            )
    return updated


async def favorites_watch_loop(
    bot: Bot,
    jackett: JackettClient,
    session_pool: async_sessionmaker[AsyncSession],
    config: AppConfig,
    interval_seconds: int,
) -> None:
    """Sleep-first loop: a deploy restart must not immediately hit trackers."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            updated = await check_favorites_once(bot, jackett, session_pool, config)
            if updated:
                logger.info("Favorites check: %s topics updated", updated)
        except (JackettError, SQLAlchemyError):
            logger.exception("Favorites check pass failed")
