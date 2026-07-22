import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin import send_to_server
from tj_bot.handlers.user import Dlt
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError


def make_torrent() -> MagicMock:
    torrent = MagicMock()
    torrent.hash = "a" * 32
    torrent.title = "Movie 1080p"
    torrent.download_url = "http://jackett:9117/dl/1"
    return torrent


def make_config(admin: bool = True) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda _uid: admin
    config.settings.qbit_category = "tj-bot"
    config.settings.qbit_poll_interval_seconds = 30
    config.settings.qbit_watch_timeout_seconds = 3600
    return cast(AppConfig, config)


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = 111
    message = AsyncMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = 777
    message.answer = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    return query


def cbdata() -> Dlt:
    return Dlt(type="server", hash="a" * 32)


async def run(
    query: AsyncMock,
    repo: AsyncMock,
    jackett: AsyncMock,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    await send_to_server(
        query, cbdata(), repo, jackett, qbit, config, cast(Bot, AsyncMock())
    )


async def test_non_admin_rejected() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    await run(
        query, repo, AsyncMock(spec=JackettClient), None, make_config(admin=False)
    )
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_qbit_none_is_noop() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    await run(query, repo, AsyncMock(spec=JackettClient), None, make_config())
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_download_too_large_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = DownloadTooLargeError
    qbit = AsyncMock(spec=QbittorrentClient)

    await run(query, repo, jackett, qbit, make_config())

    qbit.add_torrent_file.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_jackett_error_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = JackettError("down")
    qbit = AsyncMock(spec=QbittorrentClient)

    await run(query, repo, jackett, qbit, make_config())

    qbit.add_torrent_file.assert_not_awaited()


async def test_qbit_error_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"data"
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.add_torrent_file.side_effect = QbittorrentError("down")

    await run(query, repo, jackett, qbit, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_happy_path_adds_and_spawns_watcher() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrentbytes"
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrents_by_tag.return_value = [{"progress": 1.0, "name": "Movie"}]
    bot = AsyncMock(spec=Bot)

    await send_to_server(query, cbdata(), repo, jackett, qbit, make_config(), bot)

    qbit.add_torrent_file.assert_awaited_once()
    kwargs = qbit.add_torrent_file.await_args.kwargs
    assert kwargs["category"] == "tj-bot"
    assert kwargs["tag"].startswith("tjbot-")
    query.message.answer.assert_awaited_once()
    # let the spawned watcher task run to completion
    for _ in range(3):
        await asyncio.sleep(0)


def test_magnet_name_parses_dn() -> None:
    from tj_bot.handlers.admin import magnet_name

    assert magnet_name("magnet:?xt=urn:btih:abc&dn=Ubuntu%2024.04") == "Ubuntu 24.04"
    assert magnet_name("magnet:?xt=urn:btih:abc") == "magnet-ссылка"


async def test_add_magnet_adds_and_starts_progress() -> None:
    from tj_bot.handlers.admin import add_magnet

    message = AsyncMock(spec=Message)
    message.text = "magnet:?xt=urn:btih:abc&dn=Movie"
    message.from_user = MagicMock()
    message.from_user.id = 111
    message.chat = MagicMock()
    message.chat.id = 777
    sent = AsyncMock()
    sent.chat = MagicMock()
    sent.chat.id = 777
    sent.message_id = 5
    message.answer = AsyncMock(return_value=sent)
    repo = AsyncMock(spec=TorrentRepo)
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrents_by_tag.return_value = [{"progress": 1.0, "name": "Movie"}]

    await add_magnet(
        cast(Message, message), repo, qbit, make_config(), cast(Bot, AsyncMock())
    )

    qbit.add_torrent_url.assert_awaited_once()
    assert qbit.add_torrent_url.await_args.args[0].startswith("magnet:")
    repo.record_download.assert_awaited_once_with(111, "Movie", "server")
    import asyncio

    for _ in range(3):
        await asyncio.sleep(0)


async def test_add_magnet_without_qbit() -> None:
    from tj_bot.handlers.admin import add_magnet

    message = AsyncMock(spec=Message)
    message.text = "magnet:?xt=urn:btih:abc"
    message.answer = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)

    await add_magnet(
        cast(Message, message), repo, None, make_config(), cast(Bot, AsyncMock())
    )

    assert "не настроен" in message.answer.await_args.args[0]
