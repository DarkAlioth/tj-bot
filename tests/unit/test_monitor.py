import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.monitor import (
    build_alert_text,
    check_once,
    collect_problems,
    monitor_loop,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

GB = 1024**3


def make_jackett(indexers: list[dict[str, object]] | None = None) -> AsyncMock:
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.return_value = indexers if indexers is not None else []
    return jackett


def make_qbit(free_space: int = 100 * GB) -> AsyncMock:
    qbit = AsyncMock(spec=QbittorrentClient)
    qbit.free_space.return_value = free_space
    return qbit


def make_config() -> AppConfig:
    config = MagicMock()
    config.admin_ids = [111]
    config.settings.alert_free_space_gb = 10
    return cast(AppConfig, config)


async def test_collect_reports_broken_indexers_only() -> None:
    jackett = make_jackett(
        [
            {"Name": "Good", "Error": None},
            {"Name": "Bad", "Error": "login failed"},
        ]
    )

    problems = await collect_problems(jackett, make_qbit(), 10 * GB)

    assert list(problems) == ["indexer:Bad"]
    assert "Bad" in problems["indexer:Bad"]


async def test_collect_reports_jackett_down() -> None:
    jackett = make_jackett()
    jackett.indexers.side_effect = JackettError("down")

    problems = await collect_problems(jackett, None, 10 * GB)

    assert list(problems) == ["jackett"]


async def test_collect_reports_low_space() -> None:
    problems = await collect_problems(make_jackett(), make_qbit(2 * GB), 10 * GB)

    assert list(problems) == ["space"]
    assert "2.0 GB" in problems["space"]


async def test_collect_skips_space_when_qbit_unreachable_or_unknown() -> None:
    down = make_qbit()
    down.free_space.side_effect = QbittorrentError("off")
    assert await collect_problems(make_jackett(), down, 10 * GB) == {}

    unknown = make_qbit(-1)
    assert await collect_problems(make_jackett(), unknown, 10 * GB) == {}

    assert await collect_problems(make_jackett(), None, 10 * GB) == {}


async def test_check_once_baseline_is_silent() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"Name": "Bad", "Error": "err"}])

    known = await check_once(bot, jackett, None, make_config(), None)

    assert "indexer:Bad" in known
    bot.send_message.assert_not_awaited()


async def test_check_once_alerts_on_new_problem() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"Name": "Bad", "Error": "err"}])

    known = await check_once(bot, jackett, None, make_config(), {})

    assert "indexer:Bad" in known
    text = bot.send_message.await_args.args[1]
    assert "⚠️ Индексер упал: Bad" in text


async def test_check_once_alerts_on_recovery() -> None:
    bot = AsyncMock(spec=Bot)

    known = await check_once(
        bot, make_jackett(), None, make_config(), {"indexer:Bad": "x"}
    )

    assert known == {}
    text = bot.send_message.await_args.args[1]
    assert "✅ Индексер снова в строю: Bad" in text


async def test_check_once_silent_when_unchanged() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"Name": "Bad", "Error": "err"}])

    await check_once(bot, jackett, None, make_config(), {"indexer:Bad": "x"})

    bot.send_message.assert_not_awaited()


def test_alert_text_mixes_new_and_recovered() -> None:
    text = build_alert_text(["Jackett недоступен"], ["Место на диске снова в норме"])

    assert text.startswith("🚨")
    assert "⚠️ Jackett недоступен" in text
    assert "✅ Место на диске снова в норме" in text


class LoopStopError(Exception):
    pass


async def test_monitor_loop_sleeps_first_and_carries_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    states: list[dict[str, str] | None] = []

    async def fake_sleep(_seconds: float) -> None:
        order.append("sleep")

    async def fake_check_once(
        bot: object,
        jackett: object,
        qbit: object,
        config: object,
        known: dict[str, str] | None,
    ) -> dict[str, str]:
        order.append("check")
        states.append(known)
        if len(states) == 2:
            raise LoopStopError
        return {"jackett": "down"}

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("tj_bot.services.monitor.check_once", fake_check_once)

    with pytest.raises(LoopStopError):
        await monitor_loop(
            cast(Bot, AsyncMock()), make_jackett(), None, make_config(), 60
        )

    assert order == ["sleep", "check", "sleep", "check"]
    assert states == [None, {"jackett": "down"}]
