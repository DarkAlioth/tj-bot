from unittest.mock import AsyncMock

from tj_bot.services.download_watcher import watch_completion
from tj_bot.services.qbittorrent import QbittorrentError


async def test_notifies_when_download_completes() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.side_effect = [
        [{"progress": 0.4, "name": "x"}],
        [{"progress": 1.0, "name": "x"}],
    ]

    await watch_completion(
        bot,
        qbit,
        chat_id=7,
        tag="t1",
        name="Movie",
        poll_interval_seconds=0,
        timeout_seconds=3600,
    )

    bot.send_message.assert_awaited_once()
    assert "Movie" in bot.send_message.await_args.args[1]


async def test_stops_after_timeout_without_notification() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.return_value = [{"progress": 0.1, "name": "x"}]

    await watch_completion(
        bot,
        qbit,
        chat_id=7,
        tag="t1",
        name="Movie",
        poll_interval_seconds=0,
        timeout_seconds=0,
    )

    bot.send_message.assert_not_awaited()


async def test_transient_poll_error_does_not_crash() -> None:
    bot = AsyncMock()
    qbit = AsyncMock()
    qbit.torrents_by_tag.side_effect = [
        QbittorrentError("temporary"),
        [{"progress": 1.0, "name": "x"}],
    ]

    await watch_completion(
        bot,
        qbit,
        chat_id=7,
        tag="t1",
        name="Movie",
        poll_interval_seconds=0,
        timeout_seconds=3600,
    )

    bot.send_message.assert_awaited_once()
