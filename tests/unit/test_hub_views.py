import datetime
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from tj_bot.db.repo import HistoryEntry
from tj_bot.services.formatting import format_size_ru, group_thousands
from tj_bot.services.hub_views import (
    ADMIN_CAPTION,
    CAPTION,
    HubStatus,
    clamp_page,
    cut,
    cut_title,
    details_link,
    favorite_row,
    favorites_pages,
    history_age,
    history_row,
    hub_caption,
    hub_state,
    state_bytes,
    tracker_name,
)
from tj_bot.widgets.state import StateTooLargeError, dumps_state

GB = 1024**3
MSK = datetime.timezone(datetime.timedelta(hours=3))
NOW = datetime.datetime(2026, 11, 12, 12, 40, tzinfo=MSK)


def make_favorite(
    fav_id: int = 501,
    title: str = "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p",
    tracker: str = "rutracker",
    updated_at: datetime.datetime | None = None,
    seen_at: datetime.datetime | None = None,
    details_url: str = "https://rutracker.org/forum/viewtopic.php?t=6543210",
) -> MagicMock:
    favorite = MagicMock()
    favorite.id = fav_id
    favorite.title = title
    favorite.tracker = tracker
    favorite.size = int(58.4 * GB)
    favorite.published_at = datetime.date(2026, 11, 2)
    favorite.seeders = 1240
    favorite.updated_at = updated_at
    favorite.seen_at = seen_at
    favorite.details_url = details_url
    return favorite


def make_entry(
    query_id: int = 1041,
    text: str = "дюна часть вторая",
    minutes_ago: int = 3,
    result_count: int = 57,
) -> HistoryEntry:
    created = NOW - datetime.timedelta(minutes=minutes_ago)
    return HistoryEntry(query_id, text, created.astimezone(datetime.UTC), result_count)


def size(state: dict[str, Any]) -> int:
    return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())


# --- formatting -----------------------------------------------------------


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (0, "0 Б"),
        (900, "900 Б"),
        (312 * 1024**2, "312 МБ"),
        (int(4.8 * GB), "4,8 ГБ"),
        (31 * GB, "31,0 ГБ"),
        (int(1.5 * 1024**4), "1,5 ТБ"),
    ],
)
def test_format_size_ru(num_bytes: int, expected: str) -> None:
    assert format_size_ru(num_bytes) == expected


def test_group_thousands() -> None:
    assert group_thousands(64) == "64"
    assert group_thousands(1240) == "1 240"
    assert group_thousands(1234567) == "1 234 567"


@pytest.mark.parametrize(
    ("tracker", "expected"),
    [
        ("rutracker", "RuTracker"),
        ("rutor", "RuTor"),
        ("megapeer", "MegaPeer"),
        ("noname-club", "NoNaMe"),
        ("nnmclub", "NoNaMe"),
        ("tapochek", "Tapochek"),
        ("pctorrent", "PC-torrent"),
        ("PC-torrent", "PC-torrent"),
        ("byrutor", "Byrutor"),
        ("some-very-long-indexer", "some-very-lo"),
        ("", ""),
    ],
)
def test_tracker_name(tracker: str, expected: str) -> None:
    assert tracker_name(tracker) == expected


def test_cut_keeps_short_and_ellipsizes_long() -> None:
    assert cut("silo", 40) == "silo"
    assert cut("a" * 40, 40) == "a" * 40
    assert cut("a" * 41, 40) == "a" * 39 + "…"


def test_cut_title_unescapes_and_caps_chars() -> None:
    assert cut_title("Tom &amp; Jerry") == "Tom & Jerry"
    long = "Baldur's Gate 3 [v4.1.1.5022896 + DLC] (2023) RePack от Chovka xxx"
    assert len(long) > 60
    result = cut_title(long)
    assert len(result) <= 60 and result.endswith("…")
    assert result.startswith("Baldur's Gate 3")


def test_cut_title_caps_bytes_for_cyrillic() -> None:
    title = "Ж" * 60  # 60 chars but 120 bytes
    result = cut_title(title)
    assert len(result.encode()) <= 90
    assert result.endswith("…")
    assert len(result) == 44  # 43 × 2 bytes + 3-byte ellipsis = 89


def test_cut_title_strips_a_trailing_space_before_the_ellipsis() -> None:
    title = "word " * 13  # 65 chars; cut lands after a space
    assert not cut_title(title).endswith(" …")


# --- history rows ---------------------------------------------------------


def test_history_age_minutes_same_day_and_older() -> None:
    assert history_age(NOW - datetime.timedelta(minutes=3), NOW) == 3
    assert history_age(NOW, NOW) == 0
    assert history_age(NOW + datetime.timedelta(seconds=30), NOW) == 0  # skew
    assert history_age(NOW - datetime.timedelta(minutes=59), NOW) == 59
    assert history_age(NOW - datetime.timedelta(hours=2, minutes=20), NOW) == "10:20"
    assert history_age(NOW - datetime.timedelta(days=1), NOW) == "11.11"


def test_history_age_converts_utc_to_the_viewers_local_day() -> None:
    # 00:30 MSK is 21:30 UTC the day before: same local day as NOW
    created = datetime.datetime(2026, 11, 11, 21, 30, tzinfo=datetime.UTC)
    assert history_age(created, NOW) == "00:30"


def test_history_row_cuts_the_query() -> None:
    row = history_row(
        make_entry(text="фоллаут 1 сезон 4k hdr dolby vision remux x"), NOW
    )
    assert row == {
        "i": 1041,
        "q": "фоллаут 1 сезон 4k hdr dolby vision rem…",
        "a": 3,
        "n": 57,
    }


# --- favorite rows --------------------------------------------------------


def test_favorite_row_formats_meta_and_tracker() -> None:
    row = favorite_row(make_favorite())
    assert row == {
        "i": 501,
        "n": "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p",
        "t": "RuTracker",
        "m": "58,4 ГБ · 02.11.2026 · ⇡1 240",
    }


def test_favorite_row_updated_flag() -> None:
    updated = NOW
    assert "u" not in favorite_row(make_favorite(updated_at=None))
    assert favorite_row(make_favorite(updated_at=updated))["u"] == 1
    seen_before = updated - datetime.timedelta(hours=1)
    assert (
        favorite_row(make_favorite(updated_at=updated, seen_at=seen_before))["u"] == 1
    )
    assert "u" not in favorite_row(make_favorite(updated_at=updated, seen_at=updated))


def test_details_link_https_only_and_capped() -> None:
    assert details_link("https://rutracker.org/forum/viewtopic.php?t=1") == {
        "l": "https://rutracker.org/forum/viewtopic.php?t=1"
    }
    assert details_link("http://rutor.info/torrent/987654") == {}
    assert details_link("https://x.example/" + "a" * 256) == {}
    assert details_link("None") == {}
    assert details_link("https://x.example/?a=1&amp;b=2") == {
        "l": "https://x.example/?a=1&b=2"
    }


def test_paging_math() -> None:
    assert favorites_pages(0) == 1
    assert favorites_pages(4) == 1
    assert favorites_pages(5) == 2
    assert clamp_page(5, 7) == 1
    assert clamp_page(-1, 7) == 0
    assert clamp_page(0, 0) == 0


# --- state ----------------------------------------------------------------


def test_main_tab_state_matches_the_protocol_shape() -> None:
    state = hub_state(
        tab="m",
        rev=7,
        admin=False,
        favorites_total=7,
        favorites_updated=2,
        history=[make_entry(), make_entry(1037, "the bear s03", 140, 12)],
        now=NOW,
    )
    assert state == {
        "v": 1,
        "r": 7,
        "tb": "m",
        "fn": 7,
        "fu": 2,
        "h": [
            {"i": 1041, "q": "дюна часть вторая", "a": 3, "n": 57},
            {"i": 1037, "q": "the bear s03", "a": "10:20", "n": 12},
        ],
    }


def test_admin_main_tab_has_flag_and_status() -> None:
    status = HubStatus(jackett_ok=True, qbit_ok=False, at=NOW)
    state = hub_state(
        tab="m",
        rev=3,
        admin=True,
        favorites_total=0,
        favorites_updated=0,
        status=status,
        now=NOW,
    )
    assert state["adm"] == 1
    assert state["st"]["j"] == 1 and state["st"]["q"] == 0
    assert state["st"]["w"] == NOW.astimezone().strftime("%H:%M")
    # the status line belongs to the main tab only; adm stays everywhere
    history = hub_state(
        tab="h",
        rev=3,
        admin=True,
        favorites_total=0,
        favorites_updated=0,
        status=status,
        now=NOW,
    )
    assert history["adm"] == 1 and "st" not in history
    user = hub_state(
        tab="m",
        rev=3,
        admin=False,
        favorites_total=0,
        favorites_updated=0,
        status=status,
        now=NOW,
    )
    assert "adm" not in user and "st" not in user


def test_history_rows_capped_per_tab() -> None:
    entries = [make_entry(i, f"q{i}") for i in range(12)]
    main = hub_state(
        tab="m",
        rev=1,
        admin=False,
        favorites_total=0,
        favorites_updated=0,
        history=entries,
        now=NOW,
    )
    history = hub_state(
        tab="h",
        rev=1,
        admin=False,
        favorites_total=0,
        favorites_updated=0,
        history=entries,
        now=NOW,
    )
    assert len(main["h"]) == 5
    assert len(history["h"]) == 10
    assert [r["i"] for r in history["h"]] == list(range(10))


def test_history_drops_oldest_rows_past_the_budget() -> None:
    entries = [make_entry(1000 + i, "ж" * 40, 3000 + i, 100) for i in range(10)]
    state = hub_state(
        tab="h",
        rev=1,
        admin=True,
        favorites_total=99,
        favorites_updated=9,
        history=entries,
        now=NOW,
    )
    assert state_bytes(state) <= 1000
    assert 0 < len(state["h"]) < 10
    # newest first: the kept rows are the first ones
    assert [r["i"] for r in state["h"]] == [1000 + i for i in range(len(state["h"]))]
    dumps_state(state)


def test_favorites_tab_state_pages_and_fits() -> None:
    long_title = "Ж" * 60
    favorites = [
        make_favorite(500 + i, title=long_title, updated_at=NOW, tracker="tapochek")
        for i in range(6)
    ]
    for favorite in favorites:
        favorite.seeders = 999_999
        favorite.size = 999 * 1024**4
    state = hub_state(
        tab="f",
        rev=5,
        admin=True,
        favorites_total=6,
        favorites_updated=6,
        favorites=favorites,
        favorites_filter="u",
        favorites_page=1,
        status=HubStatus(True, True, NOW),
    )
    assert state["ff"] == "u" and state["fp"] == 1
    assert len(state["f"]) == 4  # the builder never ships more than a page
    assert "h" not in state and "st" not in state
    assert all(row["u"] == 1 for row in state["f"])
    assert size(state) <= 1024


def test_empty_states() -> None:
    for tab in ("m", "h"):
        state = hub_state(
            tab=tab, rev=1, admin=False, favorites_total=0, favorites_updated=0
        )
        assert state["h"] == []
    state = hub_state(
        tab="f", rev=1, admin=False, favorites_total=0, favorites_updated=0
    )
    assert state == {
        "v": 1,
        "r": 1,
        "tb": "f",
        "fn": 0,
        "fu": 0,
        "ff": "a",
        "fp": 0,
        "f": [],
    }


def test_state_over_the_hard_limit_raises() -> None:
    favorites = [make_favorite(i, title="Ж" * 60) for i in range(4)]
    for favorite in favorites:
        favorite.tracker = "Ж" * 12  # unknown tracker: shipped as is
    big = hub_state(
        tab="f",
        rev=1,
        admin=False,
        favorites_total=4,
        favorites_updated=0,
        favorites=favorites,
    )
    assert size(big) <= 1024
    with pytest.raises(StateTooLargeError):
        dumps_state({**big, "pad": "x" * 1024})


def test_caption() -> None:
    assert hub_caption(False) == CAPTION
    assert hub_caption(True) == f"{CAPTION}\n{ADMIN_CAPTION}"
    assert "/history" in CAPTION and "/favorites" in CAPTION
