import datetime
import json
from typing import Any

import pytest

from tj_bot.db.models import BotUser
from tj_bot.db.repo import SearchStats
from tj_bot.services.admin_views import (
    FLAG_ADMIN,
    FLAG_BLOCKED,
    FLAG_SUPER,
    INDEXER_ERROR_FIT,
    TOP_QUERIES_FIT,
    USER_NAME_FIT,
    Health,
    QbitSummary,
    UserRow,
    activity_answer,
    broadcast_caption,
    broadcast_done_caption,
    broadcast_state,
    dashboard_caption,
    dashboard_state,
    dashboard_tick,
    format_duration,
    health_doc,
    indexer_rows,
    recipients_dative,
    user_row,
    users_caption,
    users_state,
    with_toast,
)
from tj_bot.services.monitor import JACKETT_PROBLEM, IndexerStatus, indexer_problem
from tj_bot.widgets.state import MAX_STATE_BYTES

NAMES = [
    "RuTracker",
    "RuTor",
    "MegaPeer",
    "NoNaMe Club",
    "Tapochek",
    "PC-torrent",
    "Byrutor",
]
NOW = 1_794_487_200
AT = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=datetime.UTC)


def size(document: dict[str, Any]) -> int:
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return len(raw.encode("utf-8"))


def indexers(error: str | None = None, results: int = 100) -> list[IndexerStatus]:
    return [
        IndexerStatus(
            id=name.lower(), name=name, ok=error is None, error=error, results=results
        )
        for name in NAMES
    ]


def health(qbit: QbitSummary | None = None) -> Health:
    return Health(
        uptime=312_840,
        disk=(41_000_000_000, 53_000_000_000),
        db_size=123_400_000,
        qbit=qbit,
        checked_at=NOW,
    )


QBIT = QbitSummary(
    active=2, total=14, dl_speed=1_250_000, up_speed=234_000, free=1_230_000_000_000
)


def stats(top: list[tuple[str, int]]) -> SearchStats:
    return SearchStats(
        torrents=4213, queries=812, events_window=169, users_window=5, top_queries=top
    )


def test_format_duration_picks_the_two_largest_units() -> None:
    assert format_duration(312_840) == "3д 14ч"
    assert format_duration(5_000) == "1ч 23м"
    assert format_duration(120) == "2м"
    assert format_duration(-5) == "0м"


def test_health_doc_has_qbit_summary() -> None:
    doc = health_doc(health(QBIT))

    assert doc == {
        "up": 312_840,
        "df": [41_000_000_000, 53_000_000_000],
        "db": 123_400_000,
        "q": {"a": 2, "n": 14, "d": 1_250_000, "u": 234_000, "f": [QBIT.free, 0]},
        "m": NOW,
    }
    assert health_doc(health(None))["q"] is None


def test_health_doc_clamps_unknown_free_space() -> None:
    unknown = QbitSummary(active=0, total=0, dl_speed=0, up_speed=0, free=-1)

    assert health_doc(health(unknown))["q"]["f"] == [0, 0]


def test_indexer_rows_reflect_snapshot_and_current_problems() -> None:
    snapshot = indexers()
    down = {"indexer:rutor": indexer_problem("RuTor")}

    rows = indexer_rows(snapshot, down)

    assert rows[0] == ["RuTracker", 100]
    assert rows[1] == ["RuTor", 0, "не отвечает"]
    assert indexer_rows(snapshot, {"jackett": JACKETT_PROBLEM})[3] == [
        "NoNaMe Club",
        0,
        "Jackett недоступен",
    ]


def test_indexer_rows_cap_names_and_errors() -> None:
    long_name = IndexerStatus(
        id="x", name="A very long indexer name", ok=False, error="e" * 60, results=0
    )

    (row,) = indexer_rows([long_name], {"indexer:x": indexer_problem("x")})

    assert row == ["A very long in", 0, "e" * 40]
    (short,) = indexer_rows([long_name], {"indexer:x": indexer_problem("x")}, 24)
    assert short[2] == "e" * 24


def test_dashboard_state_matches_the_protocol() -> None:
    top = [("дюна часть вторая", 14), ("оппенгеймер " * 5, 9)]

    state = dashboard_state(
        rev=12,
        now=NOW,
        tab="h",
        health=health(QBIT),
        stats=stats(top),
        per_day=[12, 30, 22, 45, 8, 19, 33],
        indexers=indexers(),
        problems={},
    )

    assert state["v"] == 1 and state["r"] == 12 and state["t"] == NOW
    assert state["a"] == "h"
    assert state["s"]["d7"] == [12, 30, 22, 45, 8, 19, 33]
    assert state["s"]["ev"] == 169 and state["s"]["us"] == 5
    assert state["s"]["tc"] == 4213 and state["s"]["qc"] == 812
    assert state["s"]["top"][0] == ["дюна часть вторая", 14]
    assert len(state["s"]["top"][1][0]) == 24
    assert state["i"][0] == ["RuTracker", 100]
    assert size(state) <= MAX_STATE_BYTES


def test_dashboard_state_rejects_unknown_tab() -> None:
    with pytest.raises(ValueError, match="tab"):
        dashboard_state(
            rev=1,
            now=NOW,
            tab="x",
            health=health(),
            stats=stats([]),
            per_day=[0] * 7,
            indexers=[],
            problems={},
        )


def test_dashboard_fit_trims_errors_then_top_rows() -> None:
    broken = indexers(error="Login failed: captcha required, see Jackett")
    problems = {f"indexer:{ix.id}": indexer_problem(ix.name) for ix in broken}
    top = [("ж" * 24, 10_000 - i) for i in range(8)]

    state = dashboard_state(
        rev=1,
        now=NOW,
        tab="i",
        health=health(QBIT),
        stats=stats(top),
        per_day=[1000] * 7,
        indexers=broken,
        problems=problems,
    )

    assert size(state) <= MAX_STATE_BYTES
    assert all(len(row[2]) <= INDEXER_ERROR_FIT for row in state["i"])
    assert TOP_QUERIES_FIT <= len(state["s"]["top"]) < 8


def test_dashboard_fit_keeps_top_when_short_errors_suffice() -> None:
    broken = indexers(error="x" * 40)
    problems = {f"indexer:{ix.id}": indexer_problem(ix.name) for ix in broken}
    top = [("abcdef", 1)] * 8

    state = dashboard_state(
        rev=1,
        now=NOW,
        tab="s",
        health=health(QBIT),
        stats=stats(top),
        per_day=[0] * 7,
        indexers=broken,
        problems=problems,
    )

    assert len(state["s"]["top"]) == 8
    assert size(state) <= MAX_STATE_BYTES


def test_dashboard_tick_carries_rows_only_when_given() -> None:
    assert set(dashboard_tick(3, NOW, health())) == {"r", "t", "h"}
    answer = dashboard_tick(3, NOW, health(), [["RuTor", 0, "e"]])
    assert answer["i"] == [["RuTor", 0, "e"]]
    assert size(answer) <= 1024


def test_dashboard_caption_summarizes_both_tabs() -> None:
    caption = dashboard_caption(
        health(QBIT), stats([]), indexers(), {"indexer:rutor": indexer_problem("RuTor")}
    )

    assert "аптайм 3д 14ч" in caption
    assert "qBittorrent 2/14 (1.2 MB/s↓ 228.5 KB/s↑)" in caption
    assert "индексеры 6/7" in caption
    assert caption.endswith("📊 7 дней: 169 поисков, 5 пользователей")
    assert "qBittorrent недоступен" in dashboard_caption(health(), stats([]), [], {})


# --- users -----------------------------------------------------------------


def make_user(
    uid: int = 100,
    username: str | None = "alice",
    full_name: str | None = "Alice",
    blocked: bool = False,
    admin: bool = False,
) -> BotUser:
    return BotUser(
        user_id=uid,
        username=username,
        full_name=full_name,
        blocked=blocked,
        admin=admin,
        first_seen=AT - datetime.timedelta(days=30),
        last_seen=AT,
    )


def test_user_row_flags_names_and_unix_minutes() -> None:
    row = user_row(
        make_user(admin=True, blocked=True), is_super=True, searches=4, downloads=1
    )

    assert row.flags == FLAG_ADMIN | FLAG_SUPER | FLAG_BLOCKED
    assert row.last_seen == int(AT.timestamp()) // 60
    assert row.first_seen == row.last_seen - 30 * 24 * 60
    assert (row.searches, row.downloads) == (4, 1)
    assert user_row(make_user(), False, 0, 0).flags == 0


def test_user_row_falls_back_to_username_then_id_and_caps_lengths() -> None:
    assert user_row(make_user(full_name=None), False, 0, 0).name == "alice"
    assert user_row(make_user(full_name=None, username=None), False, 0, 0).name == "100"
    long = make_user(username="u" * 30, full_name="и" * 40)
    row = user_row(long, False, 0, 0)
    assert row.name == "и" * 24
    assert row.username == "u" * 16


def rows(count: int, name_chars: int = 24, count_base: int = 10_000) -> list[UserRow]:
    return [
        UserRow(
            user_id=100_000_000_000 + i,
            name="ж" * name_chars,
            username="u" * 16,
            flags=5,
            last_seen=29_908_293,
            first_seen=29_332_296,
            searches=count_base + i,
            downloads=2 * count_base + i,
        )
        for i in range(count)
    ]


def test_users_state_shape_and_worst_case_fit() -> None:
    state = users_state(
        rev=5,
        now=NOW,
        group="a",
        page=0,
        pages=3,
        counts=[2300, 1900, 300, 100],
        rows=rows(8),
    )

    assert (state["v"], state["r"], state["t"]) == (1, 5, NOW)
    assert (state["g"], state["p"], state["n"]) == ("a", 0, 3)
    assert state["c"] == [2300, 1900, 300, 100]
    assert len(state["u"]) == 8
    assert state["u"][0][:4] == [100_000_000_000, "ж" * 24, "u" * 16, 5]
    assert size(state) <= MAX_STATE_BYTES


def test_users_state_fit_cuts_names_to_20_when_over() -> None:
    state = users_state(
        rev=5,
        now=NOW,
        group="a",
        page=0,
        pages=3,
        counts=[23_000, 19_000, 3_000, 1_000],
        rows=rows(8, 24, 100_000),
    )

    assert all(len(row[1]) <= USER_NAME_FIT for row in state["u"])
    assert size(state) <= MAX_STATE_BYTES


def test_users_state_rejects_unknown_group() -> None:
    with pytest.raises(ValueError, match="group"):
        users_state(rev=1, now=NOW, group="z", page=0, pages=1, counts=[0] * 4, rows=[])


def test_with_toast_adds_m_unless_it_overflows() -> None:
    small = {"v": 1, "r": 2}
    assert with_toast(small, "Готово") == {"v": 1, "r": 2, "m": "Готово"}
    assert with_toast(small, None) is small
    full = {"x": "ж" * 505}
    assert with_toast(full, "Заблокирован") is full


def test_activity_answer_caps_rows_and_maps_kinds() -> None:
    searches = [(f"запрос {i} " + "д" * 40, AT) for i in range(7)]
    downloads = [("Дюна " * 20, "server", AT), ("Оппенгеймер", "chat", AT)]

    answer = activity_answer(searches, downloads)

    assert answer["ok"] == 1
    assert len(answer["a"]["s"]) == 5
    assert all(len(text) <= 40 for text, _ in answer["a"]["s"])
    assert answer["a"]["s"][0][1] == int(AT.timestamp()) // 60
    assert answer["a"]["d"][0][2] == "s" and answer["a"]["d"][1][2] == "c"
    assert len(answer["a"]["d"][0][0]) == 40
    assert size(answer) <= 1024


def test_users_caption_lists_counts_and_page() -> None:
    assert users_caption([23, 19, 3, 1], 0, 1) == (
        "👥 Пользователи: всего 23 · обычных 19 · админов 3 · в блоке 1"
    )
    assert users_caption([23, 19, 3, 1], 1, 3).endswith(" · стр. 2/3")


# --- broadcast -------------------------------------------------------------


def test_broadcast_state_keeps_short_text_verbatim() -> None:
    text = "Привет!\n\nСервер <b>обновляется</b> в 02:00."

    state = broadcast_state(
        rev=1, status="draft", recipients=23, ok=0, fail=0, text=text
    )

    assert state == {
        "v": 1,
        "r": 1,
        "st": "draft",
        "n": 23,
        "ok": 0,
        "fail": 0,
        "tx": text,
    }


def test_broadcast_state_cuts_long_cyrillic_text_at_a_word_boundary() -> None:
    text = "Друзья, длинное объявление. " * 40

    state = broadcast_state(rev=2, status="run", recipients=1, ok=0, fail=0, text=text)

    assert size(state) <= 1000
    assert state["more"] == len(text) - len(state["tx"])
    assert text[len(state["tx"])] == " "  # cut right after a whole word
    assert not state["tx"].endswith(" ")
    assert text.startswith(state["tx"])


def test_broadcast_state_marks_the_600_char_cut() -> None:
    text = "a" * 700

    state = broadcast_state(rev=3, status="done", recipients=5, ok=4, fail=1, text=text)

    assert len(state["tx"]) == 600
    assert state["more"] == 100
    assert (state["ok"], state["fail"], state["st"]) == (4, 1, "done")


def test_broadcast_captions_and_dative_plural() -> None:
    assert recipients_dative(1) == "1 пользователю"
    assert recipients_dative(11) == "11 пользователям"
    assert recipients_dative(21) == "21 пользователю"
    assert recipients_dative(23) == "23 пользователям"
    assert broadcast_caption(23) == (
        "📢 Рассылка 23 пользователям — подтвердите в виджете выше."
    )
    assert broadcast_done_caption(21, 23) == "📢 Доставлено 21 из 23"
