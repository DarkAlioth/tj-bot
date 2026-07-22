import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin import (
    CANCEL,
    KIND_MAGNET,
    KIND_TORRENT,
    NO_CATEGORY,
    Dsc,
    add_magnet,
    choose_category,
    magnet_name,
    send_to_server,
)
from tj_bot.handlers.user import Dlt, category_token
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

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
    config.settings.qbit_poll_interval_seconds = 30
    config.settings.qbit_watch_timeout_seconds = 3600
    return cast(AppConfig, config)


def make_qbit(free_space: int = 100 * GB) -> AsyncMock:
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.categories.return_value = {
        "movies": {"name": "movies", "savePath": "/dl/movies"},
        "tj-bot": {"name": "tj-bot", "savePath": "/dl/tj-bot"},
    }
    qbit.free_space.return_value = free_space
    qbit.torrents_by_tag.return_value = [{"progress": 1.0, "name": "Movie"}]
    return qbit


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = 111
    message = AsyncMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = 777
    message.message_id = 5
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.delete = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    return query


def cbdata() -> Dlt:
    return Dlt(type="server", hash="a" * 32)


async def drain_watchers() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


async def run_menu(
    query: AsyncMock,
    repo: AsyncMock,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    await send_to_server(query, cbdata(), repo, qbit, config)


async def run_choice(
    query: AsyncMock,
    token: str,
    repo: AsyncMock,
    jackett: AsyncMock,
    qbit: QbittorrentClient | None,
    kind: str = KIND_TORRENT,
) -> None:
    data = Dsc(k=kind, t=token, hash="a" * 32)
    await choose_category(
        query, data, repo, jackett, qbit, make_config(), cast(Bot, AsyncMock())
    )


def menu_button_texts(query: AsyncMock) -> list[str]:
    markup = query.message.answer.await_args.kwargs["reply_markup"]
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_non_admin_rejected() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    await run_menu(query, repo, None, make_config(admin=False))
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_qbit_none_is_noop() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    await run_menu(query, repo, None, make_config())
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_menu_lists_categories_with_default_first() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()

    await run_menu(query, repo, make_qbit(), make_config())

    texts = menu_button_texts(query)
    assert texts[0] == "⭐ tj-bot"
    assert "movies" in texts
    assert "📂 Без категории" in texts
    assert "❌ Отмена" in texts
    body = query.message.answer.await_args.args[0]
    assert "Свободно на диске" in body
    assert "⚠️" not in body


async def test_menu_warns_when_disk_space_is_low() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()

    await run_menu(query, repo, make_qbit(free_space=1 * GB), make_config())

    body = query.message.answer.await_args.args[0]
    assert "⚠️" in body


async def test_menu_qbit_error_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    qbit = make_qbit()
    qbit.categories.side_effect = QbittorrentError("down")

    await run_menu(query, repo, qbit, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_choice_cancel_deletes_menu() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    qbit = make_qbit()

    await run_choice(query, CANCEL, repo, AsyncMock(spec=JackettClient), qbit)

    query.message.delete.assert_awaited_once()
    qbit.add_torrent_file.assert_not_awaited()


async def test_choice_non_admin_rejected() -> None:
    query = make_query()
    data = Dsc(k=KIND_TORRENT, t=NO_CATEGORY, hash="a" * 32)
    repo = AsyncMock(spec=TorrentRepo)

    await choose_category(
        query,
        data,
        repo,
        AsyncMock(spec=JackettClient),
        make_qbit(),
        make_config(admin=False),
        cast(Bot, AsyncMock()),
    )

    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.get_torrent_by_hash.assert_not_awaited()


async def test_choice_adds_torrent_with_selected_category() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrentbytes"
    qbit = make_qbit()

    await run_choice(query, category_token("movies"), repo, jackett, qbit)

    kwargs = qbit.add_torrent_file.await_args.kwargs
    assert kwargs["category"] == "movies"
    assert kwargs["tag"].startswith("tjbot-")
    repo.record_download.assert_awaited_once_with(111, "Movie 1080p", "server")
    query.message.edit_text.assert_awaited_once()
    await drain_watchers()


async def test_choice_no_category_passes_none() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrentbytes"
    qbit = make_qbit()

    await run_choice(query, NO_CATEGORY, repo, jackett, qbit)

    assert qbit.add_torrent_file.await_args.kwargs["category"] is None
    await drain_watchers()


async def test_choice_unknown_token_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    qbit = make_qbit()

    await run_choice(query, "deadbeef", repo, AsyncMock(spec=JackettClient), qbit)

    assert query.answer.await_args.kwargs.get("show_alert") is True
    qbit.add_torrent_file.assert_not_awaited()


async def test_choice_download_too_large_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = DownloadTooLargeError
    qbit = make_qbit()

    await run_choice(query, NO_CATEGORY, repo, jackett, qbit)

    qbit.add_torrent_file.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_choice_jackett_error_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = JackettError("down")
    qbit = make_qbit()

    await run_choice(query, NO_CATEGORY, repo, jackett, qbit)

    qbit.add_torrent_file.assert_not_awaited()


async def test_choice_qbit_add_error_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = make_torrent()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"data"
    qbit = make_qbit()
    qbit.add_torrent_file.side_effect = QbittorrentError("down")

    await run_choice(query, NO_CATEGORY, repo, jackett, qbit)

    assert query.answer.await_args.kwargs.get("show_alert") is True
    repo.record_download.assert_not_awaited()


async def test_choice_stale_torrent_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_torrent_by_hash.return_value = None
    qbit = make_qbit()

    await run_choice(query, NO_CATEGORY, repo, AsyncMock(spec=JackettClient), qbit)

    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_choice_adds_magnet_with_selected_category() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_magnet_url.return_value = "magnet:?xt=urn:btih:abc&dn=Movie"
    qbit = make_qbit()

    await run_choice(
        query,
        category_token("movies"),
        repo,
        AsyncMock(spec=JackettClient),
        qbit,
        kind=KIND_MAGNET,
    )

    args = qbit.add_torrent_url.await_args
    assert args.args[0].startswith("magnet:")
    assert args.kwargs["category"] == "movies"
    repo.record_download.assert_awaited_once_with(111, "Movie", "server")
    await drain_watchers()


async def test_choice_stale_magnet_alerts() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_magnet_url.return_value = None
    qbit = make_qbit()

    await run_choice(
        query,
        NO_CATEGORY,
        repo,
        AsyncMock(spec=JackettClient),
        qbit,
        kind=KIND_MAGNET,
    )

    assert query.answer.await_args.kwargs.get("show_alert") is True
    qbit.add_torrent_url.assert_not_awaited()


def test_magnet_name_parses_dn() -> None:
    assert magnet_name("magnet:?xt=urn:btih:abc&dn=Ubuntu%2024.04") == "Ubuntu 24.04"
    assert magnet_name("magnet:?xt=urn:btih:abc") == "magnet-ссылка"


async def test_add_magnet_saves_and_shows_menu() -> None:
    message = AsyncMock(spec=Message)
    message.text = "magnet:?xt=urn:btih:abc&dn=Movie"
    message.from_user = MagicMock()
    message.from_user.id = 111
    message.answer = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.save_magnet.return_value = "b" * 32
    qbit = make_qbit()

    await add_magnet(cast(Message, message), repo, qbit, make_config())

    repo.save_magnet.assert_awaited_once_with("magnet:?xt=urn:btih:abc&dn=Movie")
    qbit.add_torrent_url.assert_not_awaited()
    body = message.answer.await_args.args[0]
    assert "Movie" in body
    assert "Выберите категорию" in body


async def test_add_magnet_without_qbit() -> None:
    message = AsyncMock(spec=Message)
    message.text = "magnet:?xt=urn:btih:abc"
    message.answer = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)

    await add_magnet(cast(Message, message), repo, None, make_config())

    assert "не настроен" in message.answer.await_args.args[0]
