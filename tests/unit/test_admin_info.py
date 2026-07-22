import datetime
from typing import cast
from unittest.mock import AsyncMock

from aiogram.types import Message

from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin_info import show_indexers, show_stats
from tj_bot.services.jackett import JackettClient, JackettError


def make_message() -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    return message


async def test_stats_renders_summary_and_top() -> None:
    message = make_message()
    repo = AsyncMock(spec=TorrentRepo)
    from tj_bot.db.repo import SearchStats

    repo.get_stats.return_value = SearchStats(
        torrents=42,
        queries=7,
        events_window=19,
        users_window=3,
        top_queries=[("ubuntu", 5), ("debian", 2)],
    )
    started = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)

    await show_stats(cast(Message, message), repo, started)

    text = message.answer.await_args.args[0]
    assert "Торрентов в кэше: 42" in text
    assert "Поисков за 7д: 19" in text
    assert "1. ubuntu — 5" in text


async def test_indexers_shows_status() -> None:
    message = make_message()
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.return_value = [
        {"name": "RuTracker", "last_error": ""},
        {"name": "NoNameClub", "last_error": "login failed"},
    ]

    await show_indexers(cast(Message, message), jackett)

    text = message.answer.await_args.args[0]
    assert "✅ RuTracker" in text
    assert "⚠️ NoNameClub" in text
    assert "login failed" in text


async def test_indexers_handles_jackett_error() -> None:
    message = make_message()
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.side_effect = JackettError("down")

    await show_indexers(cast(Message, message), jackett)

    assert "недоступен" in message.answer.await_args.args[0]
