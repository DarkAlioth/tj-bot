import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tj_bot.db.maintenance import cleanup_loop


class LoopStopError(Exception):
    pass


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
