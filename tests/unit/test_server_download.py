import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.server_download import (
    Fs,
    begin_magnet_send,
    begin_torrent_send,
    build_selection_view,
    cancel_download,
    select_all_files,
    start_download,
    toggle_file,
)
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

GB = 1024**3
HASH = "a" * 40


def make_file(index: int, priority: int = 1, size: int = GB) -> dict[str, Any]:
    return {
        "index": index,
        "name": f"dir/file{index}.mkv",
        "size": size,
        "priority": priority,
    }


def make_config(admin: bool = True) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda _uid: admin
    config.settings.qbit_category = "tj-bot"
    config.settings.qbit_poll_interval_seconds = 30
    config.settings.qbit_watch_timeout_seconds = 3600
    return cast(AppConfig, config)


def make_qbit(files: list[dict[str, Any]] | None = None) -> AsyncMock:
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.torrent_files.return_value = files if files is not None else [make_file(0)]
    qbit.torrent_info.return_value = {"name": "Movie", "tags": "tjbot-abc123"}
    qbit.free_space.return_value = 100 * GB
    qbit.torrents_by_tag.return_value = [{"hash": HASH}]
    return qbit


def make_message() -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = 777
    message.message_id = 5
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    return message


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    query.from_user = MagicMock()
    query.from_user.id = 111
    query.message = make_message()
    query.answer = AsyncMock()
    query.message.answer.return_value = make_message()
    return query


def fs(action: str, v: int = 0, p: int = 0) -> Fs:
    return Fs(a=action, h=HASH, v=v, p=p)


def test_selection_view_defaults_to_all_selected() -> None:
    files = [make_file(0), make_file(1, size=2 * GB)]

    text, kb = build_selection_view(HASH, "Movie <X>", files, 100 * GB, 0)

    assert "Movie &lt;X&gt;" in text
    assert "Выбрано: <b>2</b> из 2" in text
    assert "3.0 GB</code> из" in text
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "✅ 1" in labels and "✅ 2" in labels
    assert "▶️ Начать" in labels and "❌ Отмена" in labels
    callbacks = [
        b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data
    ]
    assert all(len(data.encode()) <= 64 for data in callbacks)


def test_selection_view_marks_skipped_files_and_warns_on_space() -> None:
    files = [make_file(0, priority=0), make_file(1, size=6 * GB)]

    text, kb = build_selection_view(HASH, "Movie", files, 5 * GB, 0)

    assert "Выбрано: <b>1</b> из 2" in text
    assert "⚠️" in text  # 6 GB selected > 5 GB free
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "▫️ 1" in labels and "✅ 2" in labels


def test_selection_view_paginates_files() -> None:
    files = [make_file(i) for i in range(20)]

    text, kb = build_selection_view(HASH, "Movie", files, 100 * GB, 1)

    file_lines = text.split("\n⠀\n")[-1].splitlines()
    assert len(file_lines) == 8  # page 2 of 20 files holds indexes 9-16
    assert file_lines[0].startswith("✅ 9. ")
    assert file_lines[-1].startswith("✅ 16. ")
    nav = [b.text for b in kb.inline_keyboard[-2]]
    assert nav == ["⬅", "2/3", "➡"]


async def test_toggle_flips_priority_and_rerenders() -> None:
    query = make_query()
    qbit = make_qbit([make_file(0), make_file(1)])

    await toggle_file(query, fs("t", v=1), qbit, make_config())

    qbit.set_file_priority.assert_awaited_once_with(HASH, "1", 0)
    query.message.edit_text.assert_awaited_once()


async def test_toggle_unknown_file_alerts() -> None:
    query = make_query()
    qbit = make_qbit([make_file(0)])

    await toggle_file(query, fs("t", v=9), qbit, make_config())

    qbit.set_file_priority.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_select_none_batches_all_ids() -> None:
    query = make_query()
    qbit = make_qbit([make_file(0), make_file(1), make_file(2)])

    await select_all_files(query, fs("all", v=0), qbit, make_config())

    qbit.set_file_priority.assert_awaited_once_with(HASH, "0|1|2", 0)


async def test_start_requires_a_selected_file() -> None:
    query = make_query()
    qbit = make_qbit([make_file(0, priority=0)])

    await start_download(
        query,
        fs("go"),
        AsyncMock(spec=TorrentRepo),
        qbit,
        make_config(),
        cast(Bot, AsyncMock()),
    )

    qbit.start_torrents.assert_not_awaited()
    assert "хотя бы один" in query.answer.await_args.args[0]


async def test_start_launches_watcher_and_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watched: dict[str, Any] = {}

    async def fake_watch(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        watched.update(kwargs)

    monkeypatch.setattr("tj_bot.handlers.server_download.watch_download", fake_watch)
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    qbit = make_qbit()

    await start_download(
        query, fs("go"), repo, qbit, make_config(), cast(Bot, AsyncMock())
    )
    for _ in range(3):
        await asyncio.sleep(0)

    qbit.start_torrents.assert_awaited_once_with(HASH)
    repo.record_download.assert_awaited_once_with(111, "Movie", "server")
    assert watched["tag"] == "tjbot-abc123"


async def test_cancel_deletes_torrent_with_files() -> None:
    query = make_query()
    qbit = make_qbit()

    await cancel_download(query, fs("x"), qbit, make_config())

    qbit.delete_torrents.assert_awaited_once_with(HASH, delete_files=True)
    assert "отменена" in query.message.edit_text.await_args.args[0]


async def test_non_admin_taps_are_ignored() -> None:
    query = make_query()
    qbit = make_qbit()

    await toggle_file(query, fs("t"), qbit, make_config(admin=False))
    await cancel_download(query, fs("x"), qbit, make_config(admin=False))

    qbit.set_file_priority.assert_not_awaited()
    qbit.delete_torrents.assert_not_awaited()


async def test_begin_torrent_send_adds_stopped_and_opens_picker() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.return_value = b"torrentbytes"
    qbit = make_qbit()

    await begin_torrent_send(
        query,
        cast(Message, query.message),
        repo,
        jackett,
        qbit,
        make_config(),
        "Movie 1080p",
        "http://jackett:9117/dl/1",
    )

    kwargs = qbit.add_torrent_file.await_args.kwargs
    assert kwargs["paused"] is True
    assert kwargs["category"] == "tj-bot"
    assert kwargs["tag"].startswith("tjbot-")
    repo.commit.assert_awaited()
    # the picker landed on the freshly sent status message
    status = query.message.answer.return_value
    assert "Выбрано" in status.edit_text.await_args.args[0]


async def test_begin_torrent_send_reports_tracker_failure() -> None:
    query = make_query()
    jackett = AsyncMock(spec=JackettClient)
    jackett.download.side_effect = JackettError("down")
    qbit = make_qbit()

    await begin_torrent_send(
        query,
        cast(Message, query.message),
        AsyncMock(spec=TorrentRepo),
        jackett,
        qbit,
        make_config(),
        "Movie",
        "http://jackett:9117/dl/1",
    )

    qbit.add_torrent_file.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_begin_magnet_send_waits_for_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tj_bot.handlers.server_download.METADATA_POLL_SECONDS", 0)
    message = make_message()
    status = make_message()
    message.answer.return_value = status
    qbit = make_qbit()

    await begin_magnet_send(
        message,
        AsyncMock(spec=TorrentRepo),
        qbit,
        make_config(),
        "magnet:?xt=urn:btih:abc&dn=Movie",
    )

    add_kwargs = qbit.add_torrent_url.await_args.kwargs
    assert add_kwargs["stop_condition"] == "MetadataReceived"
    assert add_kwargs["category"] == "tj-bot"
    qbit.stop_torrents.assert_awaited_once_with(HASH)
    assert "Выбрано" in status.edit_text.await_args.args[0]


async def test_begin_magnet_send_times_out_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tj_bot.handlers.server_download.METADATA_POLL_SECONDS", 0)
    monkeypatch.setattr(
        "tj_bot.handlers.server_download.METADATA_TIMEOUT_SECONDS", 0.05
    )
    message = make_message()
    status = make_message()
    message.answer.return_value = status
    qbit = make_qbit()
    qbit.torrent_files.return_value = []  # metadata never arrives

    await begin_magnet_send(
        message,
        AsyncMock(spec=TorrentRepo),
        qbit,
        make_config(),
        "magnet:?xt=urn:btih:abc",
    )

    qbit.delete_torrents.assert_awaited_once_with(HASH, delete_files=True)
    assert "Не удалось получить метаданные" in status.edit_text.await_args.args[0]


async def test_render_reports_qbit_down() -> None:
    query = make_query()
    qbit = make_qbit()
    qbit.torrent_files.side_effect = QbittorrentError("down")

    await toggle_file(query, fs("t"), qbit, make_config())

    assert query.answer.await_args.kwargs.get("show_alert") is True
