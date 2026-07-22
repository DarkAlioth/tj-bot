import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tj_bot.db.maintenance import cleanup_loop
from tj_bot.services.jackett import JackettError
from tj_bot.services.subscriptions import subscriptions_loop


class LoopStopError(Exception):
    pass


def pool_yielding(session: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


async def test_cleanup_loop_survives_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_cleanup_once(pool: object, ttl: object) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SQLAlchemyError("transient")
        raise LoopStopError  # exit on the second iteration

    sleeps = {"n": 0}

    async def fake_sleep(_seconds: float) -> None:
        sleeps["n"] += 1

    monkeypatch.setattr("tj_bot.db.maintenance.cleanup_once", fake_cleanup_once)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(LoopStopError):
        await cleanup_loop(MagicMock(), ttl=MagicMock(), interval_seconds=1)

    # first iteration raised SQLAlchemyError but the loop continued to a second
    assert calls["n"] == 2


async def test_subscriptions_loop_continues_past_bad_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = MagicMock(id=2, chat_id=20, query_text="debian")
    bad = MagicMock(id=1, chat_id=10, query_text="ubuntu")
    session = AsyncMock()
    repo = MagicMock()
    repo.all_subscriptions = AsyncMock(return_value=[bad, good])

    monkeypatch.setattr("tj_bot.services.subscriptions.TorrentRepo", lambda _s: repo)

    checked: list[int] = []

    async def fake_check(
        bot: object,
        r: object,
        j: object,
        sub_id: int,
        chat_id: int,
        query_text: str,
    ) -> int:
        checked.append(sub_id)
        if sub_id == 1:
            raise JackettError("boom")  # bad subscription
        raise LoopStopError  # good one reached → stop the test

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("tj_bot.services.subscriptions.check_subscription", fake_check)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(LoopStopError):
        await subscriptions_loop(
            AsyncMock(), pool_yielding(session), AsyncMock(), interval_seconds=1
        )

    # the JackettError on subscription 1 did not stop the sweep reaching 2
    assert checked == [1, 2]
