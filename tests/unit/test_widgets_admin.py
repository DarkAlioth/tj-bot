import asyncio
import datetime
import json
import time
from collections.abc import Coroutine
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, _Call

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tj_bot.handlers.widgets_admin as wa
from tj_bot.config import AppConfig
from tj_bot.db.models import BotUser, WidgetMessage
from tj_bot.db.repo import SearchStats, TorrentRepo
from tj_bot.handlers.user import SEARCHING_TEXT
from tj_bot.services.admin_views import QbitSummary
from tj_bot.services.monitor import IndexerStatus, MonitorState, indexer_problem
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError
from tj_bot.widgets.api import EditMessageWidgetData, WidgetQuery

ADMIN = 111
CHAT = 111
MESSAGE_ID = 7
KEY = (CHAT, MESSAGE_ID)
SessionPool = async_sessionmaker[AsyncSession]
AT = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=datetime.UTC)
STARTED = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)
INDEXERS = [
    IndexerStatus("rutracker", "RuTracker", True, None, 1204),
    IndexerStatus("rutor", "RuTor", False, "login failed", 0),
]
STATS = SearchStats(
    torrents=42,
    queries=7,
    events_window=19,
    users_window=3,
    top_queries=[("дюна", 5), ("шерлок", 2)],
)


@pytest.fixture(autouse=True)
def fresh_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wa, "db_size_cache", wa.Cached[int](60))
    monkeypatch.setattr(wa, "qbit_cache", wa.Cached[QbitSummary | None](5))
    monkeypatch.setattr(wa, "broadcasts", {})
    monkeypatch.setattr(wa, "background_tasks", set())


def make_query(data: str, user_id: int = ADMIN) -> WidgetQuery:
    return WidgetQuery.model_validate(
        {
            "id": "q1",
            "from": {"id": user_id, "is_bot": False, "first_name": "A"},
            "chat": {"id": CHAT, "type": "private"},
            "message_id": MESSAGE_ID,
            "chat_instance": "ci",
            "data": data,
        }
    )


def make_widget(kind: str, context: dict[str, Any], rev: int = 3) -> WidgetMessage:
    return WidgetMessage(
        chat_id=CHAT, message_id=MESSAGE_ID, kind=kind, context=context, rev=rev
    )


def make_user(uid: int = 100, blocked: bool = False, admin: bool = False) -> BotUser:
    return BotUser(
        user_id=uid,
        username="alice",
        full_name="Alice",
        blocked=blocked,
        admin=admin,
        first_seen=AT,
        last_seen=AT,
    )


def make_repo() -> AsyncMock:
    repo = AsyncMock(spec=TorrentRepo)
    repo.db_size.return_value = 123_400_000
    repo.get_stats.return_value = STATS
    repo.search_counts_by_day.return_value = [1, 2, 3, 4, 5, 6, 7]
    repo.count_users.return_value = 3
    repo.list_users_page.return_value = [make_user(100), make_user(200, admin=True)]
    repo.user_activity_counts.return_value = (2, 1)
    repo.get_user.return_value = make_user()
    repo.active_user_ids.return_value = [1, 2, 3]
    repo.uses_classic_ui.return_value = False
    repo.get_user_searches.return_value = [("дюна", AT)]
    repo.get_user_downloads.return_value = [("Дюна 2160p", "server", AT)]
    return repo


def make_monitor() -> MonitorState:
    return MonitorState(
        problems={"indexer:rutor": indexer_problem("RuTor")},
        indexers=list(INDEXERS),
        checked_at=1_794_487_200,
    )


def make_config(super_admins: set[int] | None = None) -> AppConfig:
    supers = super_admins or set()
    config = MagicMock()
    config.is_super_admin = lambda uid: uid in supers
    config.super_admins = sorted(supers)
    config.dynamic_admins = set()
    return cast(AppConfig, config)


def make_message() -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = CHAT
    message.from_user = MagicMock()
    message.from_user.id = ADMIN
    message.answer = AsyncMock()
    return message


class FakePool:
    """``async_sessionmaker`` stand-in yielding one session mock."""

    def __init__(self) -> None:
        self.session = AsyncMock()

    def __call__(self) -> "FakePool":
        return self

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None

    @property
    def typed(self) -> SessionPool:
        return cast(SessionPool, self)


def capture_spawn(monkeypatch: pytest.MonkeyPatch) -> list[Coroutine[Any, Any, None]]:
    spawned: list[Coroutine[Any, Any, None]] = []

    def fake_spawn(coroutine: Coroutine[Any, Any, None]) -> MagicMock:
        spawned.append(coroutine)
        return MagicMock()

    monkeypatch.setattr(wa, "spawn", fake_spawn)
    return spawned


def call_of(mock: AsyncMock) -> _Call:
    call = mock.await_args
    assert call is not None
    return call


def answer_of(method: object) -> dict[str, Any]:
    data = getattr(method, "data", None)
    assert isinstance(data, str)
    result: dict[str, Any] = json.loads(data)
    return result


def patch_send(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    send = AsyncMock()
    monkeypatch.setattr(wa, "send_widget", send)
    return send


def patch_edit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    edit = AsyncMock()
    monkeypatch.setattr(wa, "edit_widget_state", edit)
    return edit


# --- filters and helpers ---------------------------------------------------


async def test_widget_ui_filter_follows_the_classic_flag() -> None:
    repo = make_repo()
    message = make_message()

    assert await wa.WidgetUi()(message, repo) is True
    repo.uses_classic_ui.return_value = True
    assert await wa.WidgetUi()(message, repo) is False
    message.from_user = None
    assert await wa.WidgetUi()(message, repo) is False


async def test_widget_kind_filter_passes_the_record() -> None:
    repo = make_repo()
    widget = make_widget("users", {})
    repo.get_widget_message.return_value = widget

    assert await wa.WidgetKind("users")(make_query("t:a"), repo) == {"widget": widget}
    assert await wa.WidgetKind("dashboard")(make_query("bg"), repo) is False
    repo.get_widget_message.return_value = None
    assert await wa.WidgetKind("users")(make_query("t:a"), repo) is False
    headless = WidgetQuery.model_validate(
        {
            "id": "q",
            "from": {"id": 1, "is_bot": False, "first_name": "A"},
            "chat_instance": "ci",
            "data": "bg",
        }
    )
    assert await wa.WidgetKind("users")(headless, repo) is False


async def test_cached_reloads_only_after_the_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    load = AsyncMock(side_effect=[1, 2])
    cache = wa.Cached[int](60)

    assert await cache.get(load) == 1
    clock[0] = 150.0
    assert await cache.get(load) == 1
    clock[0] = 161.0
    assert await cache.get(load) == 2


def test_parse_helpers() -> None:
    assert wa.parse_int("12") == 12
    assert wa.parse_int("x") is None
    assert wa.parse_user_args(["5", "1"]) == (5, True)
    assert wa.parse_user_args(["5", "0"]) == (5, False)
    assert wa.parse_user_args(["5", "2"]) is None
    assert wa.parse_user_args(["a", "1"]) is None
    assert wa.parse_user_args(["5"]) is None


async def test_spawn_keeps_a_reference_until_done() -> None:
    async def noop() -> None:
        return None

    task = wa.spawn(noop())
    assert task in wa.background_tasks
    await task
    assert task not in wa.background_tasks


# --- dashboard -------------------------------------------------------------


async def test_load_qbit_summarizes_or_returns_none() -> None:
    assert await wa.load_qbit(None) is None
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = [{"state": "downloading"}, {"state": "pausedUP"}]
    qbit.transfer_info.return_value = {"dl_info_speed": 10, "up_info_speed": 5}
    qbit.free_space.return_value = 99

    assert await wa.load_qbit(qbit) == QbitSummary(1, 2, 10, 5, 99)

    qbit.list_torrents.side_effect = QbittorrentError("off")
    assert await wa.load_qbit(qbit) is None


async def test_show_dashboard_sends_the_widget_on_the_command_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = patch_send(monkeypatch)
    repo = make_repo()

    await wa.show_dashboard(
        make_message(),
        CommandObject(command="stats"),
        AsyncMock(),
        repo,
        None,
        make_monitor(),
        STARTED,
    )

    args, kwargs = call_of(send).args, call_of(send).kwargs
    assert args[1:3] == (CHAT, "dashboard")
    state = args[3]
    assert state["a"] == "s" and state["r"] == wa.FIRST_REV
    assert state["h"]["q"] is None
    assert state["h"]["db"] == 123_400_000
    assert state["i"] == [["RuTracker", 1204], ["RuTor", 0, "login failed"]]
    assert "индексеры 1/2" in args[4]
    assert kwargs["kind"] == "dashboard"
    assert kwargs["context"] == {"a": "s", "top": ["дюна", "шерлок"], "i": state["i"]}
    repo.get_stats.assert_awaited_once_with(datetime.timedelta(days=7), top_limit=8)


async def test_dashboard_bg_answers_health_and_rows_only_when_changed() -> None:
    repo = make_repo()
    monitor = make_monitor()
    rows = [["RuTracker", 1204], ["RuTor", 0, "login failed"]]
    widget = make_widget("dashboard", {"a": "h", "top": [], "i": rows}, rev=12)

    same = answer_of(
        await wa.dashboard_bg(make_query("bg"), widget, repo, None, monitor, STARTED)
    )

    assert same["r"] == 12 and "i" not in same
    assert same["h"]["m"] == monitor.checked_at
    repo.update_widget_message.assert_not_awaited()

    monitor.problems = {}
    changed = answer_of(
        await wa.dashboard_bg(make_query("bg"), widget, repo, None, monitor, STARTED)
    )

    assert changed["i"] == [["RuTracker", 1204], ["RuTor", 0]]
    repo.update_widget_message.assert_awaited_once_with(
        CHAT, MESSAGE_ID, context={"a": "h", "top": [], "i": changed["i"]}
    )


async def test_dashboard_check_answers_pending_then_publishes_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = capture_spawn(monkeypatch)
    edit = patch_edit(monkeypatch)
    repo = make_repo()
    monitor = make_monitor()
    widget = make_widget("dashboard", {"a": "i", "top": ["дюна"], "i": []}, rev=60)
    repo.get_widget_message.return_value = widget
    monkeypatch.setattr(wa, "TorrentRepo", lambda session: repo)
    pool = FakePool()

    async def fake_probe_now(*args: object) -> dict[str, object]:
        monitor.problems = {}
        monitor.checked_at = 1_794_487_920
        return {}

    monkeypatch.setattr(wa, "probe_now", fake_probe_now)

    result = await wa.dashboard_check(
        make_query("chk"),
        AsyncMock(),
        AsyncMock(),
        None,
        make_config(),
        monitor,
        STARTED,
        pool.typed,
    )

    assert answer_of(result) == {"ok": 1, "pending": 1}
    assert len(spawned) == 1
    await spawned[0]
    state = call_of(edit).args[3]
    assert call_of(edit).args[1:3] == (CHAT, MESSAGE_ID)
    assert state["r"] == 61 and state["a"] == "i"
    assert state["h"]["m"] == 1_794_487_920
    assert state["i"] == [["RuTracker", 1204], ["RuTor", 0]]
    assert call_of(edit).kwargs["context"]["i"] == state["i"]
    pool.session.commit.assert_awaited_once()


async def test_finish_check_skips_a_forgotten_message_and_logs_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = patch_edit(monkeypatch)
    repo = make_repo()
    monkeypatch.setattr(wa, "TorrentRepo", lambda session: repo)
    monkeypatch.setattr(wa, "probe_now", AsyncMock())
    common = (
        AsyncMock(),
        None,
        make_config(),
        make_monitor(),
        STARTED,
        FakePool().typed,
    )

    repo.get_widget_message.return_value = None
    await wa.finish_check(AsyncMock(), CHAT, MESSAGE_ID, *common)
    edit.assert_not_awaited()

    repo.get_widget_message.return_value = make_widget("dashboard", {"a": "h"})
    edit.side_effect = TelegramBadRequest(
        method=EditMessageWidgetData(
            chat_id=CHAT, message_id=MESSAGE_ID, chat_data="{}"
        ),
        message="Bad Request: message to edit not found",
    )
    await wa.finish_check(AsyncMock(), CHAT, MESSAGE_ID, *common)


async def test_dashboard_query_validates_the_index_and_runs_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = capture_spawn(monkeypatch)
    widget = make_widget("dashboard", {"a": "s", "top": ["дюна", "шерлок"], "i": []})
    common = (widget, AsyncMock(), AsyncMock(), make_config(), FakePool().typed)

    assert answer_of(await wa.dashboard_query(make_query("q:x"), ["x"], *common)) == {
        "err": wa.ERR_BAD_REQUEST
    }
    assert answer_of(await wa.dashboard_query(make_query("q:2"), ["2"], *common)) == {
        "err": wa.ERR_STALE_TOP
    }
    assert answer_of(await wa.dashboard_query(make_query("q:1"), ["1"], *common)) == {
        "ok": 1
    }
    assert len(spawned) == 1

    run = AsyncMock()
    monkeypatch.setattr(wa, "run_search", run)
    repo = make_repo()
    monkeypatch.setattr(wa, "TorrentRepo", lambda session: repo)
    await spawned[0]
    bot = common[1]
    bot.send_message.assert_awaited_once_with(CHAT, SEARCHING_TEXT)
    assert call_of(run).args[1:3] == (ADMIN, "шерлок")


# --- users -----------------------------------------------------------------


async def test_show_users_sends_the_first_page(monkeypatch: pytest.MonkeyPatch) -> None:
    send = patch_send(monkeypatch)
    repo = make_repo()

    await wa.show_users(make_message(), AsyncMock(), repo, make_config({100}))

    args, kwargs = call_of(send).args, call_of(send).kwargs
    assert args[1:3] == (CHAT, "users")
    state = args[3]
    assert (state["g"], state["p"], state["n"]) == ("a", 0, 1)
    assert state["c"] == [3, 3, 3, 3]
    assert state["u"][0][:4] == [100, "Alice", "alice", 2]
    assert state["u"][1][3] == 1
    assert args[4].startswith("👥 Пользователи: всего 3")
    assert kwargs["context"] == {"g": "a", "p": 0}


async def test_users_tab_and_page_answer_the_full_state_and_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = patch_edit(monkeypatch)
    repo = make_repo()
    repo.count_users.return_value = 20
    widget = make_widget("users", {"g": "a", "p": 0}, rev=5)
    common = (widget, repo, make_config(), AsyncMock())

    answer = answer_of(await wa.users_tab(make_query("t:m"), ["m"], *common))

    assert answer["r"] == 6 and answer["g"] == "m" and answer["p"] == 0
    assert "m" not in answer
    assert call_of(edit).args[3] == answer
    assert call_of(edit).kwargs["context"] == {"g": "m", "p": 0}

    page = answer_of(await wa.users_page(make_query("pg:a:9"), ["a", "9"], *common))
    assert page["p"] == 2 and page["n"] == 3  # clamped to the last page

    assert answer_of(await wa.users_tab(make_query("t:z"), ["z"], *common)) == {
        "err": wa.ERR_BAD_REQUEST
    }
    assert answer_of(
        await wa.users_page(make_query("pg:a:x"), ["a", "x"], *common)
    ) == {"err": wa.ERR_BAD_REQUEST}


async def test_users_block_enforces_refusals_then_toasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_edit(monkeypatch)
    repo = make_repo()
    widget = make_widget("users", {"g": "r", "p": 1}, rev=5)
    config = make_config({999})
    bot = AsyncMock()

    async def block(args: list[str]) -> dict[str, Any]:
        query = make_query("blk:" + ":".join(args))
        return answer_of(await wa.users_block(query, args, widget, repo, config, bot))

    assert await block(["x", "1"]) == {"err": wa.ERR_BAD_REQUEST}
    assert await block(["999", "1"]) == {"err": wa.ERR_SUPER_BLOCK}
    assert await block([str(ADMIN), "1"]) == {"err": wa.ERR_SELF_BLOCK}
    repo.get_user.return_value = None
    assert await block(["100", "1"]) == {"err": wa.ERR_USER_NOT_FOUND}
    repo.get_user.return_value = make_user(admin=True)
    assert await block(["100", "1"]) == {"err": wa.ERR_ADMIN_BLOCK}
    repo.set_blocked.assert_not_awaited()

    repo.get_user.return_value = make_user()
    answer = await block(["100", "1"])

    repo.set_blocked.assert_awaited_once_with(100, True)
    assert answer["m"] == "Заблокирован" and answer["g"] == "r" and answer["r"] == 6
    assert (await block(["100", "0"]))["m"] == "Разблокирован"


async def test_users_admin_toggles_role_commands_and_live_admin_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_edit(monkeypatch)
    commands = AsyncMock()
    monkeypatch.setattr(wa, "set_user_commands", commands)
    repo = make_repo()
    widget = make_widget("users", {"g": "a", "p": 0}, rev=5)
    config = make_config({999})
    bot = AsyncMock()

    async def toggle(args: list[str]) -> dict[str, Any]:
        query = make_query("adm:" + ":".join(args))
        return answer_of(await wa.users_admin(query, args, widget, repo, config, bot))

    assert await toggle(["5"]) == {"err": wa.ERR_BAD_REQUEST}
    assert await toggle(["999", "1"]) == {"err": wa.ERR_SUPER_ROLE}
    assert await toggle([str(ADMIN), "0"]) == {"err": wa.ERR_SELF_ROLE}
    repo.get_user.return_value = None
    assert await toggle(["100", "1"]) == {"err": wa.ERR_USER_NOT_FOUND}
    repo.get_user.return_value = make_user()

    promoted = await toggle(["100", "1"])

    repo.set_admin.assert_awaited_with(100, True)
    assert 100 in config.dynamic_admins
    commands.assert_awaited_with(bot, 100, True)
    assert promoted["m"] == "Назначен админом"

    demoted = await toggle(["100", "0"])

    repo.set_admin.assert_awaited_with(100, False)
    assert 100 not in config.dynamic_admins
    commands.assert_awaited_with(bot, 100, False)
    assert demoted["m"] == "Снят с админа"


async def test_users_activity_answers_recent_rows() -> None:
    repo = make_repo()

    answer = answer_of(await wa.users_activity(make_query("act:100"), ["100"], repo))

    minutes = int(AT.timestamp()) // 60
    assert answer == {
        "ok": 1,
        "a": {"s": [["дюна", minutes]], "d": [["Дюна 2160p", minutes, "s"]]},
    }
    repo.get_user_searches.assert_awaited_once_with(100, 5)
    assert answer_of(await wa.users_activity(make_query("act:x"), ["x"], repo)) == {
        "err": wa.ERR_BAD_REQUEST
    }


def test_current_page_falls_back_on_bad_context() -> None:
    assert wa.current_page(make_widget("users", {"g": "b", "p": 2})) == ("b", 2)
    assert wa.current_page(make_widget("users", {"g": "zz", "p": "x"})) == ("a", 0)


# --- broadcast -------------------------------------------------------------


async def test_broadcast_draft_needs_text_and_sends_the_raw_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = patch_send(monkeypatch)
    repo = make_repo()
    message = make_message()

    await wa.broadcast_draft(
        message, CommandObject(command="broadcast"), AsyncMock(), repo
    )

    assert "Использование" in message.answer.await_args.args[0]
    send.assert_not_awaited()

    text = "Привет <b>всем</b>\nвторая строка"
    await wa.broadcast_draft(
        message, CommandObject(command="broadcast", args=text), AsyncMock(), repo
    )

    args, kwargs = call_of(send).args, call_of(send).kwargs
    assert args[2] == "broadcast"
    assert args[3]["st"] == "draft" and args[3]["n"] == 3 and args[3]["tx"] == text
    assert args[4] == "📢 Рассылка 3 пользователям — подтвердите в виджете выше."
    assert kwargs["context"] == {"st": "draft", "tx": text, "n": 3}


async def test_broadcast_go_starts_delivery_and_reports_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = capture_spawn(monkeypatch)
    edit = patch_edit(monkeypatch)
    send_message = AsyncMock(side_effect=[True, False, True])
    monkeypatch.setattr("tj_bot.services.broadcaster.send_message", send_message)
    repo = make_repo()
    monkeypatch.setattr(wa, "TorrentRepo", lambda session: repo)
    bot = AsyncMock()
    pool = FakePool()
    text = "Привет <b>всем</b>"

    closed = make_widget("broadcast", {"st": "run", "tx": text, "n": 3}, rev=2)
    assert answer_of(
        await wa.broadcast_go(make_query("go"), closed, repo, bot, pool.typed)
    ) == {"err": wa.ERR_DRAFT_CLOSED}

    draft = make_widget("broadcast", {"st": "draft", "tx": text, "n": 3}, rev=1)
    answer = answer_of(
        await wa.broadcast_go(make_query("go"), draft, repo, bot, pool.typed)
    )

    assert answer == {"ok": 1, "st": "run"}
    run_state = call_of(edit).args[3]
    assert run_state["st"] == "run" and run_state["r"] == 2 and run_state["n"] == 3
    assert call_of(edit).kwargs["context"]["st"] == "run"
    progress = wa.broadcasts[KEY]
    assert progress.status == "run" and progress.recipients == 3

    await spawned[0]

    assert (progress.ok, progress.fail, progress.status, progress.rev) == (
        2,
        1,
        "done",
        3,
    )
    assert send_message.await_args_list[0].args[1:] == (
        1,
        "Привет &lt;b&gt;всем&lt;/b&gt;",
    )
    done_state = call_of(edit).args[3]
    assert done_state["st"] == "done" and done_state["r"] == 3
    assert (done_state["ok"], done_state["fail"]) == (2, 1)
    assert call_of(edit).kwargs["context"] == {
        "st": "done",
        "tx": text,
        "n": 3,
        "ok": 2,
        "fail": 1,
    }
    bot.edit_message_caption.assert_awaited_once_with(
        chat_id=CHAT, message_id=MESSAGE_ID, caption="📢 Доставлено 2 из 3"
    )
    pool.session.commit.assert_awaited_once()


async def test_deliver_broadcast_logs_a_failed_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_edit(monkeypatch)
    monkeypatch.setattr(
        "tj_bot.services.broadcaster.send_message", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(wa, "TorrentRepo", lambda session: make_repo())
    bot = AsyncMock()
    bot.edit_message_caption.side_effect = TelegramBadRequest(
        method=EditMessageWidgetData(
            chat_id=CHAT, message_id=MESSAGE_ID, chat_data="{}"
        ),
        message="Bad Request: message to edit not found",
    )
    progress = wa.BroadcastProgress(rev=2, recipients=1)

    await wa.deliver_broadcast(bot, KEY, progress, [1], "hi", FakePool().typed)

    assert progress.status == "done" and progress.ok == 1


async def test_broadcast_bg_reports_memory_then_the_stored_context() -> None:
    widget = make_widget(
        "broadcast", {"st": "run", "tx": "hi", "n": 23, "ok": 0}, rev=2
    )
    progress = wa.BroadcastProgress(rev=2, recipients=23, ok=12, fail=1)
    wa.broadcasts[KEY] = progress

    running = answer_of(await wa.broadcast_bg(make_query("bg"), widget))
    assert running == {"st": "run", "n": 23, "ok": 12, "fail": 1}

    progress.status = "done"
    progress.rev = 3
    assert answer_of(await wa.broadcast_bg(make_query("bg"), widget))["r"] == 3

    del wa.broadcasts[KEY]
    orphan = answer_of(await wa.broadcast_bg(make_query("bg"), widget))
    assert orphan == {"r": 2, "st": "done", "n": 23, "ok": 0, "fail": 0}

    draft = make_widget("broadcast", {"st": "draft", "tx": "hi", "n": 23}, rev=1)
    assert answer_of(await wa.broadcast_bg(make_query("bg"), draft))["st"] == "draft"


async def test_broadcast_cancel_closes_the_draft_or_stops_the_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = patch_edit(monkeypatch)
    repo = make_repo()
    bot = AsyncMock()

    done = make_widget("broadcast", {"st": "done", "tx": "hi", "n": 3}, rev=3)
    assert answer_of(await wa.broadcast_cancel(make_query("x"), done, repo, bot)) == {
        "err": wa.ERR_DRAFT_CLOSED
    }

    draft = make_widget("broadcast", {"st": "draft", "tx": "hi", "n": 3}, rev=1)
    answer = answer_of(await wa.broadcast_cancel(make_query("x"), draft, repo, bot))

    assert answer == {"r": 2, "st": "cancel", "n": 3, "ok": 0, "fail": 0}
    assert call_of(edit).args[3]["st"] == "cancel"
    assert call_of(edit).kwargs["context"]["st"] == "cancel"

    running = make_widget("broadcast", {"st": "run", "tx": "hi", "n": 3}, rev=2)
    progress = wa.BroadcastProgress(rev=2, recipients=3, ok=1)
    progress.task = asyncio.create_task(asyncio.sleep(10))
    wa.broadcasts[KEY] = progress

    answer = answer_of(await wa.broadcast_cancel(make_query("x"), running, repo, bot))

    assert answer == {"r": 3, "st": "cancel", "n": 3, "ok": 1, "fail": 0}
    assert KEY not in wa.broadcasts
    with pytest.raises(asyncio.CancelledError):
        await progress.task
    assert progress.task.cancelled()
