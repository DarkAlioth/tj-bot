import datetime
from typing import Any, cast
from unittest.mock import AsyncMock

from aiogram.filters import CommandObject
from aiogram.types import Message

from tj_bot.config import AppConfig
from tj_bot.db.models import SearchQuery, Torrent
from tj_bot.db.repo import TorrentData, TorrentRepo
from tj_bot.handlers.user import (
    ALL_CATEGORIES,
    Pgn,
    category_token,
    go_search,
    render_page,
    resolve_category,
    srch_torrent,
)
from tj_bot.services.jackett import JackettClient, JackettError


def make_config(qbit_enabled: bool = False, admin: bool = False) -> AppConfig:
    config = AsyncMock(spec=AppConfig)
    config.qbit_enabled = qbit_enabled
    config.is_admin = lambda _user_id: admin
    return cast(AppConfig, config)


def make_torrent_model(**overrides: Any) -> Torrent:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "id": 1,
        "hash": "abc",
        "title": "Title",
        "uploader": None,
        "description": None,
        "category": "Movies",
        "details_url": "https://tracker.example/1",
        "download_url": "http://jackett:9117/dl/1",
        "seeders": 5,
        "peers": 2,
        "published_at": datetime.date(2026, 7, 14),
        "size": 1073741824,
    }
    values.update(overrides)
    return Torrent(**values)


def make_item() -> TorrentData:
    return TorrentData(
        hash="abc",
        title="Title",
        uploader=None,
        description=None,
        category="Movies",
        details_url="https://tracker.example/1",
        download_url="http://jackett:9117/dl/1",
        seeders=5,
        peers=2,
        published_at=datetime.date(2026, 7, 14),
        size=1073741824,
    )


def make_command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="s", args=args)


async def test_search_renders_first_result_card() -> None:
    message = AsyncMock()
    sent = AsyncMock()
    message.answer.return_value = sent
    repo = AsyncMock(spec=TorrentRepo)
    repo.upsert_torrents.return_value = [1]
    repo.get_result_page.return_value = make_torrent_model()
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.return_value = [make_item()]

    await srch_torrent(
        cast(Message, message), repo, jackett, make_config(), make_command("ubuntu")
    )

    jackett.search.assert_awaited_once_with("ubuntu")
    repo.create_search.assert_awaited_once()
    card_text = sent.edit_text.await_args.args[0]
    assert "<b>1</b> из <b>1</b>" in card_text
    assert "Title" in card_text


async def test_search_failure_reports_to_user() -> None:
    message = AsyncMock()
    sent = AsyncMock()
    message.answer.return_value = sent
    repo = AsyncMock(spec=TorrentRepo)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.side_effect = JackettError("boom")

    await srch_torrent(
        cast(Message, message), repo, jackett, make_config(), make_command("x")
    )

    assert "недоступен" in sent.edit_text.await_args.args[0]
    repo.upsert_torrents.assert_not_awaited()


async def test_search_no_results_message() -> None:
    message = AsyncMock()
    sent = AsyncMock()
    message.answer.return_value = sent
    repo = AsyncMock(spec=TorrentRepo)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.return_value = []

    await srch_torrent(
        cast(Message, message), repo, jackett, make_config(), make_command("x")
    )

    assert "не найдено" in sent.edit_text.await_args.args[0]


async def test_resolve_category_round_trip() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_categories.return_value = [("Movies/HD", 3), ("Audio", 1)]

    token = category_token("Movies/HD")

    assert await resolve_category(repo, "qh", token) == "Movies/HD"
    assert await resolve_category(repo, "qh", ALL_CATEGORIES) is None
    assert await resolve_category(repo, "qh", "ffffffff") is None


async def test_render_page_navigation_buttons() -> None:
    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=3)
    repo.get_result_page.return_value = make_torrent_model()

    await render_page(query, repo, make_config(), "qh", 1, ALL_CATEGORIES)

    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["💾 Скачать", "🗂 Категории", "⬅", "➡"]

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert all(len(data.encode()) <= 64 for data in callbacks)


async def test_categories_menu_has_back_button_to_origin_card() -> None:
    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=3)
    repo.get_categories.return_value = [("Movies", 2), ("Audio", 1)]
    origin = Pgn(type="go_search", qh="qh", page=4, srch=category_token("Movies"))

    await go_search(query, origin, repo)

    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    rows = keyboard.inline_keyboard
    assert rows[0][0].text == "Все - 3"
    assert [b.text for b in rows[1]] == ["Movies - 2", "Audio - 1"]

    back = rows[-1][0]
    assert back.text == "◀️ Назад"
    unpacked = Pgn.unpack(back.callback_data)
    assert unpacked.type == "fs"
    assert unpacked.page == 4
    assert unpacked.srch == category_token("Movies")
    assert all(
        len(b.callback_data.encode()) <= 64 for r in rows for b in r if b.callback_data
    )
