import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import InlineQuery, InputTextMessageContent

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.inline import inline_article, inline_search


def make_torrent() -> MagicMock:
    torrent = MagicMock()
    torrent.hash = "a" * 32
    torrent.title = "Movie &amp; Co 1080p"
    torrent.details_url = "https://tracker.example/details"
    torrent.seeders = 120
    torrent.peers = 15
    torrent.size = 2 * 1024**3
    torrent.category = "Movies"
    torrent.tracker = "rutracker"
    torrent.published_at = datetime.date(2026, 7, 1)
    return torrent


def make_config() -> AppConfig:
    config = MagicMock()
    config.settings.search_cache_seconds = 3600
    return cast(AppConfig, config)


def make_query(text: str) -> AsyncMock:
    query = AsyncMock(spec=InlineQuery)
    query.query = text
    query.answer = AsyncMock()
    return query


async def test_short_query_answers_empty_with_hint() -> None:
    query = make_query("ab")
    repo = AsyncMock(spec=TorrentRepo)

    await inline_search(query, repo, make_config())

    assert query.answer.await_args.args[0] == []
    assert "от 3 символов" in query.answer.await_args.kwargs["button"].text
    repo.find_recent_search.assert_not_awaited()


async def test_cache_miss_offers_bot_search() -> None:
    query = make_query("ubuntu iso")
    repo = AsyncMock(spec=TorrentRepo)
    repo.find_recent_search.return_value = None

    await inline_search(query, repo, make_config())

    assert query.answer.await_args.args[0] == []
    assert "искать в боте" in query.answer.await_args.kwargs["button"].text
    repo.get_result_list.assert_not_awaited()


async def test_cache_hit_serves_articles() -> None:
    query = make_query("Ubuntu ISO")
    repo = AsyncMock(spec=TorrentRepo)
    recent = MagicMock()
    recent.hash = "qh"
    repo.find_recent_search.return_value = recent
    repo.get_result_list.return_value = [make_torrent()]

    await inline_search(query, repo, make_config())

    # the lookup uses the normalized query text
    assert repo.find_recent_search.await_args.args[0] == "ubuntu iso"
    results = query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].id == "a" * 32
    assert query.answer.await_args.kwargs["cache_time"] == 300


def test_article_unescapes_plain_fields_and_keeps_html_content() -> None:
    article = inline_article(make_torrent())

    assert article.title == "Movie & Co 1080p"
    assert "🌱 120" in (article.description or "")
    content = article.input_message_content
    assert isinstance(content, InputTextMessageContent)
    assert "Movie &amp; Co 1080p" in content.message_text
    assert "https://tracker.example/details" in content.message_text
    assert "01.07.2026" in content.message_text
