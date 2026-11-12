"""State builders for the ``hub`` widget (docs/widgets/hub/PROTOCOL.md).

Pure functions: the handler loads the rows, these shape them into the
``chat_data`` JSON within its 1024-byte budget.
"""

import datetime
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from tj_bot.db.models import Favorite
from tj_bot.db.repo import HistoryEntry
from tj_bot.services.formatting import format_size_ru, group_thousands
from tj_bot.widgets.state import dumps_state

TAB_MAIN = "m"
TAB_HISTORY = "h"
TAB_FAVORITES = "f"
TABS = (TAB_MAIN, TAB_HISTORY, TAB_FAVORITES)

FILTER_ALL = "a"
FILTER_UPDATED = "u"
FILTERS = (FILTER_ALL, FILTER_UPDATED)

HISTORY_ROWS = {TAB_MAIN: 5, TAB_HISTORY: 10}
FAVORITES_PAGE_SIZE = 4
QUERY_MAX_CHARS = 40
TITLE_MAX_CHARS = 60
TITLE_MAX_BYTES = 90
TRACKER_MAX_CHARS = 12
DETAILS_URL_MAX_CHARS = 256
# history rows are dropped (oldest first) until the state fits this
STATE_BUDGET_BYTES = 1000
ELLIPSIS = "…"

TRACKER_NAMES = {
    "rutracker": "RuTracker",
    "rutor": "RuTor",
    "megapeer": "MegaPeer",
    "nonameclub": "NoNaMe",
    "nnmclub": "NoNaMe",
    "tapochek": "Tapochek",
    "pctorrent": "PC-torrent",
    "byrutor": "Byrutor",
}

CAPTION = (
    "Напишите /s и название, чтобы найти торрент.\n"
    "История — /history, избранное — /favorites."
)
ADMIN_CAPTION = "Консоль — /dl, панель — /health, пользователи — /users."


@dataclass(frozen=True)
class HubStatus:
    """The main tab's status line: last probe of Jackett and qBittorrent."""

    jackett_ok: bool
    qbit_ok: bool
    at: datetime.datetime


class HubStatusSource(Protocol):
    """What ``dp["monitor"]`` provides for the status line, when registered."""

    def hub_status(self) -> HubStatus | None: ...


def cut(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + ELLIPSIS


def cut_title(title: str) -> str:
    """Favorite title within 60 chars and 90 UTF-8 bytes (unescaped first)."""
    text = html.unescape(title)
    if len(text) <= TITLE_MAX_CHARS and len(text.encode()) <= TITLE_MAX_BYTES:
        return text
    keep = TITLE_MAX_CHARS - 1
    budget = TITLE_MAX_BYTES - len(ELLIPSIS.encode())
    while len(text[:keep].encode()) > budget:
        keep -= 1
    return text[:keep].rstrip() + ELLIPSIS


def tracker_name(tracker: str) -> str:
    """Display name of a Jackett indexer id («noname-club» -> «NoNaMe»)."""
    raw = html.unescape(tracker)
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    return TRACKER_NAMES.get(key, raw[:TRACKER_MAX_CHARS])


def history_age(created_at: datetime.datetime, now: datetime.datetime) -> int | str:
    """Minutes when < 60, else «HH:MM» the same local day, else «DD.MM»."""
    created = created_at.astimezone(now.tzinfo)
    minutes = int((now - created).total_seconds() // 60)
    if minutes < 60:
        return max(minutes, 0)
    if created.date() == now.date():
        return created.strftime("%H:%M")
    return created.strftime("%d.%m")


def history_row(entry: HistoryEntry, now: datetime.datetime) -> dict[str, Any]:
    return {
        "i": entry.query_id,
        "q": cut(entry.text, QUERY_MAX_CHARS),
        "a": history_age(entry.created_at, now),
        "n": entry.result_count,
    }


def favorite_updated(favorite: Favorite) -> bool:
    """Changed on the tracker since the owner last acted on it."""
    if favorite.updated_at is None:
        return False
    return favorite.seen_at is None or favorite.updated_at > favorite.seen_at


def favorite_meta(favorite: Favorite) -> str:
    return (
        f"{format_size_ru(favorite.size)} · {favorite.published_at:%d.%m.%Y}"
        f" · ⇡{group_thousands(favorite.seeders)}"
    )


def favorite_row(favorite: Favorite) -> dict[str, Any]:
    row: dict[str, Any] = {
        "i": favorite.id,
        "n": cut_title(favorite.title),
        "t": tracker_name(favorite.tracker),
        "m": favorite_meta(favorite),
    }
    if favorite_updated(favorite):
        row["u"] = 1
    return row


def details_link(url: str) -> dict[str, str]:
    """The ``fo:`` answer: the link only when https and short enough."""
    link = html.unescape(url)
    if link.startswith("https://") and len(link) <= DETAILS_URL_MAX_CHARS:
        return {"l": link}
    return {}


def favorites_pages(count: int) -> int:
    return max(1, -(-count // FAVORITES_PAGE_SIZE))


def clamp_page(page: int, count: int) -> int:
    return min(max(page, 0), favorites_pages(count) - 1)


def status_fragment(status: HubStatus) -> dict[str, Any]:
    return {
        "j": int(status.jackett_ok),
        "q": int(status.qbit_ok),
        "w": status.at.astimezone().strftime("%H:%M"),
    }


def state_bytes(state: dict[str, Any]) -> int:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return len(raw.encode("utf-8"))


def hub_state(
    *,
    tab: str,
    rev: int,
    admin: bool,
    favorites_total: int,
    favorites_updated: int,
    history: Sequence[HistoryEntry] = (),
    favorites: Sequence[Favorite] = (),
    favorites_filter: str = FILTER_ALL,
    favorites_page: int = 0,
    status: HubStatus | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """The widget's chat_data for one tab, within the 1024-byte budget.

    Only the current tab's rows are included; history rows are capped per
    tab and the oldest dropped while the JSON exceeds ``STATE_BUDGET_BYTES``.
    """
    state: dict[str, Any] = {"v": 1, "r": rev, "tb": tab}
    if admin:
        state["adm"] = 1
        if status is not None and tab == TAB_MAIN:
            state["st"] = status_fragment(status)
    state["fn"] = favorites_total
    state["fu"] = favorites_updated
    if tab == TAB_FAVORITES:
        state["ff"] = favorites_filter
        state["fp"] = favorites_page
        state["f"] = [favorite_row(f) for f in favorites[:FAVORITES_PAGE_SIZE]]
    else:
        moment = now or datetime.datetime.now().astimezone()
        rows = [history_row(e, moment) for e in history[: HISTORY_ROWS[tab]]]
        state["h"] = rows
        while rows and state_bytes(state) > STATE_BUDGET_BYTES:
            rows.pop()
    dumps_state(state)  # raises when a row set still exceeds the hard limit
    return state
