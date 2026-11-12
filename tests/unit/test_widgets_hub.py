import datetime
import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Message, Update

from tj_bot.config import AppConfig
from tj_bot.db.repo import HistoryEntry, TorrentRepo
from tj_bot.handlers import routers_list
from tj_bot.handlers.favorites import GONE_TEXT, favorites_router
from tj_bot.handlers.server_download import QBIT_DOWN_TEXT
from tj_bot.handlers.user import SEARCHING_TEXT, user_router
from tj_bot.handlers.widgets_hub import (
    DOWNLOAD_FAILED_TEXT,
    NO_RIGHTS_TEXT,
    SOON_TEXT,
    STALE_QUERY_TEXT,
    STALE_TEXT,
    TOO_LARGE_TEXT,
    UNKNOWN_VERB_TEXT,
    HubQuery,
    clear_history,
    context_view,
    download_favorite,
    favorite_to_server,
    hub_favorites,
    hub_history,
    hub_message,
    hub_router,
    hub_start,
    open_admin_widget,
    open_favorite,
    remove_favorite,
    replay_search,
    switch_view,
    unknown_verb,
)
from tj_bot.services.hub_views import HubStatus
from tj_bot.services.jackett import DownloadTooLargeError, JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient
from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    SendWidget,
    WidgetQuery,
)
from tj_bot.widgets.dispatch import WidgetQueryMiddleware

USER_ID = 111
CHAT_ID = 111
MESSAGE_ID = 4242
NOW = datetime.datetime(2026, 11, 12, 12, 40, tzinfo=datetime.UTC)


def make_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.return_value = True
    sent = MagicMock(spec=Message)
    sent.message_id = 99
    bot.send_message.return_value = sent
    return bot


def make_query(data: str, bot: AsyncMock, chat: bool = True) -> WidgetQuery:
    raw: dict[str, Any] = {
        "id": "q1",
        "from": {"id": USER_ID, "is_bot": False, "first_name": "Alice"},
        "chat_instance": "ci",
        "data": data,
    }
    if chat:
        raw |= {"chat": {"id": CHAT_ID, "type": "private"}, "message_id": MESSAGE_ID}
    return WidgetQuery.model_validate(raw, context={"bot": bot})


def make_hub(
    tab: str = "m", page: int = 0, favorites_filter: str = "a", rev: int = 7
) -> MagicMock:
    hub = MagicMock()
    hub.chat_id = CHAT_ID
    hub.message_id = MESSAGE_ID
    hub.kind = "hub"
    hub.rev = rev
    hub.context = {"tab": tab, "page": page, "filter": favorites_filter}
    return hub


def make_favorite(fav_id: int = 501) -> MagicMock:
    favorite = MagicMock()
    favorite.id = fav_id
    favorite.title = "Movie &amp; Co 1080p"
    favorite.tracker = "rutracker"
    favorite.size = 2 * 1024**3
    favorite.published_at = datetime.date(2026, 11, 2)
    favorite.seeders = 12
    favorite.updated_at = None
    favorite.seen_at = None
    favorite.details_url = "https://rutracker.org/forum/viewtopic.php?t=1"
    favorite.download_url = "http://jackett:9117/dl/1"
    return favorite


def make_repo(total: int = 7, updated: int = 2) -> AsyncMock:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_favorites.return_value = total
    repo.count_updated_favorites.return_value = updated
    repo.list_favorites.return_value = [make_favorite(501), make_favorite(498)]
    repo.get_user_history_entries.return_value = [
        HistoryEntry(1041, "дюна часть вторая", NOW, 57)
    ]
    repo.get_favorite.return_value = make_favorite()
    return repo


def make_config(admin: bool = False) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda _uid: admin
    config.settings.qbit_category = "tj-bot"
    config.settings.search_cache_seconds = 3600
    return cast(AppConfig, config)


def sent_methods(bot: AsyncMock) -> list[Any]:
    return [call.args[0] for call in bot.await_args_list]


def answers(bot: AsyncMock) -> list[dict[str, Any]]:
    return [
        json.loads(m.data)
        for m in sent_methods(bot)
        if isinstance(m, AnswerWidgetQuery) and m.data
    ]


def edits(bot: AsyncMock) -> list[dict[str, Any]]:
    return [
        json.loads(m.chat_data)
        for m in sent_methods(bot)
        if isinstance(m, EditMessageWidgetData) and m.chat_data
    ]


class Monitor:
    def hub_status(self) -> HubStatus | None:
        return HubStatus(jackett_ok=True, qbit_ok=False, at=NOW)


# --- filter ----------------------------------------------------------------


async def test_hub_query_filter_passes_hub_rows_and_forgotten_messages() -> None:
    bot = make_bot()
    repo = make_repo()
    hub = make_hub()
    repo.get_widget_message.return_value = hub
    assert await HubQuery()(make_query("t:m", bot), repo) == {"hub": hub}
    repo.get_widget_message.return_value = None
    assert await HubQuery()(make_query("t:m", bot), repo) == {"hub": None}
    assert await HubQuery()(make_query("t:m", bot, chat=False), repo) is False
    other = make_hub()
    other.kind = "search"
    repo.get_widget_message.return_value = other
    assert await HubQuery()(make_query("t:m", bot), repo) is False


# --- opening ---------------------------------------------------------------


def make_message(user: bool = True) -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = CHAT_ID
    message.from_user = None
    if user:
        message.from_user = MagicMock()
        message.from_user.id = USER_ID
    return message


@pytest.mark.parametrize(
    ("handler", "tab"), [(hub_start, "m"), (hub_history, "h"), (hub_favorites, "f")]
)
async def test_commands_send_the_hub_on_their_tab(handler: Any, tab: str) -> None:  # noqa: ANN401  # handler coroutine functions
    bot = make_bot()
    sent = MagicMock(spec=Message)
    sent.message_id = 42
    bot.return_value = sent
    repo = make_repo()

    await handler(make_message(), repo, make_config(admin=True), bot, Monitor())

    method = sent_methods(bot)[0]
    assert isinstance(method, SendWidget)
    assert method.chat_id == CHAT_ID
    state = json.loads(method.chat_data or "")
    assert state["tb"] == tab and state["r"] == 1 and state["adm"] == 1
    assert ("st" in state) == (tab == "m")
    repo.save_widget_message.assert_awaited_once()
    assert repo.save_widget_message.await_args.args[2] == "hub"
    assert repo.save_widget_message.await_args.args[3]["tab"] == tab


async def test_open_without_user_is_ignored() -> None:
    bot = make_bot()
    await hub_start(make_message(user=False), make_repo(), make_config(), bot)
    bot.assert_not_awaited()


async def test_tab_switch_answers_fragment_then_edits_state() -> None:
    bot = make_bot()
    repo = make_repo()
    hub = make_hub(tab="m")

    await switch_view(make_query("t:f", bot), "t", ["f"], hub, repo, make_config(), bot)

    fragment = answers(bot)[0]
    assert fragment["r"] == 8 and fragment["tb"] == "f"
    assert fragment["ff"] == "a" and fragment["fp"] == 0
    assert [row["i"] for row in fragment["f"]] == [501, 498]
    assert fragment["f"][0]["n"] == "Movie & Co 1080p"
    assert fragment["fn"] == 7 and fragment["fu"] == 2
    assert edits(bot) == [fragment]
    # the answer goes out before the edit
    kinds = [type(m) for m in sent_methods(bot)]
    assert kinds.index(AnswerWidgetQuery) < kinds.index(EditMessageWidgetData)
    repo.update_widget_message.assert_awaited_once_with(
        CHAT_ID, MESSAGE_ID, context={"tab": "f", "page": 0, "filter": "a"}, rev=8
    )
    repo.list_favorites.assert_awaited_once_with(USER_ID, 4, 0, updated_only=False)


async def test_main_tab_carries_the_monitor_status_for_admins() -> None:
    bot = make_bot()
    await switch_view(
        make_query("t:m", bot),
        "t",
        ["m"],
        make_hub(tab="f"),
        make_repo(),
        make_config(admin=True),
        bot,
        Monitor(),
    )
    fragment = answers(bot)[0]
    assert fragment["st"] == {"j": 1, "q": 0, "w": NOW.astimezone().strftime("%H:%M")}
    assert len(fragment["h"]) == 1


async def test_filter_and_page_verbs() -> None:
    bot = make_bot()
    repo = make_repo(total=7, updated=2)
    await switch_view(
        make_query("ff:u", bot),
        "ff",
        ["u"],
        make_hub(tab="f", page=1),
        repo,
        make_config(),
        bot,
    )
    assert answers(bot)[0]["ff"] == "u" and answers(bot)[0]["fp"] == 0
    repo.list_favorites.assert_awaited_with(USER_ID, 4, 0, updated_only=True)

    bot = make_bot()
    await switch_view(
        make_query("fp:5", bot),
        "fp",
        ["5"],
        make_hub(tab="f", favorites_filter="a"),
        repo,
        make_config(),
        bot,
    )
    # 7 favorites = 2 pages: page 5 is clamped to the last one
    assert answers(bot)[0]["fp"] == 1
    repo.list_favorites.assert_awaited_with(USER_ID, 4, 4, updated_only=False)


@pytest.mark.parametrize(
    ("data", "verb", "args"),
    [("t:z", "t", ["z"]), ("t", "t", []), ("ff:q", "ff", ["q"]), ("fp:x", "fp", ["x"])],
)
async def test_bad_view_arguments_are_rejected(
    data: str, verb: str, args: list[str]
) -> None:
    bot = make_bot()
    repo = make_repo()
    await switch_view(
        make_query(data, bot), verb, args, make_hub(), repo, make_config(), bot
    )
    assert answers(bot) == [{"err": UNKNOWN_VERB_TEXT}]
    repo.update_widget_message.assert_not_awaited()


async def test_forgotten_message_is_stale_everywhere() -> None:
    bot = make_bot()
    repo = make_repo()
    config = make_config(admin=True)
    jackett = AsyncMock(spec=JackettClient)
    qbit = AsyncMock(spec=QbittorrentClient)
    await switch_view(make_query("t:f", bot), "t", ["f"], None, repo, config, bot)
    await clear_history(make_query("hc", bot), None, repo, config, bot)
    await replay_search(
        make_query("h:1", bot), ["1"], None, repo, jackett, config, AsyncMock(), bot
    )
    await open_favorite(make_query("fo:1", bot), ["1"], None, repo)
    await download_favorite(make_query("fd:1", bot), ["1"], None, repo, jackett, bot)
    await favorite_to_server(
        make_query("fs:1", bot), ["1"], None, repo, jackett, qbit, config, bot
    )
    await remove_favorite(make_query("fr:1", bot), ["1"], None, repo, config, bot)
    await open_admin_widget(make_query("con", bot), "con", None, repo, config, bot)
    await unknown_verb(make_query("zzz", bot), None)
    assert answers(bot) == [{"err": STALE_TEXT}] * 9
    assert edits(bot) == []


async def test_clear_history_empties_the_tab() -> None:
    bot = make_bot()
    repo = make_repo()
    repo.get_user_history_entries.return_value = []
    await clear_history(
        make_query("hc", bot), make_hub(tab="h"), repo, make_config(), bot
    )
    repo.clear_user_history.assert_awaited_once_with(USER_ID)
    fragment = answers(bot)[0]
    assert fragment["tb"] == "h" and fragment["h"] == [] and fragment["r"] == 8
    assert edits(bot) == [fragment]


async def test_remove_favorite_keeps_filter_and_clamps_page() -> None:
    bot = make_bot()
    repo = make_repo(total=4, updated=0)  # one page left after the removal
    await remove_favorite(
        make_query("fr:501", bot),
        ["501"],
        make_hub(tab="f", page=1, favorites_filter="a"),
        repo,
        make_config(),
        bot,
    )
    repo.remove_favorite.assert_awaited_once_with(USER_ID, 501)
    fragment = answers(bot)[0]
    assert fragment["tb"] == "f" and fragment["fp"] == 0 and fragment["fn"] == 4
    repo.update_widget_message.assert_awaited_once_with(
        CHAT_ID, MESSAGE_ID, context={"tab": "f", "page": 0, "filter": "a"}, rev=8
    )


async def test_remove_favorite_rejects_a_bad_id() -> None:
    bot = make_bot()
    repo = make_repo()
    await remove_favorite(
        make_query("fr:x", bot), ["x"], make_hub(), repo, make_config(), bot
    )
    assert answers(bot) == [{"err": UNKNOWN_VERB_TEXT}]
    repo.remove_favorite.assert_not_awaited()


# --- history replay ----------------------------------------------------------


async def test_replay_answers_ok_then_runs_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_search = AsyncMock()
    monkeypatch.setattr("tj_bot.handlers.widgets_hub.run_widget_search", run_search)
    bot = make_bot()
    repo = make_repo()
    repo.get_query_text.return_value = "dune"
    jackett = AsyncMock(spec=JackettClient)
    config = make_config()
    enricher = AsyncMock()

    await replay_search(
        make_query("h:1041", bot),
        ["1041"],
        make_hub(),
        repo,
        jackett,
        config,
        enricher,
        bot,
    )

    assert answers(bot) == [{"ok": 1}]
    bot.send_message.assert_awaited_once_with(CHAT_ID, SEARCHING_TEXT)
    run_search.assert_awaited_once_with(
        bot.send_message.return_value,
        USER_ID,
        "dune",
        repo,
        jackett,
        config,
        enricher,
        bot,
    )
    assert edits(bot) == []


@pytest.mark.parametrize("args", [["404"], ["x"], []])
async def test_replay_of_a_forgotten_query_is_rejected(args: list[str]) -> None:
    bot = make_bot()
    repo = make_repo()
    repo.get_query_text.return_value = None
    await replay_search(
        make_query("h", bot),
        args,
        make_hub(),
        repo,
        AsyncMock(spec=JackettClient),
        make_config(),
        AsyncMock(),
        bot,
    )
    assert answers(bot) == [{"err": STALE_QUERY_TEXT}]
    bot.send_message.assert_not_awaited()


# --- favorite actions --------------------------------------------------------


async def test_open_favorite_returns_the_https_link_and_marks_it_seen() -> None:
    bot = make_bot()
    repo = make_repo()
    await open_favorite(make_query("fo:501", bot), ["501"], make_hub(), repo)
    assert answers(bot) == [{"l": "https://rutracker.org/forum/viewtopic.php?t=1"}]
    repo.get_favorite.assert_awaited_once_with(USER_ID, 501)
    repo.mark_favorites_seen.assert_awaited_once_with(USER_ID, 501)


async def test_open_favorite_omits_http_links() -> None:
    bot = make_bot()
    repo = make_repo()
    repo.get_favorite.return_value.details_url = "http://rutor.info/torrent/1"
    await open_favorite(make_query("fo:501", bot), ["501"], make_hub(), repo)
    assert answers(bot) == [{}]


async def test_open_favorite_gone() -> None:
    bot = make_bot()
    repo = make_repo()
    repo.get_favorite.return_value = None
    await open_favorite(make_query("fo:9", bot), ["9"], make_hub(), repo)
    await open_favorite(make_query("fo:x", bot), ["x"], make_hub(), repo)
    assert answers(bot) == [{"err": GONE_TEXT}] * 2
    repo.mark_favorites_seen.assert_not_awaited()


async def test_download_answers_first_then_sends_the_file() -> None:
    bot = make_bot()
    repo = make_repo()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrentbytes"

    await download_favorite(
        make_query("fd:501", bot), ["501"], make_hub(), repo, jackett, bot
    )

    assert answers(bot) == [{"ok": 1}]
    repo.commit.assert_awaited()
    repo.mark_favorites_seen.assert_awaited_once_with(USER_ID, 501)
    jackett.download.assert_awaited_once_with("http://jackett:9117/dl/1")
    bot.send_document.assert_awaited_once()
    chat_id, document = bot.send_document.await_args.args
    assert chat_id == CHAT_ID
    assert document.filename == "Movie _amp_ Co 1080p.torrent"
    repo.record_download.assert_awaited_once_with(
        USER_ID, "Movie &amp; Co 1080p", "chat"
    )


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (DownloadTooLargeError(), TOO_LARGE_TEXT),
        (JackettError("down"), DOWNLOAD_FAILED_TEXT),
    ],
)
async def test_download_failures_are_reported_in_the_chat(
    error: Exception, text: str
) -> None:
    bot = make_bot()
    repo = make_repo()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = error

    await download_favorite(
        make_query("fd:501", bot), ["501"], make_hub(), repo, jackett, bot
    )

    assert answers(bot) == [{"ok": 1}]
    assert text in bot.send_message.await_args.args[1]
    bot.send_document.assert_not_awaited()
    repo.record_download.assert_not_awaited()


async def test_download_gone() -> None:
    bot = make_bot()
    repo = make_repo()
    repo.get_favorite.return_value = None
    jackett = AsyncMock(spec=JackettClient)
    await download_favorite(
        make_query("fd:9", bot), ["9"], make_hub(), repo, jackett, bot
    )
    assert answers(bot) == [{"err": GONE_TEXT}]
    jackett.download.assert_not_awaited()


async def test_send_to_server_checks_rights_qbit_and_presence() -> None:
    bot = make_bot()
    repo = make_repo()
    jackett = AsyncMock(spec=JackettClient)
    qbit = AsyncMock(spec=QbittorrentClient)
    await favorite_to_server(
        make_query("fs:501", bot),
        ["501"],
        make_hub(),
        repo,
        jackett,
        qbit,
        make_config(admin=False),
        bot,
    )
    await favorite_to_server(
        make_query("fs:501", bot),
        ["501"],
        make_hub(),
        repo,
        jackett,
        None,
        make_config(admin=True),
        bot,
    )
    repo.get_favorite.return_value = None
    await favorite_to_server(
        make_query("fs:501", bot),
        ["501"],
        make_hub(),
        repo,
        jackett,
        qbit,
        make_config(admin=True),
        bot,
    )
    assert answers(bot) == [
        {"err": NO_RIGHTS_TEXT},
        {"err": QBIT_DOWN_TEXT},
        {"err": GONE_TEXT},
    ]
    repo.mark_favorites_seen.assert_not_awaited()


async def test_send_to_server_answers_ok_and_starts_the_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    begin = AsyncMock()
    monkeypatch.setattr("tj_bot.handlers.widgets_hub.begin_torrent_send", begin)
    bot = make_bot()
    repo = make_repo()
    jackett = AsyncMock(spec=JackettClient)
    qbit = AsyncMock(spec=QbittorrentClient)
    config = make_config(admin=True)

    await favorite_to_server(
        make_query("fs:501", bot), ["501"], make_hub(), repo, jackett, qbit, config, bot
    )

    assert answers(bot) == [{"ok": 1}]
    repo.mark_favorites_seen.assert_awaited_once_with(USER_ID, 501)
    assert begin.await_args is not None
    args = begin.await_args.args
    assert args[0] is None  # no callback to ack: feedback goes to the chat
    assert isinstance(args[1], Message) and args[1].chat.id == CHAT_ID
    assert args[2:] == (
        repo,
        jackett,
        qbit,
        config,
        bot,
        "Movie &amp; Co 1080p",
        "http://jackett:9117/dl/1",
    )


async def test_hub_message_handle_answers_into_the_chat() -> None:
    bot = make_bot()
    message = hub_message(make_query("fs:1", bot), bot)
    await message.answer("hi")
    method = sent_methods(bot)[0]
    assert isinstance(method, SendMessage)
    assert method.chat_id == CHAT_ID and method.text == "hi"


# --- admin shortcuts ---------------------------------------------------------


async def test_admin_shortcuts_need_rights_and_a_registered_sender() -> None:
    bot = make_bot()
    repo = make_repo()
    await open_admin_widget(
        make_query("con", bot), "con", make_hub(), repo, make_config(admin=False), bot
    )
    await open_admin_widget(
        make_query("con", bot), "con", make_hub(), repo, make_config(admin=True), bot
    )
    assert answers(bot) == [{"err": NO_RIGHTS_TEXT}, {"err": SOON_TEXT}]


@pytest.mark.parametrize(
    ("verb", "name"), [("con", "console"), ("dash", "dashboard"), ("usr", "users")]
)
async def test_admin_shortcuts_call_the_sender_hook(verb: str, name: str) -> None:
    bot = make_bot()
    repo = make_repo()
    hook = AsyncMock()
    await open_admin_widget(
        make_query(verb, bot),
        verb,
        make_hub(),
        repo,
        make_config(admin=True),
        bot,
        hook,
    )
    assert answers(bot) == [{"ok": 1}]
    hook.assert_awaited_once_with(bot, CHAT_ID, name, repo)


async def test_unknown_verb() -> None:
    bot = make_bot()
    await unknown_verb(make_query("zzz", bot), make_hub())
    assert answers(bot) == [{"err": UNKNOWN_VERB_TEXT}]


def test_context_view_defaults_for_odd_data() -> None:
    hub = make_hub()
    hub.context = {"tab": "x", "page": -1, "filter": 3}
    assert context_view(hub) == ("m", 0, "a")
    hub.context = {}
    assert context_view(hub) == ("m", 0, "a")
    assert context_view(make_hub("f", 2, "u")) == ("f", 2, "u")


# --- routing -----------------------------------------------------------------


def test_hub_router_precedes_the_classic_routers() -> None:
    order = [id(router) for router in routers_list]
    assert order.index(id(hub_router)) < order.index(id(favorites_router))
    assert order.index(id(hub_router)) < order.index(id(user_router))


def command_update(text: str) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": CHAT_ID, "type": "private"},
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Alice"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
            },
        }
    )


@pytest.fixture(scope="module")
def dispatcher() -> Dispatcher:
    """One dispatcher per module: a router attaches to a single parent."""
    dp = Dispatcher()
    dp["jackett"] = AsyncMock(spec=JackettClient)
    dp["qbit"] = None
    dp.update.outer_middleware(WidgetQueryMiddleware(dp))
    dp.include_routers(hub_router, favorites_router, user_router)
    return dp


@pytest.mark.parametrize("text", ["/start", "/history", "/favorites"])
async def test_widget_users_get_the_hub(dispatcher: Dispatcher, text: str) -> None:
    repo = make_repo()
    repo.uses_classic_ui.return_value = False
    bot = make_bot()
    bot.return_value = MagicMock(spec=Message, message_id=42)
    dispatcher["repo"] = repo
    dispatcher["config"] = make_config()

    await dispatcher.feed_update(cast(Bot, bot), command_update(text))

    assert isinstance(sent_methods(bot)[0], SendWidget)
    repo.uses_classic_ui.assert_awaited_once_with(USER_ID)


@pytest.mark.parametrize("text", ["/start", "/history", "/favorites"])
async def test_classic_users_fall_through_to_the_text_handlers(
    dispatcher: Dispatcher, text: str
) -> None:
    repo = make_repo()
    repo.uses_classic_ui.return_value = True
    repo.get_user_history.return_value = [(1, "dune")]
    repo.list_favorites.return_value = []
    bot = make_bot()
    dispatcher["repo"] = repo
    dispatcher["config"] = make_config()

    await dispatcher.feed_update(cast(Bot, bot), command_update(text))

    methods = sent_methods(bot)
    assert methods and all(isinstance(m, SendMessage) for m in methods)
    repo.save_widget_message.assert_not_awaited()
