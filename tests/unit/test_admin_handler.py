from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin import add_magnet, send_to_server
from tj_bot.handlers.user import Dlt
from tj_bot.services.jackett import JackettClient
from tj_bot.services.qbittorrent import QbittorrentClient

GB = 1024**3


def make_torrent() -> MagicMock:
    torrent = MagicMock()
    torrent.hash = "a" * 32
    torrent.title = "Movie 1080p"
    torrent.download_url = "http://jackett:9117/dl/1"
    torrent.size = 2 * GB
    return torrent


def make_config(admin: bool = True) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda _uid: admin
    config.settings.qbit_category = "tj-bot"
    return cast(AppConfig, config)


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = 111
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    return query


async def test_send_to_server_non_admin_rejected() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)

    await send_to_server(
        query,
        Dlt(type="server", hash="a" * 32),
        repo,
        AsyncMock(spec=JackettClient),
        AsyncMock(spec=QbittorrentClient),
        make_config(admin=False),
    )

    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_send_to_server_qbit_none_is_noop() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)

    await send_to_server(
        query,
        Dlt(type="server", hash="a" * 32),
        repo,
        AsyncMock(spec=JackettClient),
        None,
        make_config(),
    )

    repo.get_torrent_by_hash.assert_not_awaited()


async def test_send_to_server_opens_file_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, object] = {}

    async def fake_begin(
        query: object,
        message: object,
        repo: object,
        jackett: object,
        qbit: object,
        config: object,
        name: str,
        download_url: str,
    ) -> None:
        started["name"] = name
        started["url"] = download_url

    monkeypatch.setattr("tj_bot.handlers.admin.begin_torrent_send", fake_begin)
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()

    await send_to_server(
        query,
        Dlt(type="server", hash="a" * 32),
        repo,
        AsyncMock(spec=JackettClient),
        AsyncMock(spec=QbittorrentClient),
        make_config(),
    )

    assert started == {"name": "Movie 1080p", "url": "http://jackett:9117/dl/1"}


async def test_magnet_without_qbit_hints() -> None:
    message = AsyncMock(spec=Message)
    message.text = "magnet:?xt=urn:btih:abc"
    message.answer = AsyncMock()

    await add_magnet(message, AsyncMock(spec=TorrentRepo), None, make_config())

    assert "не настроен" in message.answer.await_args.args[0]


async def test_magnet_delegates_to_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def fake_begin(
        message: object, repo: object, qbit: object, config: object, url: str
    ) -> None:
        seen["url"] = url

    monkeypatch.setattr("tj_bot.handlers.admin.begin_magnet_send", fake_begin)
    message = AsyncMock(spec=Message)
    message.text = "  magnet:?xt=urn:btih:abc&dn=Movie  "
    message.answer = AsyncMock()

    await add_magnet(
        message,
        AsyncMock(spec=TorrentRepo),
        AsyncMock(spec=QbittorrentClient),
        make_config(),
    )

    assert seen["url"] == "magnet:?xt=urn:btih:abc&dn=Movie"
