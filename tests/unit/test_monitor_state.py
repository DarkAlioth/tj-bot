import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.monitor import (
    IndexerStatus,
    MonitorState,
    collect_problems,
    indexer_problem,
    indexer_status,
    probe_now,
)

GB = 1024**3
RAW: list[dict[str, Any]] = [
    {"ID": "rutracker", "Name": "RuTracker", "Error": None, "Results": 1204},
    {"ID": "rutor", "Name": "RuTor", "Error": "login failed", "Results": 0},
]


def make_jackett(indexers: list[dict[str, Any]] | None = None) -> AsyncMock:
    jackett = AsyncMock(spec=JackettClient)
    jackett.indexers.return_value = indexers if indexers is not None else RAW
    jackett.take_observed_errors = MagicMock(return_value={})
    return jackett


def make_config() -> AppConfig:
    config = MagicMock()
    config.admin_ids = [111]
    config.settings.alert_free_space_gb = 10
    return cast(AppConfig, config)


def test_indexer_status_parses_a_raw_row() -> None:
    assert indexer_status(RAW[0]) == IndexerStatus(
        id="rutracker", name="RuTracker", ok=True, error=None, results=1204
    )
    assert indexer_status(RAW[1]).error == "login failed"
    assert indexer_status({"Name": "Bad", "Error": "e"}).id == "Bad"
    assert indexer_status({}) == IndexerStatus("?", "?", True, None, 0)


def test_known_is_none_until_the_baseline_pass() -> None:
    state = MonitorState()

    assert state.known is None
    assert not state.probe_running
    state.checked_at = 1
    assert state.known == {}


async def test_collect_keeps_the_snapshot_and_leaves_it_when_jackett_is_down() -> None:
    state = MonitorState()

    problems = await collect_problems(make_jackett(), None, 10 * GB, state)

    assert list(problems) == ["indexer:rutor"]
    assert [ix.name for ix in state.indexers] == ["RuTracker", "RuTor"]

    down = make_jackett()
    down.indexers.side_effect = JackettError("down")
    assert list(await collect_problems(down, None, 10 * GB, state)) == ["jackett"]
    assert len(state.indexers) == 2


async def test_probe_now_records_the_pass_and_shares_a_running_probe() -> None:
    gate = asyncio.Event()
    jackett = make_jackett()

    async def slow_indexers() -> list[dict[str, Any]]:
        await gate.wait()
        return RAW

    jackett.indexers.side_effect = slow_indexers
    state = MonitorState()
    bot = cast(Bot, AsyncMock(spec=Bot))

    first = asyncio.create_task(probe_now(bot, jackett, None, make_config(), state))
    await asyncio.sleep(0)
    assert state.probe_running
    second = asyncio.create_task(probe_now(bot, jackett, None, make_config(), state))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second)

    assert results[0] == results[1] == {"indexer:rutor": indexer_problem("RuTor")}
    assert jackett.indexers.await_count == 1
    assert not state.probe_running
    assert state.checked_at > 0
    assert state.known == {"indexer:rutor": indexer_problem("RuTor")}


async def test_probe_now_announces_against_the_shared_state() -> None:
    jackett = make_jackett()
    state = MonitorState(checked_at=1)  # baseline done, nothing known
    bot = AsyncMock(spec=Bot)

    await probe_now(cast(Bot, bot), jackett, None, make_config(), state)

    text = bot.send_message.await_args.args[1]
    assert "⚠️ Индексер упал: RuTor" in text
    assert "indexer:rutor" in state.problems
