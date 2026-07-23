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
    go_list,
    go_search,
    list_line,
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
    repo.is_favorite.return_value = False

    await render_page(query, repo, make_config(), "qh", 1, ALL_CATEGORIES, DEFAULT_SORT)

    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == [
        "📋 Список",
        "Сиды ↓",
        "🔎 Фильтр",
        "💾 Скачать",
        "☆ Сохранить",
        "⬅",
        "🔄",
        "➡",
    ]

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert all(len(data.encode()) <= 64 for data in callbacks)


async def test_last_replays_most_recent_query_from_cache() -> None:
    from tj_bot.handlers.user import repeat_last_search

    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 111
    sent = AsyncMock()
    message.answer.return_value = sent
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user_history.return_value = [(5, "ubuntu iso")]
    recent = SearchQuery(id=5, hash="qh", result_count=2)
    repo.find_recent_search.return_value = recent
    repo.get_result_page.return_value = make_torrent_model()
    jackett = AsyncMock(spec=JackettClient)

    await repeat_last_search(cast(Message, message), repo, jackett, make_config())

    repo.get_user_history.assert_awaited_once_with(111, limit=1)
    jackett.search.assert_not_awaited()  # served from cache
    assert "из <b>2</b>" in sent.edit_text.await_args.args[0]


async def test_last_with_empty_history_hints() -> None:
    from tj_bot.handlers.user import repeat_last_search

    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 111
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user_history.return_value = []

    await repeat_last_search(
        cast(Message, message), repo, AsyncMock(spec=JackettClient), make_config()
    )

    assert "пуста" in message.answer.await_args.args[0]


async def test_list_view_renders_numbered_page() -> None:
    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=25)
    repo.get_result_list.return_value = [
        make_torrent_model(id=i, hash=f"h{i}", title=f"Item {i}") for i in range(10)
    ]
    data = Pg2(t="ls", qh="qh", p=1, c=ALL_CATEGORIES, s="se", fl="000")

    await go_list(query, data, repo)

    assert repo.get_result_list.await_args.args[2] == 10  # offset = page * size
    text = telegram_message.edit_text.await_args.args[0]
    assert "Результаты 11–20" in text
    assert "Item 0" in text
    assert "14.07.26" in text  # published date shown in list lines
    keyboard = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    # row 1 mirrors the card controls: card mode, sort, filter
    top = keyboard.inline_keyboard[0]
    assert [b.text for b in top] == ["🃏 Карточка", "Сиды ↓", "🔎 Фильтр"]
    card = Pg2.unpack(top[0].callback_data)
    assert card.t == "fs"
    assert card.p == 10
    sort = Pg2.unpack(top[1].callback_data)
    assert sort.t == "ls"
    assert sort.s == "sz"  # next sort in the cycle, staying in list mode
    from tj_bot.handlers.user import Flt

    flt = Flt.unpack(top[2].callback_data)
    assert flt.a == "open"
    assert flt.o == "l"
    numbers = [b for row in keyboard.inline_keyboard[1:-1] for b in row]
    assert [b.text for b in numbers] == [str(n) for n in range(11, 21)]
    first = Pg2.unpack(numbers[0].callback_data)
    assert first.t == "fs"
    assert first.p == 10
    nav = keyboard.inline_keyboard[-1]
    assert [b.text for b in nav] == ["⬅", "🔄", "➡"]
    assert all(
        len(b.callback_data.encode()) <= 64
        for row in keyboard.inline_keyboard
        for b in row
    )


async def test_list_view_empty_page_alerts() -> None:
    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=0)
    repo.get_result_list.return_value = []
    data = Pg2(t="ls", qh="qh", p=0, c=ALL_CATEGORIES, s="se", fl="000")

    await go_list(query, data, repo)

    assert query.answer.await_args.kwargs.get("show_alert") is True
    telegram_message.edit_text.assert_not_awaited()


def test_list_line_truncates_after_unescaping() -> None:
    torrent = make_torrent_model(title="Movie &amp; " + "x" * 60)

    line = list_line(3, torrent)

    assert line.startswith("3. ")
    assert "…" in line
    assert "Movie &amp; x" in line  # re-escaped after the cut
    assert "1.0 GB" in line
    assert "14.07.26" in line


async def test_categories_menu_has_back_button_to_origin_card() -> None:
    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=3)
    repo.get_categories.return_value = [("Movies", 2), ("Audio", 1)]
    origin = Pg2(t="gs", qh="qh", p=4, c=category_token("Movies"), s="se", fl="000")

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


async def test_filter_menu_open_and_cycle() -> None:
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)

    await filter_menu(
        query,
        Flt(a="open", qh="qh", c="ALL", s="se", fl="000", o="c"),
        repo,
        make_config(),
    )

    text = telegram_message.edit_text.await_args.args[0]
    assert "Фильтры" in text
    kb = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert labels[0] == "Категория: Все  ›"
    assert any("Сиды" in x for x in labels)
    assert any("Применить" in x for x in labels)
    assert all(
        len(b.callback_data.encode()) <= 64
        for r in kb.inline_keyboard
        for b in r
        if b.callback_data
    )


async def test_filter_menu_escapes_size_label() -> None:
    """Regression: raw "<" from the size label broke Telegram HTML parsing."""
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)

    await filter_menu(
        query,
        Flt(a="cz", qh="qh", c="ALL", s="se", fl="010", o="c"),
        repo,
        make_config(),
    )

    text = telegram_message.edit_text.await_args.args[0]
    assert "размер &lt; 1 GB" in text
    assert "размер <" not in text
    kb = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    # button labels are plain text, they keep the raw "<"
    assert kb.inline_keyboard[2][0].text == "Размер: < 1 GB  🔁"


async def test_filter_menu_category_row_and_picker() -> None:
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=3)
    repo.get_categories.return_value = [("Movies", 2), ("Audio", 1)]
    token = category_token("Movies")

    await filter_menu(
        query,
        Flt(a="open", qh="qh", c=token, s="se", fl="000", o="c"),
        repo,
        make_config(),
    )
    text = telegram_message.edit_text.await_args.args[0]
    assert "категория Movies" in text
    kb = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].text == "Категория: Movies  ›"

    # the category row opens a picker; options reopen the menu with c swapped
    await filter_menu(
        query,
        Flt(a="ct", qh="qh", c=token, s="se", fl="230", o="l"),
        repo,
        make_config(),
    )
    picker = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    rows = picker.inline_keyboard
    assert rows[0][0].text == "Все - 3"
    assert [b.text for b in rows[1]] == ["Movies - 2", "Audio - 1"]
    assert rows[-1][0].text == "◀️ Назад"
    for button in (rows[0][0], *rows[1], rows[-1][0]):
        opt = Flt.unpack(button.callback_data)
        assert opt.a == "open"
        assert opt.fl == "230"  # working filter code survives the picker
        assert opt.o == "l"  # origin survives too
    assert Flt.unpack(rows[0][0].callback_data).c == "ALL"
    assert Flt.unpack(rows[1][0].callback_data).c == token


async def test_filter_menu_apply_renders_page() -> None:
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=3)
    repo.count_results.return_value = 1
    repo.get_result_page.return_value = make_torrent_model()
    repo.is_favorite.return_value = False

    await filter_menu(
        query,
        Flt(a="ap", qh="qh", c="ALL", s="se", fl="230", o="c"),
        repo,
        make_config(),
    )

    # applied filter -> counted with filters, page rendered
    repo.count_results.assert_awaited()
    telegram_message.edit_text.assert_awaited()


async def test_filter_apply_from_list_returns_to_list() -> None:
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=9)
    repo.count_results.return_value = 2
    repo.get_result_list.return_value = [
        make_torrent_model(id=i, hash=f"h{i}", title=f"Item {i}") for i in range(2)
    ]

    await filter_menu(
        query,
        Flt(a="ap", qh="qh", c="ALL", s="se", fl="230", o="l"),
        repo,
        make_config(),
    )

    text = telegram_message.edit_text.await_args.args[0]
    assert "Результаты 1–2" in text  # back to the list, not the card
    repo.get_result_page.assert_not_awaited()


async def test_render_page_carries_filter_into_buttons() -> None:
    from tj_bot.handlers.user import render_page

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=5)
    repo.count_results.return_value = 5
    repo.get_result_page.return_value = make_torrent_model()
    repo.is_favorite.return_value = False

    await render_page(query, repo, make_config(), "qh", 1, "ALL", "se", "230")

    kb = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    from tj_bot.handlers.user import Flt, Pg2

    fls = []
    for row in kb.inline_keyboard:
        for b in row:
            if b.callback_data and b.callback_data.startswith("pg2:"):
                fls.append(Pg2.unpack(b.callback_data).fl)
            if b.callback_data and b.callback_data.startswith("flt:"):
                fls.append(Flt.unpack(b.callback_data).fl)
    # every pagination/filter button keeps the applied filter code, not "000"
    assert fls and all(x == "230" for x in fls)


async def test_filter_reset_clears_filter_and_category() -> None:
    from tj_bot.handlers.user import Flt, filter_menu

    query = AsyncMock()
    telegram_message = AsyncMock(spec=Message)
    telegram_message.edit_text = AsyncMock()
    query.message = telegram_message
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_search.return_value = SearchQuery(id=1, hash="qh", result_count=5)
    repo.get_result_page.return_value = make_torrent_model()
    repo.is_favorite.return_value = False

    # reset from an active filter + category -> the unfiltered card; category
    # is one of the filters now, so it is cleared as well (no count_results)
    await filter_menu(
        query,
        Flt(a="rs", qh="qh", c=category_token("Movies"), s="se", fl="230", o="c"),
        repo,
        make_config(),
    )

    assert repo.get_result_page.await_args.args[1] is None  # category cleared
    kb = telegram_message.edit_text.await_args.kwargs["reply_markup"]
    from tj_bot.handlers.user import Pg2

    for row in kb.inline_keyboard:
        for b in row:
            if b.callback_data and b.callback_data.startswith("pg2:"):
                unpacked = Pg2.unpack(b.callback_data)
                assert unpacked.fl == "000"
                assert unpacked.c == ALL_CATEGORIES


def test_result_keyboard_layout_admin_and_saved() -> None:
    from tj_bot.handlers.user import result_keyboard

    kb = result_keyboard(
        "h1",
        "q" * 32,
        13,
        ALL_CATEGORIES,
        "se",
        has_prev=True,
        has_next=True,
        show_server=True,
        is_fav=True,
    )
    rows = kb.inline_keyboard
    assert [b.text for b in rows[0]] == ["📋 Список", "Сиды ↓", "🔎 Фильтр"]
    assert [b.text for b in rows[1]] == ["💾 Скачать", "⭐ Сохранено", "⬇️ На сервер"]
    assert [b.text for b in rows[2]] == ["⬅", "🔄", "➡"]
    # the list button opens the list page containing this card (13 // 10 = 1)
    assert rows[0][0].callback_data is not None
    lst = Pg2.unpack(rows[0][0].callback_data)
    assert lst.t == "ls"
    assert lst.p == 1
    assert all(
        len(b.callback_data.encode()) <= 64
        for row in rows
        for b in row
        if b.callback_data
    )
