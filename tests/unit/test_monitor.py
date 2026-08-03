import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.monitor import (
    JACKETT_PROBLEM,
    Problem,
    build_alert_text,
    check_once,
    collect_problems,
    indexer_problem,
    monitor_loop,
    recheck_problems,
    space_problem,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

GB = 1024**3


def make_jackett(indexers: list[dict[str, object]] | None = None) -> AsyncMock:
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.return_value = indexers if indexers is not None else []
    jackett.probe_indexer.return_value = None
    jackett.observed_errors = MagicMock(return_value={})
    jackett.take_observed_errors = MagicMock(return_value={})
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
            {"ID": "good", "Name": "Good", "Error": None},
            {"ID": "bad", "Name": "Bad", "Error": "login failed"},
        ]
    )

    problems = await collect_problems(jackett, make_qbit(), 10 * GB)

    assert list(problems) == ["indexer:bad"]
    assert "Bad" in problems["indexer:bad"].alert


async def test_collect_falls_back_to_name_without_id() -> None:
    jackett = make_jackett([{"Name": "Bad", "Error": "err"}])

    problems = await collect_problems(jackett, None, 10 * GB)

    assert list(problems) == ["indexer:Bad"]


async def test_collect_reports_jackett_down() -> None:
    jackett = make_jackett()
    jackett.indexers.side_effect = JackettError("down")

    problems = await collect_problems(jackett, None, 10 * GB)

    assert list(problems) == ["jackett"]


async def test_collect_reports_low_space() -> None:
    problems = await collect_problems(make_jackett(), make_qbit(2 * GB), 10 * GB)

    assert list(problems) == ["space"]
    assert "2.0 GB" in problems["space"].alert


async def test_collect_skips_space_when_qbit_unreachable_or_unknown() -> None:
    down = make_qbit()
    down.free_space.side_effect = QbittorrentError("off")
    assert await collect_problems(make_jackett(), down, 10 * GB) == {}

    unknown = make_qbit(-1)
    assert await collect_problems(make_jackett(), unknown, 10 * GB) == {}

    assert await collect_problems(make_jackett(), None, 10 * GB) == {}


async def test_check_once_baseline_is_silent() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"ID": "bad", "Name": "Bad", "Error": "err"}])

    known = await check_once(bot, jackett, None, make_config(), None)

    assert "indexer:bad" in known
    bot.send_message.assert_not_awaited()


async def test_check_once_alerts_on_new_problem() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"ID": "bad", "Name": "Bad", "Error": "err"}])

    known = await check_once(bot, jackett, None, make_config(), {})

    assert "indexer:bad" in known
    text = bot.send_message.await_args.args[1]
    assert "⚠️ Индексер упал: Bad" in text


async def test_check_once_alerts_on_recovery() -> None:
    bot = AsyncMock(spec=Bot)

    known = await check_once(
        bot,
        make_jackett(),
        None,
        make_config(),
        {"indexer:bad": indexer_problem("Bad")},
    )

    assert known == {}
    text = bot.send_message.await_args.args[1]
    assert "✅ Индексер снова в строю: Bad" in text


async def test_check_once_silent_when_unchanged() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett([{"ID": "bad", "Name": "Bad", "Error": "err"}])

    await check_once(
        bot, jackett, None, make_config(), {"indexer:bad": indexer_problem("Bad")}
    )

    bot.send_message.assert_not_awaited()


def test_alert_text_mixes_new_and_recovered() -> None:
    text = build_alert_text(["Jackett недоступен"], ["Место на диске снова в норме"])

    assert text.startswith("🚨")
    assert "⚠️ Jackett недоступен" in text
    assert "✅ Место на диске снова в норме" in text


async def test_recheck_probes_only_failed_and_announces_recovery() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett()
    jackett.probe_indexer.return_value = None
    known = {"indexer:rutracker": indexer_problem("RuTracker.org")}

    problems = await recheck_problems(bot, jackett, None, make_config(), known)

    assert problems == {}
    jackett.probe_indexer.assert_awaited_once_with("rutracker")
    jackett.indexers.assert_not_awaited()  # healthy indexers are left alone
    text = bot.send_message.await_args.args[1]
    assert "✅ Индексер снова в строю: RuTracker.org" in text


async def test_recheck_keeps_confirmed_failure_silently() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett()
    jackett.probe_indexer.return_value = "still down"
    known = {"indexer:rutracker": indexer_problem("RuTracker.org")}

    problems = await recheck_problems(bot, jackett, None, make_config(), known)

    assert "indexer:rutracker" in problems
    bot.send_message.assert_not_awaited()


async def test_recheck_confirms_search_observed_failure() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett()
    jackett.take_observed_errors = MagicMock(return_value={"rutor": "RuTor"})
    jackett.probe_indexer.return_value = "http 500"

    problems = await recheck_problems(bot, jackett, None, make_config(), {})

    assert "indexer:rutor" in problems
    text = bot.send_message.await_args.args[1]
    assert "⚠️ Индексер упал: RuTor" in text


async def test_recheck_dismisses_transient_observation() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett()
    jackett.take_observed_errors = MagicMock(return_value={"rutor": "RuTor"})
    jackett.probe_indexer.return_value = None

    problems = await recheck_problems(bot, jackett, None, make_config(), {})

    assert problems == {}
    bot.send_message.assert_not_awaited()


async def test_recheck_marks_jackett_down_when_probe_fails() -> None:
    bot = AsyncMock(spec=Bot)
    jackett = make_jackett()
    jackett.probe_indexer.side_effect = JackettError("boom")
    known = {"indexer:rutracker": indexer_problem("RuTracker.org")}

    problems = await recheck_problems(bot, jackett, None, make_config(), known)

    assert "jackett" in problems
    assert "indexer:rutracker" in problems  # state unknown, keep it
    text = bot.send_message.await_args.args[1]
    assert "⚠️ Jackett недоступен" in text


async def test_recheck_announces_space_recovery() -> None:
    bot = AsyncMock(spec=Bot)
    known: dict[str, Problem] = {"space": space_problem(2 * GB)}

    problems = await recheck_problems(
        bot, make_jackett(), make_qbit(100 * GB), make_config(), known
    )

    assert problems == {}
    text = bot.send_message.await_args.args[1]
    assert "✅ Место на диске снова в норме" in text


class LoopStopError(Exception):
    pass


async def test_monitor_loop_sleeps_first_and_carries_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    states: list[dict[str, Problem] | None] = []

    async def fake_sleep(_seconds: float) -> None:
        order.append("sleep")

    async def fake_check_once(
        bot: object,
        jackett: object,
        qbit: object,
        config: object,
        known: dict[str, Problem] | None,
    ) -> dict[str, Problem]:
        order.append("check")
        states.append(known)
        if len(states) == 2:
            raise LoopStopError
        return {"jackett": JACKETT_PROBLEM}

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("tj_bot.services.monitor.check_once", fake_check_once)

    with pytest.raises(LoopStopError):
        await monitor_loop(
            cast(Bot, AsyncMock()), make_jackett(), None, make_config(), 60
        )

    assert order == ["sleep", "check", "sleep", "check"]
    assert states == [None, {"jackett": JACKETT_PROBLEM}]


async def test_monitor_loop_switches_to_fast_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed indexer flips the loop to short sleeps and targeted probes."""
    sleeps: list[float] = []
    calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_check_once(*_args: object) -> dict[str, Problem]:
        calls.append("full")
        return {"indexer:bad": indexer_problem("Bad")}

    async def fake_recheck(*_args: object) -> dict[str, Problem]:
        calls.append("recheck")
        if len(calls) == 3:
            raise LoopStopError
        return {"indexer:bad": indexer_problem("Bad")}

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("tj_bot.services.monitor.check_once", fake_check_once)
    monkeypatch.setattr("tj_bot.services.monitor.recheck_problems", fake_recheck)

    with pytest.raises(LoopStopError):
        await monitor_loop(
            cast(Bot, AsyncMock()),
            make_jackett(),
            None,
            make_config(),
            1800,
            recheck_interval_seconds=300,
        )

    assert calls == ["full", "recheck", "recheck"]
    assert sleeps == [1800, 300, 300]
