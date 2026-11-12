"""JSON helpers for widget ``chat_data`` (Bot API limit: 1024 bytes)."""

import json
from typing import Any

MAX_STATE_BYTES = 1024


class StateTooLargeError(ValueError):
    """The serialized state exceeds the 1024-byte ``chat_data`` limit."""


def dumps_state(state: dict[str, Any]) -> str:
    """Serialize compactly (no spaces, non-ASCII kept) within the size limit."""
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    size = len(raw.encode("utf-8"))
    if size > MAX_STATE_BYTES:
        raise StateTooLargeError(f"state is {size} bytes, limit is {MAX_STATE_BYTES}")
    return raw


def loads_state(raw: str | None) -> dict[str, Any] | None:
    """Parse ``chat_data``; ``None`` for absent or empty data."""
    if not raw:
        return None
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise ValueError("widget state must be a JSON object")
    return state
