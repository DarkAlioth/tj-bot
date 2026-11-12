import datetime
import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.filters import category_token
from tj_bot.db.models import SearchQuery, Torrent, WidgetMessage
from tj_bot.db.repo import TorrentData, TorrentRepo
from tj_bot.filters.widgets import WidgetUser
from tj_bot.handlers import widgets_search
from tj_bot.handlers.user import STALE_QUERY_TEXT, Hst, Upd
from tj_bot.handlers.widgets_search import (
    Ctx,
    apply_enrichment,
    apply_filters,
    card_navigate,
    download_torrent,
    list_page,
    list_verb,
    more_details,
    open_card,
    open_filters,
    parse_ctx,
    push_enrichment,
    refresh_widget,
    register_enrichment_push,
    repeat_last_widget,
    replay_history_widget,
    search_widget,
    send_to_server,
    toggle_classic,
    toggle_favorite_widget,
)
from tj_bot.services.enrichment import CardMeta
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    SendWidget,
    WidgetQuery,
)

GB = 1024**3
QH = "a1b2c3d4" + "0" * 24
HASH = "8f3a1c2b" + "f" * 24
HASH2 = "2c9e77aa" + "e" * 24
CHAT_ID = 111
CARD_MID = 7
LIST_MID = 8
SHEET_MID = 9
PHOTO_MID = 6
CARD_CONTEXT: dict[str, Any] = {
    "qh": QH, "i": 3, "n": 10, "s": "se", "f": "000", "c": "ALL", "h": HASH,
    "fc": 0, "adm": 0, "en": 0,
}  # fmt: skip


def make_torrent(**overrides: Any) -> Torrent:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "id": 1,
        "hash": HASH,
        "title": "Дюна: Часть вторая / Dune: Part Two (2024) BDRemux 2160p",
        "uploader": "CEBEPu2D",
        "description": None,
        "category": "Movies/HD",
        "tracker": "rutracker",
        "details_url": "https://rutracker.org/forum/viewtopic.php?t=6512345",
        "download_url": "http://jackett:9117/dl/1",
        "seeders": 1543,
        "peers": 120,
        "published_at": datetime.date(2024, 3, 1),
        "size": 45 * GB,
    }
    values.update(overrides)
    return Torrent(**values)


def make_item() -> TorrentData:
    return TorrentData(
        hash=HASH,
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
        size=GB,
    )


def make_search(hash_: str = QH, result_count: int = 10) -> SearchQuery:
    return SearchQuery(id=1, hash=hash_, result_count=result_count, query_text="дюна")


def make_row(
    kind: str,
    context: dict[str, Any],
    rev: int = 5,
    message_id: int = CARD_MID,
) -> WidgetMessage:
    return WidgetMessage(
        chat_id=CHAT_ID, message_id=message_id, kind=kind, context=context, rev=rev
    )


def make_config(admin: bool = False, qbit_enabled: bool = False) -> AppConfig:
    config = MagicMock()
    config.qbit_enabled = qbit_enabled
    config.is_admin = lambda _uid: admin
    config.settings.search_cache_seconds = 3600
    config.settings.qbit_category = "tj-bot"
    return cast(AppConfig, config)


def make_bot() -> AsyncMock:
    """A bot whose ``await bot(method)`` returns a sent message for sendWidget."""
    bot = AsyncMock()
    sent = MagicMock()
    sent.message_id = 42
    bot.side_effect = lambda method: sent if isinstance(method, SendWidget) else True
    photo = MagicMock(spec=Message)
    photo.message_id = PHOTO_MID
    photo.photo = [MagicMock(file_id="fid-sent")]
    bot.send_photo.return_value = photo
    edited = MagicMock(spec=Message)
    edited.photo = [MagicMock(file_id="fid-edited")]
    bot.edit_message_media.return_value = edited
    return bot


def make_query(data: str, bot: AsyncMock, message_id: int = CARD_MID) -> WidgetQuery:
    return WidgetQuery.model_validate(
        {
            "id": "q1",
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": "A"},
            "chat": {"id": CHAT_ID, "type": "private"},
            "message_id": message_id,
            "chat_instance": "ci",
            "data": data,
        },
        context={"bot": bot},
    )


def make_enricher(meta: CardMeta | None = None) -> AsyncMock:
    enricher = AsyncMock()
    enricher.card_meta.return_value = meta or CardMeta()
    enricher.prefetch = MagicMock()
    enricher.on_ready = MagicMock()
    return enricher


def make_repo(rows: dict[int, WidgetMessage] | None = None) -> AsyncMock:
    repo = AsyncMock(spec=TorrentRepo)
    rows = rows or {}
    repo.get_widget_message.side_effect = lambda _chat, mid: rows.get(mid)
    repo.get_search.return_value = make_search()
    repo.get_categories.return_value = [("Movies/HD", 8), ("TV/HD", 2)]
    repo.is_favorite.return_value = False
    repo.uses_classic_ui.return_value = False
    return repo


def methods(bot: AsyncMock, kind: type) -> list[Any]:
    return [c.args[0] for c in bot.await_args_list if isinstance(c.args[0], kind)]


def answer(bot: AsyncMock) -> dict[str, Any]:
    sent = methods(bot, AnswerWidgetQuery)
    assert len(sent) == 1
    return cast(dict[str, Any], json.loads(sent[0].data))


def edited_state(bot: AsyncMock, message_id: int) -> dict[str, Any]:
    for method in methods(bot, EditMessageWidgetData):
        if method.message_id == message_id:
            return cast(dict[str, Any], json.loads(method.chat_data))
    raise AssertionError(f"no widget edit for message {message_id}")


# --- routing -----------------------------------------------------------------


async def test_widget_ui_filter_follows_the_classic_flag() -> None:
    repo = make_repo()
    event = MagicMock()
    event.from_user.id = 5

    assert await WidgetUser()(event, repo) is True
    repo.uses_classic_ui.return_value = True
    assert await WidgetUser()(event, repo) is False
    event.from_user = None
    assert await WidgetUser()(event, repo) is False


def test_parse_ctx() -> None:
    assert parse_ctx(["a1b2c3d4", "17", "sz", "120", "ALL"]) == Ctx(
        "a1b2c3d4", 17, "sz", "120", "ALL"
    )
    assert parse_ctx(["a1b2c3d4", "0", "se", "000", "9f1c2b3a", "x"]) is not None
    assert parse_ctx(["a1b2c3d4", "0", "se", "000"]) is None
    assert parse_ctx(["A1B2C3D4", "0", "se", "000", "ALL"]) is None
    assert parse_ctx(["a1b2c3d4", "-1", "se", "000", "ALL"]) is None
    assert parse_ctx(["a1b2c3d4", "0", "xx", "000", "ALL"]) is None
    assert parse_ctx(["a1b2c3d4", "0", "se", "12", "ALL"]) is None
    assert parse_ctx(["a1b2c3d4", "0", "se", "000", "nope"]) is None


async def test_toggle_classic_flips_the_flag() -> None:
    message = AsyncMock()
    message.from_user.id = CHAT_ID
    repo = make_repo()

    await toggle_classic(cast(Message, message), repo)
    repo.set_classic_ui.assert_awaited_once_with(CHAT_ID, True)
    assert "включён" in message.answer.await_args.args[0]

    repo.uses_classic_ui.return_value = True
    await toggle_classic(cast(Message, message), repo)
    repo.set_classic_ui.assert_awaited_with(CHAT_ID, False)
    assert "выключен" in message.answer.await_args.args[0]


# --- /s, /last, history, refresh ------------------------------------------------


def make_message() -> AsyncMock:
    message = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = CHAT_ID
    placeholder = AsyncMock()
    placeholder.chat.id = CHAT_ID
    message.answer.return_value = placeholder
    return message


async def test_search_sends_poster_then_card_and_prefetches() -> None:
    message = make_message()
    repo = make_repo()
    repo.find_recent_search.return_value = make_search(result_count=5)
    repo.get_result_page.return_value = make_torrent()
    following = [make_torrent(id=2, hash=HASH2), make_torrent(id=3, hash="c" * 32)]
    repo.get_result_list.return_value = following
    enricher = make_enricher(CardMeta(poster_bytes=b"jpg", pending=True))
    bot = make_bot()
    jackett = AsyncMock(spec=JackettClient)

    await search_widget(
        cast(Message, message),
        repo,
        jackett,
        make_config(),
        enricher,
        bot,
        CommandObject(prefix="/", command="s", args="дюна"),
    )

    jackett.search.assert_not_awaited()
    repo.record_search_event.assert_awaited_once_with(CHAT_ID, 1)
    enricher.card_meta.assert_awaited_once_with(
        repo.get_result_page.return_value, budget=6.0
    )
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.args[0] == CHAT_ID
    enricher.remember_poster.assert_awaited_once_with(HASH, "fid-sent")
    (widget,) = methods(bot, SendWidget)
    state = json.loads(widget.chat_data)
    assert (state["i"], state["n"], state["fc"], state["en"]) == (0, 5, 1, 1)
    assert widget.caption.startswith("Дюна: Часть вторая · 45 ГБ")
    assert widget.reply_markup.inline_keyboard[0][0].text == "🔄"
    assert Upd.unpack(widget.reply_markup.inline_keyboard[0][0].callback_data).qh == QH
    repo.save_widget_message.assert_awaited_once()
    context = repo.save_widget_message.await_args.args[3]
    assert context["h"] == HASH and context["photo_mid"] == PHOTO_MID
    assert context["en"] == 1 and context["n"] == 5
    message.answer.return_value.delete.assert_awaited_once()
    enricher.prefetch.assert_called_once_with(following)
    assert repo.get_result_list.await_args.args[1:3] == (3, 1)


async def test_search_without_poster_skips_the_photo_and_survives_send_errors() -> None:
    message = make_message()
    repo = make_repo()
    repo.find_recent_search.return_value = make_search()
    repo.get_result_page.return_value = make_torrent()
    repo.get_result_list.return_value = []
    bot = make_bot()
    bot.send_photo.side_effect = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="bad"
    )
    enricher = make_enricher(CardMeta(poster_file_id="fid"))

    await search_widget(
        cast(Message, message),
        repo,
        AsyncMock(spec=JackettClient),
        make_config(),
        enricher,
        bot,
        CommandObject(prefix="/", command="s", args="дюна"),
    )

    assert "photo_mid" not in repo.save_widget_message.await_args.args[3]
    enricher.remember_poster.assert_not_awaited()
    assert len(methods(bot, SendWidget)) == 1


async def test_search_usage_hint_and_no_results() -> None:
    message = make_message()
    repo = make_repo()
    bot = make_bot()

    await search_widget(
        cast(Message, message),
        repo,
        AsyncMock(spec=JackettClient),
        make_config(),
        make_enricher(),
        bot,
        CommandObject(prefix="/", command="s", args=None),
    )
    assert "/s Название" in message.answer.await_args.args[0]

    repo.find_recent_search.return_value = None
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.return_value = []
    await search_widget(
        cast(Message, message),
        repo,
        jackett,
        make_config(),
        make_enricher(),
        bot,
        CommandObject(prefix="/", command="s", args="дюна"),
    )
    placeholder = message.answer.return_value
    assert "не найдено" in placeholder.edit_text.await_args.args[0]
    assert methods(bot, SendWidget) == []


async def test_last_replays_history_or_hints() -> None:
    message = make_message()
    repo = make_repo()
    repo.get_user_history.return_value = []
    bot = make_bot()

    await repeat_last_widget(
        cast(Message, message), repo, AsyncMock(spec=JackettClient), make_config(),
        make_enricher(), bot,
    )  # fmt: skip
    assert "пуста" in message.answer.await_args.args[0]

    repo.get_user_history.return_value = [(1, "дюна")]
    repo.find_recent_search.return_value = make_search()
    repo.get_result_page.return_value = make_torrent()
    repo.get_result_list.return_value = []
    await repeat_last_widget(
        cast(Message, message), repo, AsyncMock(spec=JackettClient), make_config(),
        make_enricher(), bot,
    )  # fmt: skip
    assert repo.find_recent_search.await_args.args[0] == "дюна"
    assert len(methods(bot, SendWidget)) == 1


def make_callback() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = CHAT_ID
    query.message = AsyncMock(spec=Message)
    query.message.answer = make_message().answer
    query.answer = AsyncMock()
    return query


async def test_history_replay_and_refresh_send_new_cards() -> None:
    repo = make_repo()
    repo.get_query_text.return_value = None
    bot = make_bot()
    query = make_callback()

    await replay_history_widget(
        query, Hst(qid=3), repo, AsyncMock(spec=JackettClient), make_config(),
        make_enricher(), bot,
    )  # fmt: skip
    query.answer.assert_awaited_once_with(STALE_QUERY_TEXT, show_alert=True)

    repo.get_query_text.return_value = "дюна"
    repo.find_recent_search.return_value = None
    repo.upsert_torrents.return_value = [1]
    repo.create_search.return_value = 1
    repo.get_result_page.return_value = make_torrent()
    repo.get_result_list.return_value = []
    jackett = AsyncMock(spec=JackettClient)
    jackett.search.return_value = [make_item()]
    query = make_callback()
    await refresh_widget(
        query, Upd(qh=QH), repo, jackett, make_config(), make_enricher(), bot
    )
    query.answer.assert_awaited_once_with("Обновляю…")
    # a refresh never reads the cache
    repo.find_recent_search.assert_not_awaited()
    jackett.search.assert_awaited_once_with("дюна")
    assert len(methods(bot, SendWidget)) == 1

    query = make_callback()
    await replay_history_widget(
        query, Hst(qid=3), repo, jackett, make_config(), make_enricher(), bot
    )
    assert len(methods(bot, SendWidget)) == 2

    repo.get_search.return_value = None
    query = make_callback()
    await refresh_widget(
        query, Upd(qh=QH), repo, jackett, make_config(), make_enricher(), bot
    )
    query.answer.assert_awaited_once_with(STALE_QUERY_TEXT, show_alert=True)


# --- card verbs ---------------------------------------------------------------


def card_repo(context: dict[str, Any] | None = None, rev: int = 5) -> AsyncMock:
    row = make_row("search", context or CARD_CONTEXT, rev=rev)
    repo = make_repo({CARD_MID: row})
    repo.get_result_page.return_value = make_torrent(
        id=2, hash=HASH2, title="Дюна / Dune (2021)"
    )
    return repo


async def test_next_answers_full_state_then_edits_card_and_poster() -> None:
    repo = card_repo({**CARD_CONTEXT, "photo_mid": PHOTO_MID})
    bot = make_bot()
    enricher = make_enricher(CardMeta(poster_file_id="fid2", genres=("драма",)))
    query = make_query("n:a1b2c3d4:3:se:000:ALL", bot)

    await card_navigate(
        query,
        "n",
        ["a1b2c3d4", "3", "se", "000", "ALL"],
        repo,
        make_config(),
        enricher,
        bot,
    )

    assert repo.get_result_page.await_args.args == (
        QH,
        None,
        4,
        "se",
        repo.get_result_page.await_args.args[4],
    )
    enricher.card_meta.assert_awaited_once_with(
        repo.get_result_page.return_value, budget=3.0
    )
    state = answer(bot)
    assert (state["i"], state["r"], state["h"], state["tr"], state["ml"]) == (
        4, 6, "2c9e77aa", "Дюна", "драма",
    )  # fmt: skip
    assert edited_state(bot, CARD_MID) == state
    # answered before the edit, poster edited in between
    kinds = [type(c.args[0]).__name__ for c in bot.await_args_list]
    assert kinds == ["AnswerWidgetQuery", "EditMessageWidgetData"]
    bot.edit_message_media.assert_awaited_once()
    media = bot.edit_message_media.await_args.args[0]
    assert media.media == "fid2"
    assert bot.edit_message_media.await_args.kwargs == {
        "chat_id": CHAT_ID,
        "message_id": PHOTO_MID,
    }
    enricher.remember_poster.assert_awaited_once_with(HASH2, "fid-edited")
    repo.update_widget_message.assert_awaited_once()
    context = repo.update_widget_message.await_args.kwargs["context"]
    assert (context["i"], context["h"], context["photo_mid"], context["en"]) == (
        4,
        HASH2,
        PHOTO_MID,
        0,
    )
    assert repo.update_widget_message.await_args.kwargs["rev"] == 6


@pytest.mark.parametrize(("pending", "deleted"), [(True, False), (False, True)])
async def test_prev_without_poster_deletes_the_photo_unless_pending(
    pending: bool, deleted: bool
) -> None:
    repo = card_repo({**CARD_CONTEXT, "photo_mid": PHOTO_MID})
    bot = make_bot()
    query = make_query("p:a1b2c3d4:3:se:000:ALL", bot)

    await card_navigate(
        query, "p", ["a1b2c3d4", "3", "se", "000", "ALL"], repo, make_config(),
        make_enricher(CardMeta(pending=pending)), bot,
    )  # fmt: skip

    assert repo.get_result_page.await_args.args[2] == 2
    bot.edit_message_media.assert_not_awaited()
    assert bot.delete_message.await_count == int(deleted)
    context = repo.update_widget_message.await_args.kwargs["context"]
    assert ("photo_mid" in context) is not deleted
    assert answer(bot).get("en") == (1 if pending else None)


async def test_card_without_photo_never_gets_one() -> None:
    repo = card_repo()
    bot = make_bot()
    query = make_query("n:a1b2c3d4:3:se:000:ALL", bot)

    await card_navigate(
        query, "n", ["a1b2c3d4", "3", "se", "000", "ALL"], repo, make_config(),
        make_enricher(CardMeta(poster_file_id="fid")), bot,
    )  # fmt: skip

    bot.send_photo.assert_not_awaited()
    bot.edit_message_media.assert_not_awaited()
    assert "photo_mid" not in repo.update_widget_message.await_args.kwargs["context"]


async def test_sort_goes_to_card_zero_with_the_next_code_and_admin_flag() -> None:
    repo = card_repo({**CARD_CONTEXT, "c": "9f1c2b3a", "f": "100"})
    repo.get_categories.return_value = [("Movies/HD", 8)]
    repo.count_results.return_value = 8
    bot = make_bot()
    token = category_token("Movies/HD")
    query = make_query(f"s:a1b2c3d4:3:sz:100:{token}", bot)

    await card_navigate(
        query, "s", ["a1b2c3d4", "3", "sz", "100", token], repo,
        make_config(admin=True, qbit_enabled=True), make_enricher(), bot,
    )  # fmt: skip

    args = repo.get_result_page.await_args.args
    assert args[:4] == (QH, "Movies/HD", 0, "sz")
    state = answer(bot)
    assert (state["i"], state["s"], state["sl"], state["n"], state["c"]) == (
        0, "sz", "Размер ↓", 8, token,
    )  # fmt: skip
    assert (state["fa"], state["adm"]) == (1, 1)


async def test_navigation_rejects_stale_or_invalid_context() -> None:
    bot = make_bot()
    config = make_config()
    enricher = make_enricher()
    cases = [
        (make_repo(), ["a1b2c3d4", "3", "se", "000", "ALL"], "n"),  # unknown widget
        (
            card_repo({**CARD_CONTEXT, "qh": "f" * 32}),
            ["a1b2c3d4", "3", "se", "000", "ALL"],
            "n",
        ),
        (card_repo(), ["bad"], "n"),
        (card_repo(), ["a1b2c3d4", "0", "se", "000", "ALL"], "p"),  # index -1
    ]
    for repo, args, verb in cases:
        bot.reset_mock()
        await card_navigate(
            make_query("x", bot), verb, args, repo, config, enricher, bot
        )
        assert answer(bot) == {"err": STALE_QUERY_TEXT}
        assert methods(bot, EditMessageWidgetData) == []

    repo = card_repo()
    repo.get_search.return_value = None
    bot.reset_mock()
    await card_navigate(
        make_query("x", bot),
        "n",
        ["a1b2c3d4", "3", "se", "000", "ALL"],
        repo,
        config,
        enricher,
        bot,
    )
    assert answer(bot) == {"err": STALE_QUERY_TEXT}

    repo = card_repo()
    repo.get_result_page.return_value = None
    bot.reset_mock()
    await card_navigate(
        make_query("x", bot),
        "n",
        ["a1b2c3d4", "9", "se", "000", "ALL"],
        repo,
        config,
        enricher,
        bot,
    )
    assert answer(bot) == {"err": STALE_QUERY_TEXT}


async def test_list_from_card_sends_the_page_of_the_card_and_records_it() -> None:
    repo = card_repo()
    repo.get_result_list.return_value = [
        make_torrent(id=i, hash=f"{i:08x}" + "0" * 24) for i in range(10)
    ]
    bot = make_bot()
    query = make_query("ls:a1b2c3d4:13:se:000:ALL", bot)

    await list_verb(query, ["a1b2c3d4", "13", "se", "000", "ALL"], repo, bot)

    assert answer(bot) == {"ok": 1}
    assert repo.get_result_list.await_args.args[1:3] == (10, 10)
    (widget,) = methods(bot, SendWidget)
    state = json.loads(widget.chat_data)
    assert (state["i0"], state["n"], state["q"], len(state["rows"])) == (
        10,
        10,
        "дюна",
        10,
    )
    assert widget.caption == "Результаты 11–20 из 10 по запросу «дюна» · Сиды ↓"
    repo.save_widget_message.assert_awaited_once_with(
        CHAT_ID, 42, "search-list", {"qh": QH, "card": CARD_MID}, rev=1
    )
    repo.update_widget_message.assert_awaited_once_with(
        CHAT_ID, CARD_MID, context={**CARD_CONTEXT, "list": 42}
    )


async def test_list_sort_chip_resorts_to_page_zero_in_place() -> None:
    row = make_row(
        "search-list", {"qh": QH, "card": CARD_MID}, rev=8, message_id=LIST_MID
    )
    repo = make_repo({LIST_MID: row})
    repo.get_result_list.return_value = [make_torrent()]
    bot = make_bot()
    query = make_query("ls:a1b2c3d4:20:dt:000:ALL", bot, message_id=LIST_MID)

    await list_verb(query, ["a1b2c3d4", "20", "dt", "000", "ALL"], repo, bot)

    assert repo.get_result_list.await_args.args[1:5] == (10, 0, None, "dt")
    state = answer(bot)
    assert (state["i0"], state["s"], state["r"]) == (0, "dt", 9)
    assert edited_state(bot, LIST_MID) == state
    assert methods(bot, SendWidget) == []


async def test_list_page_turns_and_validates_the_page() -> None:
    row = make_row(
        "search-list", {"qh": QH, "card": CARD_MID}, rev=8, message_id=LIST_MID
    )
    repo = make_repo({LIST_MID: row})
    repo.get_result_list.return_value = [make_torrent()]
    bot = make_bot()
    ctx = ["a1b2c3d4", "0", "se", "000", "ALL"]

    await list_page(make_query("lp", bot, LIST_MID), [*ctx, "2"], repo, bot)
    assert repo.get_result_list.await_args.args[2] == 20
    assert answer(bot)["i0"] == 20
    assert edited_state(bot, LIST_MID)["i0"] == 20

    bot.reset_mock()
    await list_page(make_query("lp", bot, LIST_MID), [*ctx, "x"], repo, bot)
    assert answer(bot) == {"err": STALE_QUERY_TEXT}

    bot.reset_mock()
    await list_page(make_query("lp", bot, CARD_MID), [*ctx, "1"], make_repo(), bot)
    assert answer(bot) == {"err": STALE_QUERY_TEXT}


async def test_filters_from_the_list_carry_card_and_list_ids() -> None:
    row = make_row(
        "search-list", {"qh": QH, "card": CARD_MID}, rev=8, message_id=LIST_MID
    )
    repo = make_repo({LIST_MID: row})
    bot = make_bot()
    query = make_query("fs:a1b2c3d4:10:se:000:ALL", bot, message_id=LIST_MID)

    await open_filters(query, ["a1b2c3d4", "10", "se", "000", "ALL"], repo, bot)

    kinds = [type(c.args[0]).__name__ for c in bot.await_args_list]
    assert kinds == ["AnswerWidgetQuery", "SendWidget"]
    assert answer(bot) == {"ok": 1}
    (widget,) = methods(bot, SendWidget)
    state = json.loads(widget.chat_data)
    assert (state["i"], state["f"], state["c"], state["n"]) == (10, "000", "ALL", 10)
    assert state["cats"][0] == ["ALL", "Все", 10]
    assert state["cats"][1][1:] == ["Фильмы HD", 8]
    assert widget.caption == "Фильтры поиска «дюна» · Активно: не заданы"
    repo.save_widget_message.assert_awaited_once_with(
        CHAT_ID,
        42,
        "search-filters",
        {"qh": QH, "card": CARD_MID, "list": LIST_MID},
        rev=1,
    )


async def test_filters_from_the_card_pick_up_its_open_list() -> None:
    repo = card_repo({**CARD_CONTEXT, "list": LIST_MID})
    repo.get_categories.return_value = [("Movies/HD", 10)]
    bot = make_bot()
    query = make_query("fs:a1b2c3d4:3:se:000:ALL", bot)

    await open_filters(query, ["a1b2c3d4", "3", "se", "000", "ALL"], repo, bot)

    context = repo.save_widget_message.await_args.args[3]
    assert context == {"qh": QH, "card": CARD_MID, "list": LIST_MID}
    assert "cats" not in json.loads(methods(bot, SendWidget)[0].chat_data)


async def test_open_card_from_the_list_updates_the_card_and_finishes_the_list() -> None:
    list_row = make_row(
        "search-list", {"qh": QH, "card": CARD_MID}, rev=8, message_id=LIST_MID
    )
    card_row = make_row(
        "search", {**CARD_CONTEXT, "photo_mid": PHOTO_MID, "list": LIST_MID}
    )
    repo = make_repo({LIST_MID: list_row, CARD_MID: card_row})
    repo.get_result_page.return_value = make_torrent(id=2, hash=HASH2)
    bot = make_bot()
    query = make_query("cd:a1b2c3d4:10:se:000:ALL:13", bot, message_id=LIST_MID)

    await open_card(
        query, ["a1b2c3d4", "10", "se", "000", "ALL", "13"], repo, make_config(),
        make_enricher(), bot,
    )  # fmt: skip

    assert answer(bot) == {
        "v": 1, "r": 9, "qh": "a1b2c3d4", "q": "дюна", "sl": "Сиды ↓",
        "done": "Открыта карточка 14",
    }  # fmt: skip
    assert repo.get_result_page.await_args.args[2] == 13
    card_state = edited_state(bot, CARD_MID)
    assert (card_state["i"], card_state["h"], card_state["r"]) == (13, "2c9e77aa", 6)
    # no poster now and not pending: the old photo goes away
    bot.delete_message.assert_awaited_once_with(CHAT_ID, PHOTO_MID)
    assert edited_state(bot, LIST_MID)["done"] == "Открыта карточка 14"
    list_update = [
        c for c in repo.update_widget_message.await_args_list if c.args[1] == LIST_MID
    ]
    assert list_update[0].kwargs == {
        "context": {"qh": QH, "card": CARD_MID, "done": 1},
        "rev": 9,
    }


async def test_open_card_survives_a_deleted_card() -> None:
    list_row = make_row(
        "search-list", {"qh": QH, "card": CARD_MID}, rev=8, message_id=LIST_MID
    )
    repo = make_repo({LIST_MID: list_row})
    bot = make_bot()

    await open_card(
        make_query("cd", bot, LIST_MID), ["a1b2c3d4", "10", "se", "000", "ALL", "0"],
        repo, make_config(), make_enricher(), bot,
    )  # fmt: skip

    assert answer(bot)["done"] == "Открыта карточка 1"
    assert [m.message_id for m in methods(bot, EditMessageWidgetData)] == [LIST_MID]


def sheet_repo(list_done: bool = False) -> AsyncMock:
    sheet = make_row(
        "search-filters",
        {"qh": QH, "card": CARD_MID, "list": LIST_MID},
        rev=3,
        message_id=SHEET_MID,
    )
    list_ctx: dict[str, Any] = {"qh": QH, "card": CARD_MID}
    if list_done:
        list_ctx["done"] = 1
    list_row = make_row("search-list", list_ctx, rev=8, message_id=LIST_MID)
    card_row = make_row("search", CARD_CONTEXT)
    repo = make_repo({SHEET_MID: sheet, LIST_MID: list_row, CARD_MID: card_row})
    repo.count_results.return_value = 8
    repo.get_result_page.return_value = make_torrent(id=2, hash=HASH2)
    return repo


async def test_apply_filters_updates_card_list_and_sheet() -> None:
    repo = sheet_repo()
    bot = make_bot()
    token = category_token("Movies/HD")
    args = ["a1b2c3d4", "3", "se", "000", "ALL", "120", token]

    await apply_filters(
        make_query("f", bot, SHEET_MID), args, repo, make_config(), make_enricher(), bot
    )

    text = "Применено: сиды 1+ · 1–5 ГБ · Фильмы HD · 8 результатов"
    assert answer(bot) == {"v": 1, "r": 4, "qh": "a1b2c3d4", "done": text}
    assert repo.count_results.await_args.args[:2] == (QH, "Movies/HD")
    assert repo.get_result_page.await_args.args[:4] == (QH, "Movies/HD", 0, "se")
    card = edited_state(bot, CARD_MID)
    assert (card["i"], card["n"], card["f"], card["c"], card["fa"]) == (
        0,
        8,
        "120",
        token,
        1,
    )
    assert edited_state(bot, LIST_MID) == {
        "v": 1, "r": 9, "qh": "a1b2c3d4", "q": "дюна", "sl": "Сиды ↓", "done": text,
    }  # fmt: skip
    assert edited_state(bot, SHEET_MID)["done"] == text
    updates = {c.args[1]: c.kwargs for c in repo.update_widget_message.await_args_list}
    assert updates[SHEET_MID]["context"]["done"] == 1 and updates[SHEET_MID]["rev"] == 4
    assert updates[LIST_MID]["context"]["done"] == 1


async def test_apply_filters_reset_and_done_list_left_alone() -> None:
    repo = sheet_repo(list_done=True)
    bot = make_bot()
    args = ["a1b2c3d4", "3", "se", "120", "ALL", "000", "ALL"]

    await apply_filters(
        make_query("f", bot, SHEET_MID), args, repo, make_config(), make_enricher(), bot
    )

    assert answer(bot)["done"] == "Применено: фильтры сброшены · 10 результатов"
    repo.count_results.assert_not_awaited()
    assert sorted(m.message_id for m in methods(bot, EditMessageWidgetData)) == [
        CARD_MID,
        SHEET_MID,
    ]


async def test_apply_filters_with_an_empty_set_errors_and_changes_nothing() -> None:
    repo = sheet_repo()
    repo.count_results.return_value = 0
    bot = make_bot()
    args = ["a1b2c3d4", "3", "se", "000", "ALL", "300", "ALL"]

    await apply_filters(
        make_query("f", bot, SHEET_MID), args, repo, make_config(), make_enricher(), bot
    )

    assert answer(bot) == {"err": "Ничего не найдено с этими фильтрами"}
    assert methods(bot, EditMessageWidgetData) == []

    bot.reset_mock()
    await apply_filters(
        make_query("f", bot, SHEET_MID),
        args[:6],
        repo,
        make_config(),
        make_enricher(),
        bot,
    )
    assert answer(bot) == {"err": STALE_QUERY_TEXT}


async def test_download_answers_first_then_sends_the_file() -> None:
    repo = card_repo()
    repo.get_torrent_by_hash_prefix.return_value = make_torrent()
    bot = make_bot()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrent"
    order: list[str] = []

    def download(_url: str) -> bytes:
        order.append("download")
        return b"torrent"

    jackett.download.side_effect = download
    repo.commit.side_effect = lambda: order.append("commit")

    await download_torrent(
        make_query("d:8f3a1c2b", bot), ["8f3a1c2b"], repo, jackett, bot
    )

    repo.get_torrent_by_hash_prefix.assert_awaited_once_with(QH, "8f3a1c2b")
    assert answer(bot) == {"ok": 1}
    assert order == ["commit", "download"]
    bot.send_document.assert_awaited_once()
    assert bot.send_document.await_args.args[0] == CHAT_ID
    assert bot.send_document.await_args.args[1].filename.endswith(".torrent")
    repo.record_download.assert_awaited_once_with(CHAT_ID, make_torrent().title, "chat")


async def test_download_errors_go_to_the_chat() -> None:
    repo = card_repo()
    repo.get_torrent_by_hash_prefix.return_value = make_torrent()
    bot = make_bot()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = JackettError("boom")

    await download_torrent(
        make_query("d:8f3a1c2b", bot), ["8f3a1c2b"], repo, jackett, bot
    )

    assert answer(bot) == {"ok": 1}
    bot.send_document.assert_not_awaited()
    assert "Не удалось скачать" in bot.send_message.await_args.args[1]

    bot.reset_mock()
    repo.get_torrent_by_hash_prefix.return_value = None
    await download_torrent(make_query("d:ZZ", bot), ["ZZ"], repo, jackett, bot)
    assert answer(bot) == {"err": STALE_QUERY_TEXT}
    repo.get_torrent_by_hash_prefix.assert_awaited_once()  # no second lookup: bad hash


async def test_favorite_toggle_answers_the_fragment_and_bumps_the_rev() -> None:
    repo = card_repo(rev=7)
    repo.get_torrent_by_hash_prefix.return_value = make_torrent()
    repo.add_favorite.return_value = True
    bot = make_bot()

    await toggle_favorite_widget(make_query("fav:8f3a1c2b", bot), ["8f3a1c2b"], repo)
    assert answer(bot) == {"fav": 1, "r": 8}
    repo.update_widget_message.assert_awaited_once_with(CHAT_ID, CARD_MID, rev=8)

    bot.reset_mock()
    repo.add_favorite.return_value = False
    await toggle_favorite_widget(make_query("fav:8f3a1c2b", bot), ["8f3a1c2b"], repo)
    assert answer(bot) == {"fav": 0, "r": 8}
    repo.remove_favorite_by_hash.assert_awaited_once_with(CHAT_ID, HASH)


async def test_send_to_server_requires_admin_and_qbit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = card_repo()
    repo.get_torrent_by_hash_prefix.return_value = make_torrent()
    bot = make_bot()
    begin = AsyncMock()
    monkeypatch.setattr(widgets_search, "begin_torrent_send", begin)
    jackett = AsyncMock(spec=JackettClient)

    await send_to_server(
        make_query("srv:x", bot),
        ["8f3a1c2b"],
        repo,
        jackett,
        MagicMock(),
        make_config(),
        bot,
    )
    assert answer(bot) == {"err": "Недостаточно прав"}

    bot.reset_mock()
    await send_to_server(
        make_query("srv:x", bot),
        ["8f3a1c2b"],
        repo,
        jackett,
        None,
        make_config(admin=True, qbit_enabled=True),
        bot,
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}
    begin.assert_not_awaited()

    bot.reset_mock()
    qbit = MagicMock()
    config = make_config(admin=True, qbit_enabled=True)
    await send_to_server(
        make_query("srv:x", bot), ["8f3a1c2b"], repo, jackett, qbit, config, bot
    )
    assert answer(bot) == {"ok": 1}
    begin.assert_awaited_once()
    assert begin.await_args is not None
    args = begin.await_args.args
    assert (
        args[0] is None and isinstance(args[1], Message) and args[1].chat.id == CHAT_ID
    )
    assert args[2:] == (
        repo,
        jackett,
        qbit,
        config,
        bot,
        make_torrent().title,
        "http://jackett:9117/dl/1",
    )


async def test_more_answers_the_full_plot() -> None:
    repo = card_repo()
    repo.get_torrent_by_hash_prefix.return_value = make_torrent()
    bot = make_bot()
    enricher = make_enricher(CardMeta(plot="Полный сюжет", director="Дени Вильнёв"))

    await more_details(make_query("more:8f3a1c2b", bot), ["8f3a1c2b"], repo, enricher)

    assert answer(bot) == {"pl": "Полный сюжет", "pp": "Реж. Дени Вильнёв"}
    enricher.card_meta.assert_awaited_once_with(
        repo.get_torrent_by_hash_prefix.return_value, budget=3.0
    )


# --- enrichment push -------------------------------------------------------------


async def test_apply_enrichment_redraws_pending_cards_and_their_posters() -> None:
    waiting = make_row(
        "search", {**CARD_CONTEXT, "en": 1, "photo_mid": PHOTO_MID}, rev=3
    )
    done = make_row("search", {**CARD_CONTEXT, "en": 0}, message_id=20)
    other = make_row("search", {**CARD_CONTEXT, "h": HASH2, "en": 1}, message_id=21)
    broken = make_row("search", {**CARD_CONTEXT, "en": 1}, rev=1, message_id=22)
    repo = make_repo()
    repo.widget_messages_of_kind.return_value = [broken, waiting, done, other]
    repo.get_torrent_by_hash.return_value = make_torrent()
    repo.is_favorite.return_value = True
    bot = make_bot()
    not_found = TelegramBadRequest(
        method=EditMessageWidgetData(chat_id=1, message_id=22, chat_data="{}"),
        message="Bad Request: message to edit not found",
    )

    def edit(method: Any) -> bool:  # noqa: ANN401  # mock side effect
        if isinstance(method, EditMessageWidgetData) and method.message_id == 22:
            raise not_found
        return True

    bot.side_effect = edit
    enricher = make_enricher()
    meta = CardMeta(poster_file_id="fid", genres=("драма",), mosaic="abc" * 24)

    await apply_enrichment(bot, repo, enricher, HASH, meta)

    repo.get_torrent_by_hash.assert_awaited_once_with(HASH)
    state = edited_state(bot, CARD_MID)
    assert (state["r"], state["ml"], state["m"], state["fav"]) == (
        4,
        "драма",
        "abc" * 24,
        1,
    )
    assert "en" not in state
    assert [m.message_id for m in methods(bot, EditMessageWidgetData)] == [22, CARD_MID]
    bot.edit_message_media.assert_awaited_once()
    assert bot.edit_message_media.await_args.kwargs["message_id"] == PHOTO_MID
    updated = {c.args[1]: c.kwargs for c in repo.update_widget_message.await_args_list}
    assert updated[CARD_MID]["context"]["en"] == 0 and updated[CARD_MID]["rev"] == 4
    assert 22 not in updated


async def test_apply_enrichment_without_the_torrent_does_nothing() -> None:
    repo = make_repo()
    repo.widget_messages_of_kind.return_value = [
        make_row("search", {**CARD_CONTEXT, "en": 1})
    ]
    repo.get_torrent_by_hash.return_value = None
    bot = make_bot()

    await apply_enrichment(bot, repo, make_enricher(), HASH, CardMeta())

    bot.assert_not_awaited()


class FakePool:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    def __call__(self) -> "FakePool":
        return self

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_register_and_push_open_a_session_and_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo()
    repo.widget_messages_of_kind.return_value = []
    monkeypatch.setattr(widgets_search, "TorrentRepo", lambda _session: repo)
    session = AsyncMock()
    pool = FakePool(session)
    enricher = make_enricher()
    bot = make_bot()

    register_enrichment_push(bot, cast(Any, pool), enricher)
    enricher.on_ready.assert_called_once()
    callback = enricher.on_ready.call_args_list[0].args[0]
    await callback(HASH, CardMeta())

    repo.widget_messages_of_kind.assert_awaited_once_with("search")
    session.commit.assert_awaited_once()

    from sqlalchemy.exc import SQLAlchemyError

    repo.widget_messages_of_kind.side_effect = SQLAlchemyError("down")
    await push_enrichment(
        bot, cast(Any, pool), enricher, HASH, CardMeta()
    )  # logged, not raised
