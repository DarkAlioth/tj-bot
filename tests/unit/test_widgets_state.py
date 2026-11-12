import pytest

from tj_bot.widgets.state import (
    MAX_STATE_BYTES,
    StateTooLargeError,
    dumps_state,
    loads_state,
)


def test_dumps_state_is_compact_and_keeps_unicode() -> None:
    assert dumps_state({"v": 1, "r": 2, "q": "кино"}) == '{"v":1,"r":2,"q":"кино"}'


def test_dumps_state_rejects_over_limit() -> None:
    with pytest.raises(StateTooLargeError):
        dumps_state({"x": "ж" * (MAX_STATE_BYTES // 2)})


def test_round_trip() -> None:
    state = {"v": 1, "r": 3, "i": [1, 2], "t": None}

    assert loads_state(dumps_state(state)) == state


def test_loads_state_absent_or_empty_is_none() -> None:
    assert loads_state(None) is None
    assert loads_state("") is None


def test_loads_state_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        loads_state("[1]")
