import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.filters import CommandObject
from aiogram.types import Message

from tj_bot.config import AppConfig
from tj_bot.db.models import SearchQuery, Torrent
from tj_bot.db.repo import TorrentData, TorrentRepo
from tj_bot.handlers.user import (
    ALL_CATEGORIES,
    DEFAULT_SORT,
    Pg2,
    category_token,
    go_search,
    render_page,
    resolve_category,
    srch_torrent,
)
from tj_bot.services.jackett import JackettClient, JackettError


def make_config(qbit_enabled: bool = False, admin: bool = False) -> AppConfig:
    config = MagicMock()
    config.qbit_enabled = qbit_enabled
    config.is_admin = lambda _user_id: admin
    config.settings.search_cache_seconds = 3600
    config.settings.subscriptions_per_user = 3
    return cast(AppConfig, config)


def make_torrent_model(**overrides: Any) -> Torrent:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "id": 1,
        "hash": "abc",
        "title": "Title",
        "uploader": None,
        "description": None,
        "category": "Movies",
        "tracker": "rutracker",
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
        tracker="rutracker",
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
    repo.find_recent_search.return_value = None
    repo.upsert_torrents.return_value = [1]
    repo.create_search.return_value = 1
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
    repo.find_recent_search.return_value = None

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
    repo.find_recent_search.return_value = None
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

    await render_page(query, repo, make_config(), "qh", 1, ALL_CATEGORIES, DEFAULT_SORT)

    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == ["💾 Скачать", "🗂 Категории", "↕ Сиды", "🔔", "⬅", "🔄", "➡"]

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
    origin = Pg2(t="gs", qh="qh", p=4, c=category_token("Movies"), s="se")

    await go_search(query, origin, repo)

    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    rows = keyboard.inline_keyboard
    assert rows[0][0].text == "Все - 3"
    assert [b.text for b in rows[1]] == ["Movies - 2", "Audio - 1"]

    back = rows[-1][0]
    assert back.text == "◀️ Назад"
    unpacked = Pg2.unpack(back.callback_data)
    assert unpacked.t == "fs"
    assert unpacked.p == 4
    assert unpacked.c == category_token("Movies")
    assert all(
        len(b.callback_data.encode()) <= 64 for r in rows for b in r if b.callback_data
    )


async def test_instant_cache_hit_skips_jackett() -> None:
    message = AsyncMock()
    sent = AsyncMock()
    message.answer.return_value = sent
    repo = AsyncMock(spec=TorrentRepo)
    repo.find_recent_search.return_value = SearchQuery(
        id=5, hash="qh", query_text="ubuntu", result_count=2
    )
    repo.get_result_page.return_value = make_torrent_model()
    jackett = AsyncMock(spec=JackettClient)

    await srch_torrent(
        cast(Message, message), repo, jackett, make_config(), make_command("Ubuntu")
    )

    jackett.search.assert_not_awaited()
    repo.record_search_event.assert_awaited_once()
    card = sent.edit_text.await_args.args[0]
    assert "⚡" in card


async def test_refresh_button_forces_new_search() -> None:
    from tj_bot.handlers.user import Upd, refresh_search

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    telegram_message.answer = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(
        id=5, hash="qh", query_text="ubuntu", result_count=2
    )
    repo.find_recent_search.return_value = None
    repo.upsert_torrents.return_value = [1]
    repo.create_search.return_value = 5
    repo.get_result_page.return_value = make_torrent_model()
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.return_value = [make_item()]

    await refresh_search(query, Upd(qh="qh"), repo, jackett, make_config())

    jackett.search.assert_awaited_once_with("ubuntu")


async def test_history_empty_and_filled() -> None:
    from tj_bot.handlers.user import show_history

    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 42
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user_history.return_value = []

    await show_history(cast(Message, message), repo)
    assert "пуста" in message.answer.await_args.args[0]

    repo.get_user_history.return_value = [(1, "ubuntu iso"), (2, "debian")]
    await show_history(cast(Message, message), repo)
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert [row[0].text for row in keyboard.inline_keyboard] == [
        "ubuntu iso",
        "debian",
    ]


async def test_history_replay_stale_query_alerts() -> None:
    from tj_bot.handlers.user import Hst, replay_history

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_query_text.return_value = None
    jackett = AsyncMock(spec=JackettClient)

    await replay_history(query, Hst(qid=9), repo, jackett, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_subscribe_prefills_seen_and_confirms() -> None:
    from tj_bot.handlers.user import Sub, subscribe

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.chat = MagicMock()
    telegram_message.chat.id = 777
    query.message = telegram_message
    query.from_user = MagicMock()
    query.from_user.id = 42
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(
        id=5, hash="qh", query_text="ubuntu", result_count=2
    )
    repo.count_subscriptions.return_value = 0
    created = MagicMock()
    created.id = 10
    repo.create_subscription.return_value = created
    repo.get_result_hashes.return_value = ["h1", "h2"]

    await subscribe(query, Sub(a="add", i=0, qh="qh"), repo, make_config())

    repo.create_subscription.assert_awaited_once_with(42, 777, "ubuntu")
    repo.add_seen_hashes.assert_awaited_once_with(10, ["h1", "h2"])
    assert "Подписка создана" in query.answer.await_args.args[0]


async def test_subscribe_limit_blocks() -> None:
    from tj_bot.handlers.user import Sub, subscribe

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.chat = MagicMock()
    query.message = telegram_message
    query.from_user = MagicMock()
    query.from_user.id = 42
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(
        id=5, hash="qh", query_text="ubuntu", result_count=2
    )
    config = make_config()
    config.settings.subscriptions_per_user = 3
    repo.count_subscriptions.return_value = 3

    await subscribe(query, Sub(a="add", i=0, qh="qh"), repo, config)

    repo.create_subscription.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_unsubscribe_rerenders_list() -> None:
    from tj_bot.handlers.user import Sub, unsubscribe

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    query.from_user = MagicMock()
    query.from_user.id = 42
    repo = AsyncMock(spec=TorrentRepo)
    repo.delete_subscription.return_value = True
    repo.list_subscriptions.return_value = []

    await unsubscribe(query, Sub(a="del", i=7, qh=""), repo)

    repo.delete_subscription.assert_awaited_once_with(7, 42)
    assert "Подписок нет" in telegram_message.edit_text.await_args.args[0]
