import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from tj_bot.db.models import WidgetMessage
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.widgets import WidgetKind, WidgetUser
from tj_bot.handlers import widgets_server as ws
from tj_bot.services.qbittorrent import (
    QbittorrentClient,
    QbittorrentError,
    QueueingDisabledError,
)
from tj_bot.widgets.api import (
    AnswerWidgetQuery,
    EditMessageWidgetData,
    SendWidget,
    WidgetQuery,
)

GB = 1024**3
HASH = "3f9a1c2b" + "a" * 32
OTHER_HASH = "deadbeef" + "b" * 32
USER = {"id": 111, "is_bot": False, "first_name": "Admin"}
CHAT = {"id": 777, "type": "private", "first_name": "Admin"}


def make_bot() -> AsyncMock:
    bot = AsyncMock()
    sent = MagicMock(spec=Message)
    sent.message_id = 99
    bot.return_value = sent  # the result of every awaited method
    return bot


def make_query(data: str, bot: AsyncMock) -> WidgetQuery:
    return WidgetQuery.model_validate(
        {
            "id": "q1",
            "from": USER,
            "chat": CHAT,
            "message_id": 7,
            "chat_instance": "ci",
            "data": data,
        },
        context={"bot": bot},
    )


def make_widget(kind: str, rev: int = 3, **context: Any) -> WidgetMessage:  # noqa: ANN401  # test helper
    return WidgetMessage(chat_id=777, message_id=7, kind=kind, context=context, rev=rev)


def picker_widget(page: int = 0) -> WidgetMessage:
    return make_widget("picker", h=HASH, n="Movie", page=page)


def progress_widget(phase: str = "active") -> WidgetMessage:
    return make_widget("progress", h=HASH, n="Movie", st=phase, fc=2, ft=3)


def console_widget(tab: str = "all", page: int = 0) -> WidgetMessage:
    return make_widget("console", rev=12, tab=tab, page=page)


def make_file(index: int, priority: int = 1, name: str | None = None) -> dict[str, Any]:
    return {
        "index": index,
        "name": name or f"Root/Season 1/ep{index}.mkv",
        "size": GB,
        "priority": priority,
    }


def make_torrent(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401  # test helper
    base: dict[str, Any] = {
        "hash": HASH,
        "name": "Movie",
        "state": "downloading",
        "progress": 0.5,
        "size": 4 * GB,
        "downloaded": 2 * GB,
        "dlspeed": 1_000_000,
        "upspeed": 0,
        "eta": 600,
        "num_seeds": 3,
        "num_leechs": 4,
        "ratio": 0.1,
        "added_on": 1_762_939_805,
        "force_start": False,
        "save_path": "/data",
        "category": "movies",
    }
    base.update(overrides)
    return base


def make_qbit(
    files: list[dict[str, Any]] | None = None, torrent: dict[str, Any] | None = None
) -> AsyncMock:
    qbit = AsyncMock(spec=QbittorrentClient)
    info = torrent if torrent is not None else make_torrent()
    qbit.torrent_info.return_value = info
    qbit.torrent_files.return_value = (
        files if files is not None else [make_file(0), make_file(1), make_file(2, 0)]
    )
    qbit.free_space.return_value = 100 * GB
    qbit.list_torrents.return_value = [
        info,
        make_torrent(hash=OTHER_HASH, name="Other"),
    ]
    qbit.transfer_info.return_value = {
        "dl_info_speed": 5_000_000,
        "up_info_speed": 100,
        "connection_status": "connected",
    }
    qbit.alt_speed_enabled.return_value = False
    return qbit


def make_repo() -> AsyncMock:
    return AsyncMock(spec=TorrentRepo)


def methods(bot: AsyncMock) -> list[Any]:
    return [call.args[0] for call in bot.await_args_list]


def answer(bot: AsyncMock) -> dict[str, Any]:
    answers = [m for m in methods(bot) if isinstance(m, AnswerWidgetQuery)]
    assert len(answers) == 1, answers
    assert answers[0].widget_query_id == "q1"
    return cast(dict[str, Any], json.loads(answers[0].data or "{}"))


def edits(bot: AsyncMock) -> list[dict[str, Any]]:
    return [
        json.loads(m.chat_data or "{}")
        for m in methods(bot)
        if isinstance(m, EditMessageWidgetData)
    ]


def sent_widgets(bot: AsyncMock) -> list[SendWidget]:
    return [m for m in methods(bot) if isinstance(m, SendWidget)]


def answered_before_edit(bot: AsyncMock) -> bool:
    kinds = [type(m) for m in methods(bot)]
    return kinds.index(AnswerWidgetQuery) < kinds.index(EditMessageWidgetData)


# --- filters ------------------------------------------------------------------


async def test_kind_filter_passes_the_widget_record() -> None:
    bot = make_bot()
    repo = make_repo()
    widget = picker_widget()
    repo.get_widget_message.return_value = widget

    assert await WidgetKind("picker", "progress")(make_query("tg:1", bot), repo) == {
        "widget": widget
    }
    assert await WidgetKind("console")(make_query("tg:1", bot), repo) is False
    repo.get_widget_message.return_value = None
    assert await WidgetKind("picker")(make_query("tg:1", bot), repo) is False
    shared = WidgetQuery.model_validate(
        {"id": "q", "from": USER, "chat_instance": "ci", "data": "x"}
    )
    assert await WidgetKind("picker")(shared, repo) is False


async def test_widget_ui_filter_excludes_classic_users() -> None:
    repo = make_repo()
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(id=111)

    repo.uses_classic_ui.return_value = False
    assert await WidgetUser()(message, repo) is True
    repo.uses_classic_ui.return_value = True
    assert await WidgetUser()(message, repo) is False
    message.from_user = None
    assert await WidgetUser()(message, repo) is False


# --- picker -------------------------------------------------------------------


async def test_send_picker_widget_records_context() -> None:
    bot = make_bot()
    repo = make_repo()

    assert await ws.send_picker_widget(bot, 777, repo, make_qbit(), HASH) is True

    (widget,) = sent_widgets(bot)
    state = json.loads(widget.chat_data or "")
    assert state["m"] == "pick" and state["k"] == 2 and state["c"] == 3
    assert state["d"] == [["Season 1", 3, 2]]
    repo.save_widget_message.assert_awaited_once_with(
        777, 99, "picker", {"h": HASH, "n": "Movie", "page": 0}, rev=1
    )
    gone = make_qbit()
    gone.torrent_info.return_value = None
    assert await ws.send_picker_widget(bot, 777, repo, gone, HASH) is False


async def test_picker_toggle_answers_fragment_and_edits_state() -> None:
    bot = make_bot()
    qbit = make_qbit()
    after = [make_file(0), make_file(1, 0), make_file(2, 0)]
    qbit.torrent_files.side_effect = [qbit.torrent_files.return_value, after]
    repo = make_repo()

    await ws.picker_select(
        make_query("tg:1", bot), "tg", ["1"], picker_widget(page=5), repo, qbit, bot
    )

    qbit.set_file_priority.assert_awaited_once_with(HASH, "1", 0)
    fragment = answer(bot)
    assert (fragment["r"], fragment["p"], fragment["k"]) == (4, 0, 1)
    assert [row[4] for row in fragment["f"]] == [1, 0, 0]
    (state,) = edits(bot)
    assert state["r"] == 4 and state["m"] == "pick" and state["p"] == 0
    assert answered_before_edit(bot)
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"h": HASH, "n": "Movie", "page": 0}, rev=4
    )


@pytest.mark.parametrize(
    ("data", "verb", "args", "ids", "priority"),
    [
        ("tf:0", "tf", ["0"], "0|1|2", 1),
        ("qp:v", "qp", ["v"], "0|1|2", 1),
        ("all", "all", [], "0|1|2", 1),
        ("none", "none", [], "0|1|2", 0),
    ],
)
async def test_picker_bulk_verbs_toggle_groups(
    data: str, verb: str, args: list[str], ids: str, priority: int
) -> None:
    bot = make_bot()
    qbit = make_qbit()

    await ws.picker_select(
        make_query(data, bot), verb, args, picker_widget(page=0), make_repo(), qbit, bot
    )

    qbit.set_file_priority.assert_awaited_once_with(HASH, ids, priority)
    assert answer(bot)["p"] == 0


async def test_picker_folder_toggle_clears_a_fully_selected_folder() -> None:
    bot = make_bot()
    qbit = make_qbit([make_file(0), make_file(1)])

    await ws.picker_select(
        make_query("tf:0", bot), "tf", ["0"], picker_widget(), make_repo(), qbit, bot
    )

    qbit.set_file_priority.assert_awaited_once_with(HASH, "0|1", 0)


@pytest.mark.parametrize(
    ("verb", "args"),
    [
        ("tg", ["9"]),
        ("tg", ["x"]),
        ("tf", ["4"]),
        ("tf", []),
        ("qp", ["z"]),
        ("qp", []),
    ],
)
async def test_picker_rejects_bad_targets(verb: str, args: list[str]) -> None:
    bot = make_bot()
    qbit = make_qbit()

    await ws.picker_select(
        make_query(verb, bot), verb, args, picker_widget(), make_repo(), qbit, bot
    )

    qbit.set_file_priority.assert_not_awaited()
    assert answer(bot) == {"err": "Неверный запрос"}


async def test_picker_vanished_torrent_answers_error_and_marks_gone() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None
    repo = make_repo()

    await ws.picker_select(
        make_query("all", bot), "all", [], picker_widget(), repo, qbit, bot
    )

    assert answer(bot)["err"].startswith("Загрузка не найдена")
    assert edits(bot) == [{"v": 1, "r": 4, "m": "gone", "n": "Movie"}]
    assert answered_before_edit(bot)


async def test_picker_qbit_errors_and_missing_client() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.set_file_priority.side_effect = QbittorrentError("down")

    await ws.picker_select(
        make_query("all", bot), "all", [], picker_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}
    assert edits(bot) == []

    for handler, args in (
        (ws.picker_select, ("all", [], picker_widget())),
        (ws.picker_turn_page, (["1"], picker_widget())),
        (ws.picker_start, (picker_widget(),)),
        (ws.picker_cancel, (picker_widget(),)),
    ):
        bot = make_bot()
        await handler(make_query("x", bot), *args, make_repo(), None, bot)
        assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_picker_page_reports_qbit_down() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_files.side_effect = QbittorrentError("down")

    await ws.picker_turn_page(
        make_query("pg:1", bot), ["1"], picker_widget(), make_repo(), qbit, bot
    )

    assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_picker_page_answers_rows_and_stores_the_page() -> None:
    bot = make_bot()
    files = [make_file(i) for i in range(14)]
    repo = make_repo()

    await ws.picker_turn_page(
        make_query("pg:9", bot), ["9"], picker_widget(), repo, make_qbit(files), bot
    )

    page = answer(bot)
    assert page["r"] == 3 and page["p"] == 2
    assert [row[0] for row in page["f"]] == [12, 13]
    assert edits(bot) == []
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"h": HASH, "n": "Movie", "page": 2}
    )


async def test_picker_page_of_a_vanished_torrent() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None

    await ws.picker_turn_page(
        make_query("pg:1", bot), ["1"], picker_widget(), make_repo(), qbit, bot
    )

    assert "err" in answer(bot)
    assert edits(bot)[0]["m"] == "gone"


async def test_picker_go_starts_records_and_sends_progress() -> None:
    bot = make_bot()
    qbit = make_qbit(torrent=make_torrent(state="stoppedDL"))
    qbit.torrent_info.side_effect = [
        make_torrent(state="stoppedDL"),
        make_torrent(state="downloading"),
    ]
    repo = make_repo()

    await ws.picker_start(make_query("go", bot), picker_widget(), repo, qbit, bot)

    qbit.start_torrents.assert_awaited_once_with(HASH)
    repo.record_download.assert_awaited_once_with(111, "Movie", "server")
    assert answer(bot) == {"r": 4, "m": "go"}
    assert edits(bot) == [{"v": 1, "r": 4, "m": "go", "n": "Movie"}]
    (progress,) = sent_widgets(bot)
    state = json.loads(progress.chat_data or "")
    assert state["st"] == "downloading" and (state["fc"], state["ft"]) == (2, 3)
    repo.save_widget_message.assert_awaited_once_with(
        777,
        99,
        "progress",
        {"h": HASH, "n": "Movie", "st": "active", "fc": 2, "ft": 3},
        rev=1,
    )


async def test_picker_go_from_the_console_does_not_count_a_download() -> None:
    bot = make_bot()
    repo = make_repo()

    await ws.picker_start(
        make_query("go", bot), picker_widget(), repo, make_qbit(), bot
    )

    repo.record_download.assert_not_awaited()
    assert answer(bot)["m"] == "go"


async def test_picker_go_requires_a_selected_file() -> None:
    bot = make_bot()
    qbit = make_qbit([make_file(0, 0)])

    await ws.picker_start(
        make_query("go", bot), picker_widget(), make_repo(), qbit, bot
    )

    qbit.start_torrents.assert_not_awaited()
    assert answer(bot) == {"err": "Выберите хотя бы один файл"}


async def test_picker_go_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None
    await ws.picker_start(
        make_query("go", bot), picker_widget(), make_repo(), qbit, bot
    )
    assert "err" in answer(bot) and edits(bot)[0]["m"] == "gone"

    bot = make_bot()
    qbit = make_qbit()
    qbit.start_torrents.side_effect = QbittorrentError("down")
    await ws.picker_start(
        make_query("go", bot), picker_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}
    assert sent_widgets(bot) == []


async def test_picker_cancel_deletes_with_files() -> None:
    bot = make_bot()
    qbit = make_qbit()

    await ws.picker_cancel(
        make_query("x", bot), picker_widget(), make_repo(), qbit, bot
    )

    qbit.delete_torrents.assert_awaited_once_with(HASH, delete_files=True)
    assert answer(bot) == {"r": 4, "m": "x"}
    assert edits(bot) == [{"v": 1, "r": 4, "m": "x", "n": "Movie"}]

    bot = make_bot()
    qbit.delete_torrents.side_effect = QbittorrentError("down")
    await ws.picker_cancel(
        make_query("x", bot), picker_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_progress_tick_answers_snapshot_without_editing() -> None:
    bot = make_bot()
    repo = make_repo()

    await ws.progress_refresh(
        make_query("bg", bot), progress_widget(), repo, make_qbit(), bot
    )

    snapshot = answer(bot)
    assert snapshot["st"] == "downloading" and snapshot["pr"] == 0.5
    assert "r" not in snapshot and "t" not in snapshot
    assert (snapshot["fc"], snapshot["ft"]) == (2, 3)
    assert edits(bot) == []
    repo.update_widget_message.assert_not_awaited()


async def test_progress_tick_persists_a_transition_once() -> None:
    bot = make_bot()
    repo = make_repo()
    qbit = make_qbit(torrent=make_torrent(state="uploading", progress=1))

    await ws.progress_refresh(make_query("bg", bot), progress_widget(), repo, qbit, bot)

    (state,) = edits(bot)
    assert (state["r"], state["st"], state["pr"]) == (4, "uploading", 1.0)
    assert answered_before_edit(bot)
    repo.update_widget_message.assert_awaited_once_with(
        777,
        7,
        context={"h": HASH, "n": "Movie", "st": "done", "fc": 2, "ft": 3},
        rev=4,
    )

    bot = make_bot()
    await ws.progress_refresh(
        make_query("bg", bot), progress_widget("done"), repo, qbit, bot
    )
    assert edits(bot) == []


async def test_progress_tick_of_a_vanished_torrent() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None

    await ws.progress_refresh(
        make_query("bg", bot), progress_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"st": "gone"}
    (state,) = edits(bot)
    assert state == {
        "v": 1,
        "r": 4,
        "t": state["t"],
        "h": "3f9a1c2b",
        "n": "Movie",
        "st": "gone",
    }

    bot = make_bot()
    await ws.progress_refresh(
        make_query("bg", bot), progress_widget("gone"), make_repo(), qbit, bot
    )
    assert edits(bot) == []


async def test_progress_tick_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.side_effect = QbittorrentError("down")
    await ws.progress_refresh(
        make_query("rf", bot), progress_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.progress_refresh(
        make_query("bg", bot), progress_widget(), make_repo(), None, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


@pytest.mark.parametrize(
    ("verb", "method", "call_args"),
    [
        ("pa", "stop_torrents", (HASH,)),
        ("re", "start_torrents", (HASH,)),
        ("fo", "set_force_start", (HASH, True)),
        ("rc", "recheck_torrents", (HASH,)),
    ],
)
async def test_progress_controls_answer_snapshot_and_persist(
    verb: str, method: str, call_args: tuple[Any, ...]
) -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.side_effect = [make_torrent(), make_torrent(state="stoppedDL")]
    repo = make_repo()

    await ws.progress_control(
        make_query(verb, bot), verb, progress_widget(), repo, qbit, bot
    )

    getattr(qbit, method).assert_awaited_once_with(*call_args)
    snapshot = answer(bot)
    assert snapshot["r"] == 4 and snapshot["st"] == "stoppedDL"
    (state,) = edits(bot)
    assert state["r"] == 4 and state["st"] == "stoppedDL"
    assert answered_before_edit(bot)
    repo.update_widget_message.assert_awaited_once()
    assert repo.update_widget_message.await_args.kwargs["context"]["st"] == "paused"


async def test_progress_recheck_starts_after_the_check() -> None:
    bot = make_bot()
    qbit = make_qbit()

    await ws.progress_control(
        make_query("rc", bot), "rc", progress_widget(), make_repo(), qbit, bot
    )

    assert [
        c[0]
        for c in qbit.method_calls
        if c[0] in ("recheck_torrents", "start_torrents")
    ] == [
        "recheck_torrents",
        "start_torrents",
    ]


async def test_progress_control_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None
    await ws.progress_control(
        make_query("pa", bot), "pa", progress_widget(), make_repo(), qbit, bot
    )
    assert answer(bot)["err"].startswith("Загрузка не найдена")
    assert edits(bot)[0]["st"] == "gone"

    bot = make_bot()
    qbit = make_qbit()
    qbit.stop_torrents.side_effect = QbittorrentError("down")
    await ws.progress_control(
        make_query("pa", bot), "pa", progress_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}
    assert edits(bot) == []

    bot = make_bot()
    await ws.progress_control(
        make_query("pa", bot), "pa", progress_widget(), make_repo(), None, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


@pytest.mark.parametrize(
    ("args", "with_files"), [(["0"], False), (["1"], True), ([], False)]
)
async def test_progress_delete(args: list[str], with_files: bool) -> None:
    bot = make_bot()
    qbit = make_qbit()
    repo = make_repo()

    await ws.progress_delete(
        make_query("del", bot), args, progress_widget(), repo, qbit, bot
    )

    qbit.delete_torrents.assert_awaited_once_with(HASH, delete_files=with_files)
    assert answer(bot) == {"r": 4, "st": "gone"}
    (state,) = edits(bot)
    assert state["st"] == "gone" and state["r"] == 4
    assert repo.update_widget_message.await_args.kwargs["context"]["st"] == "gone"


async def test_progress_delete_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.delete_torrents.side_effect = QbittorrentError("down")
    await ws.progress_delete(
        make_query("del:1", bot), ["1"], progress_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.progress_delete(
        make_query("del:1", bot), ["1"], progress_widget(), make_repo(), None, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_progress_console_button_sends_the_console() -> None:
    bot = make_bot()
    repo = make_repo()

    await ws.progress_console(
        make_query("con", bot), progress_widget(), repo, make_qbit(), bot
    )

    assert answer(bot) == {"ok": 1}
    (console,) = sent_widgets(bot)
    assert json.loads(console.chat_data or "")["cnt"]["all"] == 2
    repo.save_widget_message.assert_awaited_once_with(
        777, 99, "console", {"tab": "all", "page": 0}, rev=1
    )

    bot = make_bot()
    await ws.progress_console(
        make_query("con", bot), progress_widget(), repo, None, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


# --- console ------------------------------------------------------------------


def make_message() -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = 777
    message.answer = AsyncMock()
    return message


async def test_dl_command_sends_the_console_widget() -> None:
    bot = make_bot()
    message = make_message()

    await ws.console_command(cast(Message, message), make_repo(), make_qbit(), bot)

    (console,) = sent_widgets(bot)
    state = json.loads(console.chat_data or "")
    assert (state["tab"], state["pg"], state["np"]) == ("all", 0, 1)
    assert [row[0] for row in state["rows"]] == ["3f9a1c2b", "deadbeef"]
    message.answer.assert_not_awaited()


async def test_dl_command_without_client_or_with_qbit_down() -> None:
    bot = make_bot()
    message = make_message()
    await ws.console_command(cast(Message, message), make_repo(), None, bot)
    assert "не настроен" in message.answer.await_args.args[0]
    assert sent_widgets(bot) == []

    qbit = make_qbit()
    qbit.list_torrents.side_effect = QbittorrentError("down")
    await ws.console_command(cast(Message, message), make_repo(), qbit, bot)
    (console,) = sent_widgets(bot)
    assert json.loads(console.chat_data or "")["err"] == "qBittorrent недоступен"


async def test_console_tick_answers_snapshot_without_editing() -> None:
    bot = make_bot()
    repo = make_repo()

    await ws.console_view(
        make_query("bg", bot), "bg", [], console_widget("dl", 0), repo, make_qbit(), bot
    )

    state = answer(bot)
    assert state["r"] == 12 and state["tab"] == "dl"
    assert len(state["rows"]) == 2
    assert edits(bot) == []
    repo.update_widget_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("data", "verb", "args", "tab", "page"),
    [
        ("rf", "rf", [], "all", 1),
        ("t:up", "t", ["up"], "up", 0),
        ("t:zzz", "t", ["zzz"], "all", 0),
        ("pg:-1", "pg", ["-1"], "all", 0),
        ("pg:99", "pg", ["99"], "all", 1),
    ],
)
async def test_console_view_verbs_persist_tab_and_page(
    data: str, verb: str, args: list[str], tab: str, page: int
) -> None:
    bot = make_bot()
    repo = make_repo()
    qbit = make_qbit()
    qbit.list_torrents.return_value = [
        make_torrent(hash=f"{i:08x}" + "c" * 32, state="downloading") for i in range(7)
    ]

    await ws.console_view(
        make_query(data, bot), verb, args, console_widget("all", 1), repo, qbit, bot
    )

    state = answer(bot)
    assert (state["r"], state["tab"], state["pg"]) == (13, tab, page)
    assert edits(bot) == [state]
    assert answered_before_edit(bot)
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"tab": tab, "page": page}, rev=13
    )


async def test_console_view_when_qbit_is_unreachable() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.transfer_info.side_effect = QbittorrentError("down")
    repo = make_repo()

    await ws.console_view(
        make_query("bg", bot), "bg", [], console_widget("up", 2), repo, qbit, bot
    )
    unavailable = answer(bot)
    assert unavailable["r"] == 12 and unavailable["err"] == "qBittorrent недоступен"
    assert edits(bot) == []

    bot = make_bot()
    await ws.console_view(
        make_query("rf", bot), "rf", [], console_widget("up", 2), repo, qbit, bot
    )
    assert edits(bot)[0]["err"] == "qBittorrent недоступен"
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"tab": "up", "page": 2}, rev=13
    )

    bot = make_bot()
    await ws.console_view(
        make_query("bg", bot), "bg", [], console_widget(), repo, None, bot
    )
    assert answer(bot)["err"] == "qBittorrent недоступен"


async def test_console_details() -> None:
    bot = make_bot()

    await ws.console_details(make_query("x:deadbeef", bot), ["deadbeef"], make_qbit())

    details = answer(bot)["x"]
    assert details["h"] == "deadbeef" and details["sp"] == "/data"
    assert details["l"].startswith("Загружается · 4 ГБ")


@pytest.mark.parametrize(
    "args", [["00000000"], ["DEADBEEF"], ["dead"], [], ["deadbeef'"]]
)
async def test_console_details_rejects_unknown_or_malformed_hashes(
    args: list[str],
) -> None:
    bot = make_bot()

    await ws.console_details(make_query("x", bot), args, make_qbit())

    assert answer(bot) == {"err": "Торрент не найден"}


async def test_console_details_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.list_torrents.side_effect = QbittorrentError("down")
    await ws.console_details(make_query("x:deadbeef", bot), ["deadbeef"], qbit)
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.console_details(make_query("x:deadbeef", bot), ["deadbeef"], None)
    assert answer(bot) == {"err": "qBittorrent недоступен"}


@pytest.mark.parametrize(
    ("data", "verb", "args", "method", "call_args"),
    [
        ("pa:3f9a1c2b", "pa", ["3f9a1c2b"], "stop_torrents", (HASH,)),
        ("re:3f9a1c2b", "re", ["3f9a1c2b"], "start_torrents", (HASH,)),
        ("fo:3f9a1c2b", "fo", ["3f9a1c2b"], "set_force_start", (HASH, True)),
        (
            "pr:3f9a1c2b:top",
            "pr",
            ["3f9a1c2b", "top"],
            "change_priority",
            ("topPrio", HASH),
        ),
        (
            "pr:3f9a1c2b:up",
            "pr",
            ["3f9a1c2b", "up"],
            "change_priority",
            ("increasePrio", HASH),
        ),
        (
            "pr:3f9a1c2b:down",
            "pr",
            ["3f9a1c2b", "down"],
            "change_priority",
            ("decreasePrio", HASH),
        ),
        (
            "pr:3f9a1c2b:bottom",
            "pr",
            ["3f9a1c2b", "bottom"],
            "change_priority",
            ("bottomPrio", HASH),
        ),
    ],
)
async def test_console_control_answers_patch_and_persists(
    data: str, verb: str, args: list[str], method: str, call_args: tuple[Any, ...]
) -> None:
    bot = make_bot()
    qbit = make_qbit()
    after = [make_torrent(state="stoppedDL"), make_torrent(hash=OTHER_HASH)]
    qbit.list_torrents.side_effect = [qbit.list_torrents.return_value, after]
    repo = make_repo()

    await ws.console_control(
        make_query(data, bot), verb, args, console_widget(), repo, qbit, bot
    )

    getattr(qbit, method).assert_awaited_once_with(*call_args)
    patch = answer(bot)
    assert set(patch) == {"r", "t", "cnt", "tr", "row", "x"}
    assert patch["r"] == 13 and patch["row"][2] == "p" and patch["x"]["h"] == "3f9a1c2b"
    assert patch["cnt"] == {"all": 2, "dl": 1, "up": 0, "st": 1, "er": 0}
    (state,) = edits(bot)
    assert state["r"] == 13 and state["rows"][0][2] == "p"
    assert answered_before_edit(bot)
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"tab": "all", "page": 0}, rev=13
    )


async def test_console_control_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.change_priority.side_effect = QueueingDisabledError
    await ws.console_control(
        make_query("pr", bot),
        "pr",
        ["3f9a1c2b", "top"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )
    assert answer(bot) == {"err": "Очередь отключена в qBittorrent"}
    assert edits(bot) == []

    bot = make_bot()
    await ws.console_control(
        make_query("pr", bot),
        "pr",
        ["3f9a1c2b", "sideways"],
        console_widget(),
        make_repo(),
        make_qbit(),
        bot,
    )
    assert answer(bot) == {"err": "Неверный запрос"}

    bot = make_bot()
    await ws.console_control(
        make_query("pa", bot),
        "pa",
        ["00000000"],
        console_widget(),
        make_repo(),
        make_qbit(),
        bot,
    )
    assert answer(bot) == {"err": "Торрент не найден"}

    bot = make_bot()
    qbit = make_qbit()
    qbit.stop_torrents.side_effect = QbittorrentError("down")
    await ws.console_control(
        make_query("pa", bot),
        "pa",
        ["3f9a1c2b"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.console_control(
        make_query("pa", bot),
        "pa",
        ["3f9a1c2b"],
        console_widget(),
        make_repo(),
        None,
        bot,
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_console_control_row_omitted_when_the_torrent_vanished() -> None:
    bot = make_bot()
    qbit = make_qbit()
    qbit.list_torrents.side_effect = [qbit.list_torrents.return_value, []]

    await ws.console_control(
        make_query("pa", bot),
        "pa",
        ["3f9a1c2b"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )

    assert set(answer(bot)) == {"r", "t", "cnt", "tr"}


@pytest.mark.parametrize(
    ("data", "verb", "args", "method", "call_args", "call_kwargs"),
    [
        (
            "del:3f9a1c2b:0",
            "del",
            ["3f9a1c2b", "0"],
            "delete_torrents",
            (HASH,),
            {"delete_files": False},
        ),
        (
            "del:3f9a1c2b:1",
            "del",
            ["3f9a1c2b", "1"],
            "delete_torrents",
            (HASH,),
            {"delete_files": True},
        ),
        ("alt", "alt", [], "toggle_alt_speed", (), {}),
        ("sa", "sa", [], "stop_torrents", ("all",), {}),
        ("ra", "ra", [], "start_torrents", ("all",), {}),
    ],
)
async def test_console_bulk_verbs_answer_a_snapshot(
    data: str,
    verb: str,
    args: list[str],
    method: str,
    call_args: tuple[Any, ...],
    call_kwargs: dict[str, Any],
) -> None:
    bot = make_bot()
    qbit = make_qbit()
    repo = make_repo()

    await ws.console_bulk(
        make_query(data, bot), verb, args, console_widget("st", 1), repo, qbit, bot
    )

    getattr(qbit, method).assert_awaited_once_with(*call_args, **call_kwargs)
    state = answer(bot)
    assert state["r"] == 13 and state["tab"] == "st" and "rows" in state
    assert edits(bot) == [state]
    repo.update_widget_message.assert_awaited_once_with(
        777, 7, context={"tab": "st", "page": 0}, rev=13
    )


async def test_console_bulk_errors() -> None:
    bot = make_bot()
    qbit = make_qbit()
    await ws.console_bulk(
        make_query("del", bot),
        "del",
        ["00000000", "1"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )
    assert answer(bot) == {"err": "Торрент не найден"}
    qbit.delete_torrents.assert_not_awaited()

    bot = make_bot()
    qbit.toggle_alt_speed.side_effect = QbittorrentError("down")
    await ws.console_bulk(
        make_query("alt", bot), "alt", [], console_widget(), make_repo(), qbit, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.console_bulk(
        make_query("alt", bot), "alt", [], console_widget(), make_repo(), None, bot
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


async def test_console_files_opens_a_picker_for_the_torrent() -> None:
    bot = make_bot()
    repo = make_repo()

    await ws.console_files(
        make_query("fi:deadbeef", bot),
        ["deadbeef"],
        console_widget(),
        repo,
        make_qbit(),
        bot,
    )

    assert answer(bot) == {"ok": 1}
    (picker,) = sent_widgets(bot)
    assert json.loads(picker.chat_data or "")["m"] == "pick"
    assert repo.save_widget_message.await_args.args[2:4] == (
        "picker",
        {"h": OTHER_HASH, "n": "Movie", "page": 0},
    )


async def test_console_files_errors() -> None:
    bot = make_bot()
    await ws.console_files(
        make_query("fi:00000000", bot),
        ["00000000"],
        console_widget(),
        make_repo(),
        make_qbit(),
        bot,
    )
    assert answer(bot) == {"err": "Торрент не найден"}
    assert sent_widgets(bot) == []

    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_info.return_value = None
    await ws.console_files(
        make_query("fi:deadbeef", bot),
        ["deadbeef"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )
    assert answer(bot) == {"err": "Торрент не найден"}

    bot = make_bot()
    qbit = make_qbit()
    qbit.torrent_files.side_effect = QbittorrentError("down")
    await ws.console_files(
        make_query("fi:deadbeef", bot),
        ["deadbeef"],
        console_widget(),
        make_repo(),
        qbit,
        bot,
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}

    bot = make_bot()
    await ws.console_files(
        make_query("fi:deadbeef", bot),
        ["deadbeef"],
        console_widget(),
        make_repo(),
        None,
        bot,
    )
    assert answer(bot) == {"err": "qBittorrent недоступен"}


def test_router_is_registered_before_the_classic_console() -> None:
    from tj_bot.handlers import routers_list
    from tj_bot.handlers.qbit_console import qbit_router
    from tj_bot.handlers.server_download import server_download_router

    position = routers_list.index(ws.widgets_server_router)
    assert position < routers_list.index(qbit_router)
    assert position < routers_list.index(server_download_router)
