import datetime
from typing import cast
from unittest.mock import AsyncMock

from aiogram.types import Message

from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin_info import show_health, show_indexers, show_stats
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError


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
        {"Name": "RuTracker", "Error": None, "Results": 50},
        {"Name": "NoNameClub", "Error": "login failed", "Results": 0},
    ]
    status = AsyncMock()
    message.answer.return_value = status

    await show_indexers(cast(Message, message), jackett)

    text = status.edit_text.await_args.args[0]
    assert "✅ RuTracker" in text
    assert "⚠️ NoNameClub" in text
    assert "login failed" in text


async def test_indexers_handles_jackett_error() -> None:
    message = make_message()
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.side_effect = JackettError("down")
    status = AsyncMock()
    message.answer.return_value = status

    await show_indexers(cast(Message, message), jackett)

    assert "недоступен" in status.edit_text.await_args.args[0]


def make_health_env() -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    message = make_message()
    status = AsyncMock()
    message.answer.return_value = status
    repo = AsyncMock(spec=TorrentRepo)
    repo.db_size.return_value = 42 * 1024 * 1024
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.return_value = [
        {"Name": "RuTracker", "Error": None, "Results": 50},
        {"Name": "Broken", "Error": "login failed", "Results": 0},
    ]
    return message, status, repo, jackett


def make_qbit() -> AsyncMock:
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = [
        {"state": "downloading"},
        {"state": "stoppedUP"},
    ]
    qbit.transfer_info.return_value = {"dl_info_speed": 1024, "up_info_speed": 0}
    qbit.free_space.return_value = 100 * 1024**3
    return qbit


STARTED = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)


async def test_health_renders_all_sections() -> None:
    message, status, repo, jackett = make_health_env()

    await show_health(cast(Message, message), repo, jackett, make_qbit(), STARTED)

    first = message.answer.await_args.args[0]
    assert "Аптайм" in first
    assert "42.0 MB" in first
    assert "Диск бота" in first
    assert "1 акт. / 2 всего" in first
    assert "проверяю" in first
    final = status.edit_text.await_args.args[0]
    assert "1/2 в строю" in final
    assert "⚠️ Broken" in final


async def test_health_without_qbit() -> None:
    message, status, repo, jackett = make_health_env()

    await show_health(cast(Message, message), repo, jackett, None, STARTED)

    assert "не настроен" in message.answer.await_args.args[0]


async def test_health_qbit_error_degrades() -> None:
    message, status, repo, jackett = make_health_env()
    qbit = make_qbit()
    qbit.list_torrents.side_effect = QbittorrentError("down")

    await show_health(cast(Message, message), repo, jackett, qbit, STARTED)

    assert "недоступен" in message.answer.await_args.args[0]


async def test_health_jackett_error_degrades() -> None:
    message, status, repo, jackett = make_health_env()
    jackett.indexers.side_effect = JackettError("down")

    await show_health(cast(Message, message), repo, jackett, make_qbit(), STARTED)

    assert "Jackett недоступен" in status.edit_text.await_args.args[0]
