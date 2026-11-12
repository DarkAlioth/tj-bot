"""State documents, answers and captions of the admin widgets.

Pure builders for ``dashboard``, ``users`` and ``broadcast`` as specified in
``docs/widgets/<name>/PROTOCOL.md``: every state is trimmed with the
protocol's ``fit()`` rules and checked against the 1024-byte ``chat_data``
limit before it is returned.
"""

import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from tj_bot.db.models import BotUser
from tj_bot.db.repo import SearchStats
from tj_bot.services.formatting import format_size, format_speed
from tj_bot.services.monitor import IndexerStatus, Problem
from tj_bot.widgets.state import MAX_STATE_BYTES, dumps_state

STATE_VERSION = 1
STATS_WINDOW_DAYS = 7

DASHBOARD_TABS = frozenset({"h", "s", "i"})
TOP_QUERIES = 8
TOP_QUERIES_FIT = 5
TOP_TEXT_CHARS = 24
INDEXER_NAME_CHARS = 14
INDEXER_ERROR_CHARS = 40
INDEXER_ERROR_FIT = 24
JACKETT_DOWN_TEXT = "Jackett недоступен"
INDEXER_DOWN_TEXT = "не отвечает"

USER_GROUPS = {"a": "all", "r": "regular", "m": "admins", "b": "blocked"}
USERS_PAGE_SIZE = 8
USER_NAME_CHARS = 24
USER_NAME_FIT = 20
USERNAME_CHARS = 16
FLAG_ADMIN = 1
FLAG_SUPER = 2
FLAG_BLOCKED = 4
ACTIVITY_ROWS = 5
ACTIVITY_TEXT_CHARS = 40
DOWNLOAD_KINDS = {"chat": "c", "server": "s"}

BROADCAST_TEXT_CHARS = 600
BROADCAST_BUDGET_BYTES = 1000
BROADCAST_CUT_CHARS = 20

type BroadcastStatus = Literal["draft", "run", "done", "cancel"]


def _size(document: dict[str, Any]) -> int:
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return len(raw.encode("utf-8"))


def format_duration(seconds: int) -> str:
    minutes, _ = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


# --- dashboard -------------------------------------------------------------


@dataclass(frozen=True)
class QbitSummary:
    active: int
    total: int
    dl_speed: int
    up_speed: int
    free: int


@dataclass(frozen=True)
class Health:
    """The ``h`` tab: uptime and disk in seconds/bytes, ``checked_at`` unix seconds."""

    uptime: int
    disk: tuple[int, int]
    db_size: int
    qbit: QbitSummary | None
    checked_at: int


def qbit_doc(summary: QbitSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    # qBittorrent reports free space only, never the disk size: the widget
    # draws an empty bar for a zero total
    return {
        "a": summary.active,
        "n": summary.total,
        "d": summary.dl_speed,
        "u": summary.up_speed,
        "f": [max(summary.free, 0), 0],
    }


def health_doc(health: Health) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "up": health.uptime,
        "df": list(health.disk),
        "db": health.db_size,
        "q": qbit_doc(health.qbit),
        "m": health.checked_at,
    }
    return doc


def indexer_rows(
    indexers: Sequence[IndexerStatus],
    problems: dict[str, Problem],
    error_chars: int = INDEXER_ERROR_CHARS,
) -> list[list[Any]]:
    """``[name, results]`` per healthy indexer, ``[name, 0, error]`` per broken one.

    ``problems`` (the monitor's current view) wins over the snapshot, so a
    recovery or failure confirmed by a partial recheck shows at once.
    """
    rows: list[list[Any]] = []
    for indexer in indexers:
        name = indexer.name[:INDEXER_NAME_CHARS]
        if "jackett" in problems:
            rows.append([name, 0, JACKETT_DOWN_TEXT[:error_chars]])
        elif f"indexer:{indexer.id}" in problems:
            error = indexer.error or INDEXER_DOWN_TEXT
            rows.append([name, 0, error[:error_chars]])
        else:
            rows.append([name, indexer.results])
    return rows


def top_rows(stats: SearchStats) -> list[list[Any]]:
    return [
        [text[:TOP_TEXT_CHARS], count]
        for text, count in stats.top_queries[:TOP_QUERIES]
    ]


def dashboard_state(
    *,
    rev: int,
    now: int,
    tab: str,
    health: Health,
    stats: SearchStats,
    per_day: Sequence[int],
    indexers: Sequence[IndexerStatus],
    problems: dict[str, Problem],
) -> dict[str, Any]:
    if tab not in DASHBOARD_TABS:
        raise ValueError(f"unknown dashboard tab {tab!r}")
    state: dict[str, Any] = {
        "v": STATE_VERSION,
        "r": rev,
        "t": now,
        "a": tab,
        "h": health_doc(health),
        "s": {
            "d7": list(per_day),
            "ev": stats.events_window,
            "us": stats.users_window,
            "tc": stats.torrents,
            "qc": stats.queries,
            "top": top_rows(stats),
        },
        "i": indexer_rows(indexers, problems),
    }
    if _size(state) > MAX_STATE_BYTES:
        state["i"] = indexer_rows(indexers, problems, INDEXER_ERROR_FIT)
    top: list[list[Any]] = state["s"]["top"]
    while _size(state) > MAX_STATE_BYTES and len(top) > TOP_QUERIES_FIT:
        top.pop()
    dumps_state(state)
    return state


def dashboard_tick(
    rev: int, now: int, health: Health, rows: list[list[Any]] | None = None
) -> dict[str, Any]:
    """The ``bg`` answer; ``rows`` only when the indexer list changed."""
    answer: dict[str, Any] = {"r": rev, "t": now, "h": health_doc(health)}
    if rows is not None:
        answer["i"] = rows
    return answer


def dashboard_caption(
    health: Health,
    stats: SearchStats,
    indexers: Sequence[IndexerStatus],
    problems: dict[str, Problem],
) -> str:
    qbit = health.qbit
    if qbit is None:
        qbit_text = "недоступен"
    else:
        qbit_text = (
            f"{qbit.active}/{qbit.total} "
            f"({format_speed(qbit.dl_speed)}↓ {format_speed(qbit.up_speed)}↑)"
        )
    healthy = sum(1 for row in indexer_rows(indexers, problems) if len(row) == 2)
    return (
        f"🩺 Панель: аптайм {format_duration(health.uptime)}"
        f" · диск {format_size(health.disk[0])}"
        f" · qBittorrent {qbit_text}"
        f" · индексеры {healthy}/{len(indexers)}\n"
        f"📊 {STATS_WINDOW_DAYS} дней: {stats.events_window} поисков,"
        f" {stats.users_window} пользователей"
    )


# --- users -----------------------------------------------------------------


@dataclass(frozen=True)
class UserRow:
    user_id: int
    name: str
    username: str | None
    flags: int
    last_seen: int
    first_seen: int
    searches: int
    downloads: int


def unix_minutes(value: datetime.datetime) -> int:
    return int(value.timestamp() // 60)


def user_flags(user: BotUser, is_super: bool) -> int:
    flags = 0
    if user.admin:
        flags |= FLAG_ADMIN
    if is_super:
        flags |= FLAG_SUPER
    if user.blocked:
        flags |= FLAG_BLOCKED
    return flags


def user_row(user: BotUser, is_super: bool, searches: int, downloads: int) -> UserRow:
    name = user.full_name or user.username or str(user.user_id)
    return UserRow(
        user_id=user.user_id,
        name=name[:USER_NAME_CHARS],
        username=user.username[:USERNAME_CHARS] if user.username else None,
        flags=user_flags(user, is_super),
        last_seen=unix_minutes(user.last_seen),
        first_seen=unix_minutes(user.first_seen),
        searches=searches,
        downloads=downloads,
    )


def _user_doc(row: UserRow, name_chars: int) -> list[Any]:
    return [
        row.user_id,
        row.name[:name_chars],
        row.username,
        row.flags,
        row.last_seen,
        row.first_seen,
        row.searches,
        row.downloads,
    ]


def users_state(
    *,
    rev: int,
    now: int,
    group: str,
    page: int,
    pages: int,
    counts: Sequence[int],
    rows: Sequence[UserRow],
) -> dict[str, Any]:
    if group not in USER_GROUPS:
        raise ValueError(f"unknown users group {group!r}")
    state: dict[str, Any] = {
        "v": STATE_VERSION,
        "r": rev,
        "t": now,
        "g": group,
        "p": page,
        "n": pages,
        "c": list(counts),
        "u": [_user_doc(row, USER_NAME_CHARS) for row in rows],
    }
    if _size(state) > MAX_STATE_BYTES:
        state["u"] = [_user_doc(row, USER_NAME_FIT) for row in rows]
    dumps_state(state)
    return state


def with_toast(state: dict[str, Any], toast: str | None) -> dict[str, Any]:
    """The full-state answer plus ``m``; the toast is dropped when it overflows."""
    if toast is None:
        return state
    answer = {**state, "m": toast}
    return answer if _size(answer) <= MAX_STATE_BYTES else state


def activity_answer(
    searches: Sequence[tuple[str, datetime.datetime]],
    downloads: Sequence[tuple[str, str, datetime.datetime]],
) -> dict[str, Any]:
    return {
        "ok": 1,
        "a": {
            "s": [
                [text[:ACTIVITY_TEXT_CHARS], unix_minutes(when)]
                for text, when in searches[:ACTIVITY_ROWS]
            ],
            "d": [
                [
                    title[:ACTIVITY_TEXT_CHARS],
                    unix_minutes(when),
                    DOWNLOAD_KINDS.get(kind, kind[:1]),
                ]
                for title, kind, when in downloads[:ACTIVITY_ROWS]
            ],
        },
    }


def users_caption(counts: Sequence[int], page: int, pages: int) -> str:
    total, regular, admins, blocked = counts
    caption = (
        f"👥 Пользователи: всего {total} · обычных {regular}"
        f" · админов {admins} · в блоке {blocked}"
    )
    if pages > 1:
        caption += f" · стр. {page + 1}/{pages}"
    return caption


# --- broadcast -------------------------------------------------------------


def _cut_words(text: str, chars: int) -> str:
    cut = text[:-chars]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    return cut[:boundary] if boundary > 0 else cut


def broadcast_state(
    *,
    rev: int,
    status: BroadcastStatus,
    recipients: int,
    ok: int,
    fail: int,
    text: str,
) -> dict[str, Any]:
    """``tx`` is the plain text as typed (the widget renders it as text, not HTML)."""
    preview = text[:BROADCAST_TEXT_CHARS]
    state: dict[str, Any] = {
        "v": STATE_VERSION,
        "r": rev,
        "st": status,
        "n": recipients,
        "ok": ok,
        "fail": fail,
        "tx": preview,
    }
    if len(preview) < len(text):
        state["more"] = len(text) - len(preview)
    while _size(state) > BROADCAST_BUDGET_BYTES and preview:
        preview = _cut_words(preview, BROADCAST_CUT_CHARS)
        state["tx"] = preview
        state["more"] = len(text) - len(preview)
    dumps_state(state)
    return state


def recipients_dative(count: int) -> str:
    word = "пользователю" if count % 10 == 1 and count % 100 != 11 else "пользователям"
    return f"{count} {word}"


def broadcast_caption(recipients: int) -> str:
    return f"📢 Рассылка {recipients_dative(recipients)} — подтвердите в виджете выше."


def broadcast_done_caption(ok: int, recipients: int) -> str:
    return f"📢 Доставлено {ok} из {recipients}"
