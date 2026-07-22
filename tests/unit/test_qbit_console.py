from typing import Any, cast
from unittest.mock import AsyncMock

from aiogram.types import Message

from tj_bot.handlers.qbit_console import (
    Qbm,
    build_card_view,
    build_list_view,
    format_eta,
    format_size,
    format_speed,
    matches_filter,
    progress_bar,
    qbit_actions,
)
from tj_bot.services.qbittorrent import QbittorrentClient


def make_torrent(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401  # test helper
    base: dict[str, Any] = {
        "hash": "a" * 40,
        "name": "Debian 14 netinst",
        "state": "downloading",
        "progress": 0.473,
        "size": 4_601_968_640,
        "completed": 2_176_731_136,
        "dlspeed": 8_800_000,
        "upspeed": 120_000,
        "eta": 8040,
        "num_seeds": 12,
        "num_leechs": 45,
        "ratio": 0.03,
        "category": "tj-bot",
    }
    base.update(overrides)
    return base


def test_format_helpers() -> None:
    assert format_size(0) == "0 B"
    assert format_size(4_601_968_640) == "4.3 GB"
    assert format_speed(0) == "0"
    assert format_speed(8_800_000) == "8.4 MB/s"
    assert format_eta(8040) == "2ч 14м"
    assert format_eta(8640000) == "∞"
    assert format_eta(90000) == "1д 1ч"
    assert progress_bar(0.473) == "▓▓▓▓▓░░░░░ 47.3%"


def test_matches_filter_groups() -> None:
    assert matches_filter({"state": "downloading"}, "dl")
    assert matches_filter({"state": "stalledUP"}, "up")
    assert matches_filter({"state": "stoppedDL"}, "stop")
    assert not matches_filter({"state": "uploading"}, "dl")
    assert matches_filter({"state": "anything"}, "all")


def test_list_view_counts_pagination_and_callbacks() -> None:
    torrents = [make_torrent(hash=f"{i:040d}", name=f"T{i}") for i in range(8)]
    torrents[0]["state"] = "stoppedDL"
    transfer = {"dl_info_speed": 1_048_576, "up_info_speed": 0}

    text, keyboard, page = build_list_view(torrents, transfer, False, 1, "all")

    assert "Торрентов: 8" in text
    assert "Загружается: 7" in text
    assert "Пауза: 1" in text
    assert "Страница 2/2" in text
    # page 1 of 8 items with PAGE_SIZE 6 → 2 torrent buttons
    torrent_rows = [
        row
        for row in keyboard.inline_keyboard
        if row[0].callback_data and Qbm.unpack(row[0].callback_data).a == "card"
    ]
    assert len(torrent_rows) == 2
    assert all(
        len(b.callback_data.encode()) <= 64
        for row in keyboard.inline_keyboard
        for b in row
        if b.callback_data
    )


def test_card_view_buttons_depend_on_state() -> None:
    text, keyboard = build_card_view(make_torrent(), 0, "all")
    assert "▓▓▓▓▓░░░░░ 47.3%" in text
    assert "Debian 14 netinst" in text
    first_row_labels = [b.text for b in keyboard.inline_keyboard[0]]
    assert first_row_labels == ["⏸ Пауза", "🚀 Force"]

    _, stopped_kb = build_card_view(make_torrent(state="stoppedDL"), 0, "all")
    assert [b.text for b in stopped_kb.inline_keyboard[0]] == ["▶️ Старт", "🚀 Force"]


def make_query() -> tuple[AsyncMock, AsyncMock]:
    query = AsyncMock()
    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()
    query.message = message
    return query, message


async def test_delete_flow_requires_confirmation() -> None:
    query, message = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrent_info.return_value = make_torrent()

    await qbit_actions(query, Qbm(a="del", h="a" * 40, p=0, f="all"), qbit)

    qbit.delete_torrents.assert_not_awaited()
    confirm_text = message.edit_text.await_args.args[0]
    assert "Удалить" in confirm_text
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    actions = [
        Qbm.unpack(b.callback_data).a for row in keyboard.inline_keyboard for b in row
    ]
    assert actions == ["delf", "delt", "card"]


async def test_delete_with_files_calls_client_and_returns_to_list() -> None:
    query, _ = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = []
    qbit.transfer_info.return_value = {}
    qbit.alt_speed_enabled.return_value = False

    await qbit_actions(query, Qbm(a="delf", h="a" * 40, p=0, f="all"), qbit)

    qbit.delete_torrents.assert_awaited_once_with("a" * 40, delete_files=True)
    qbit.list_torrents.assert_awaited_once()


async def test_stop_action_refreshes_card() -> None:
    query, message = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrent_info.return_value = make_torrent(state="stoppedDL")

    await qbit_actions(query, Qbm(a="stop", h="a" * 40, p=0, f="all"), qbit)

    qbit.stop_torrents.assert_awaited_once_with("a" * 40)
    assert message.edit_text.await_count == 1


async def test_menu_without_qbit_reports_not_configured() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()

    from tj_bot.handlers.qbit_console import qbit_menu

    await qbit_menu(cast(Message, message), None)

    assert "не настроен" in message.answer.await_args.args[0]


async def test_menu_renders_list() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = [make_torrent()]
    qbit.transfer_info.return_value = {"dl_info_speed": 0, "up_info_speed": 0}
    qbit.alt_speed_enabled.return_value = True

    from tj_bot.handlers.qbit_console import qbit_menu

    await qbit_menu(cast(Message, message), qbit)

    text = message.answer.await_args.args[0]
    assert "qBittorrent" in text
    assert "🐢 вкл" in text


async def test_list_action_edits_message() -> None:
    query, message = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = []
    qbit.transfer_info.return_value = {}
    qbit.alt_speed_enabled.return_value = False

    await qbit_actions(query, Qbm(a="ls", h="", p=0, f="dl"), qbit)

    assert "Список пуст" in message.edit_text.await_args.args[0]


async def test_card_falls_back_to_list_when_missing() -> None:
    query, message = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrent_info.return_value = None
    qbit.list_torrents.return_value = []
    qbit.transfer_info.return_value = {}
    qbit.alt_speed_enabled.return_value = False

    await qbit_actions(query, Qbm(a="card", h="a" * 40, p=0, f="all"), qbit)

    qbit.list_torrents.assert_awaited_once()


async def test_priority_action_maps_to_api_and_handles_disabled_queueing() -> None:
    from tj_bot.services.qbittorrent import QueueingDisabledError

    query, message = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrent_info.return_value = make_torrent()

    await qbit_actions(query, Qbm(a="ptop", h="a" * 40, p=0, f="all"), qbit)
    qbit.change_priority.assert_awaited_once_with("topPrio", "a" * 40)

    qbit.change_priority.side_effect = QueueingDisabledError
    await qbit_actions(query, Qbm(a="pup", h="a" * 40, p=0, f="all"), qbit)
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_global_actions() -> None:
    query, _ = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.return_value = []
    qbit.transfer_info.return_value = {}
    qbit.alt_speed_enabled.return_value = False

    await qbit_actions(query, Qbm(a="stopall", h="", p=0, f="all"), qbit)
    qbit.stop_torrents.assert_awaited_once_with("all")

    await qbit_actions(query, Qbm(a="startall", h="", p=0, f="all"), qbit)
    qbit.start_torrents.assert_awaited_once_with("all")

    await qbit_actions(query, Qbm(a="alt", h="", p=0, f="all"), qbit)
    qbit.toggle_alt_speed.assert_awaited_once()


async def test_client_error_shows_alert() -> None:
    from tj_bot.services.qbittorrent import QbittorrentError

    query, _ = make_query()
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.list_torrents.side_effect = QbittorrentError("down")

    await qbit_actions(query, Qbm(a="ls", h="", p=0, f="all"), qbit)

    assert query.answer.await_args.kwargs.get("show_alert") is True
