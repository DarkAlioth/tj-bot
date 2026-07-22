from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup

from tj_bot.services.download_watcher import watch_download
from tj_bot.services.qbittorrent import QbittorrentError

KB = InlineKeyboardMarkup(inline_keyboard=[])


def kb_for(_state: str) -> InlineKeyboardMarkup:
    return KB


async def run(
    bot: AsyncMock, qbit: AsyncMock, interval: int = 0, timeout: int = 3600
) -> None:
    await watch_download(
        bot,
        qbit,
        chat_id=7,
        message_id=99,
        tag="t1",
        name="Movie",
        keyboard_for_state=kb_for,
        poll_interval_seconds=interval,
        timeout_seconds=timeout,
    )


async def test_live_progress_then_completion() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.side_effect = [
        [{"progress": 0.4, "state": "downloading", "name": "Movie"}],
        [{"progress": 1.0, "state": "uploading", "name": "Movie"}],
    ]

    await run(bot, qbit)

    # first poll edits with progress, second edits with completion
    assert bot.edit_message_text.await_count == 2
    final_text = bot.edit_message_text.await_args.args[0]
    assert "завершена" in final_text


async def test_deleted_torrent_reported() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.return_value = []

    await run(bot, qbit)

    assert "Удалён" in bot.edit_message_text.await_args.args[0]


async def test_transient_poll_error_continues() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.side_effect = [
        QbittorrentError("temp"),
        [{"progress": 1.0, "state": "uploading", "name": "Movie"}],
    ]

    await run(bot, qbit)

    assert "завершена" in bot.edit_message_text.await_args.args[0]


async def test_timeout_stops_without_completion() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.return_value = [
        {"progress": 0.1, "state": "downloading", "name": "Movie"}
    ]

    await watch_download(
        bot,
        qbit,
        chat_id=7,
        message_id=99,
        tag="t1",
        name="Movie",
        keyboard_for_state=kb_for,
        poll_interval_seconds=0,
        timeout_seconds=0,
    )

    # deadline already passed -> no poll, no completion edit
    assert not any(
        "завершена" in (c.args[0] if c.args else "")
        for c in bot.edit_message_text.await_args_list
    )
