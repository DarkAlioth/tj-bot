import datetime
import json
from typing import Any

from tj_bot.db.filters import ResultFilters
from tj_bot.db.models import Torrent
from tj_bot.services.cards import (
    DROP_ORDER,
    THIN_SPACE,
    SearchContext,
    category_view,
    cut,
    done_state,
    filters_caption,
    filters_done_text,
    filters_state,
    fmt_int,
    fmt_size,
    list_caption,
    list_state,
    more_answer,
    pack,
    pack_list,
    plural,
    quality_chips,
    relative_day,
    search_state,
    split_title,
    state_size,
    title_year,
    tracker_name,
)
from tj_bot.services.enrichment import CardMeta
from tj_bot.widgets.state import MAX_STATE_BYTES, dumps_state

GB = 1024**3
TODAY = datetime.date(2024, 3, 10)
QH = "a1b2c3d4" + "0" * 24
HASH = "8f3a1c2b" + "f" * 24
TITLE = (
    "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p | 4K | HDR10 "
    "| Dolby Vision | D, P, A"
)


def make_torrent(**overrides: Any) -> Torrent:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "id": 1,
        "hash": HASH,
        "title": TITLE,
        "uploader": "CEBEPu2D",
        "description": None,
        "category": "Movies/UHD",
        "tracker": "rutracker",
        "details_url": "https://rutracker.org/forum/viewtopic.php?t=6512345",
        "download_url": "http://jackett:9117/dl/1",
        "seeders": 1543,
        "peers": 120,
        "published_at": datetime.date(2024, 3, 1),
        "size": 45 * GB,
    }
    values.update(overrides)
    return Torrent(**values)


FULL_META = CardMeta(
    genres=("фантастика", "драма", "приключения", "боевик"),
    countries=("США",),
    duration_min=166,
    director="Дени Вильнёв",
    cast=("Тимоти Шаламе", "Зендея", "Ребекка Фергюсон", "Хавьер Бардем", "X"),
    translation=("Дубляж", "MVO"),
    subtitles=("рус", "англ"),
    plot=(
        "Пол Атрейдес объединяется с Чани и фрименами, чтобы отомстить "
        "заговорщикам, уничтожившим его семью. Стоя перед выбором между любовью "
        "всей жизни и судьбой вселенной, он должен предотвратить ужасное будущее."
    ),
)
CTX = SearchContext(qh=QH, index=11, total=302, from_cache=False, is_admin=False)


def test_search_state_full_meta_follows_the_protocol() -> None:
    state = search_state(
        make_torrent(), CTX, FULL_META, rev=7, is_fav=True, today=TODAY
    )

    assert state["v"] == 1 and state["r"] == 7
    assert state["qh"] == "a1b2c3d4" and state["h"] == "8f3a1c2b"
    assert (state["i"], state["n"]) == (11, 302)
    assert (state["s"], state["sl"], state["f"], state["c"]) == (
        "se",
        "Сиды ↓",
        "000",
        "ALL",
    )
    assert (state["fa"], state["fc"], state["adm"]) == (0, 0, 0)
    assert state["tr"] == "Дюна: Часть вторая" and state["te"] == "Dune: Part Two"
    assert state["y"] == 2024
    assert state["q"] == ["2160p", "HDR10", "BDRemux"]
    assert state["cn"] == "Фильмы 4K"
    assert state["tk"] == "RuTracker"
    assert (state["sd"], state["pr"]) == (1543, 120)
    assert state["sz"] == "45 ГБ" and state["dt"] == "01.03.2024"
    assert state["dr"] == "9 дн. назад"
    assert state["up"] == "CEBEPu2D"
    assert state["ml"] == "фантастика, драма, приключения · США · 2 ч 46 мин"
    assert state["pp"] == "Реж. Дени Вильнёв · Тимоти Шаламе, Зендея"
    assert state["aa"] == "Дубляж, MVO · Субтитры: рус, англ"
    assert len(state["pl"]) <= 80 and state["pl"].endswith("…")
    assert state["pm"] == 1
    assert state["u"] == "https://rutracker.org/forum/viewtopic.php?t=6512345"
    assert state["fav"] == 1
    assert "en" not in state
    assert state_size(state) <= MAX_STATE_BYTES
    dumps_state(state)


def test_search_state_fallbacks_without_meta() -> None:
    torrent = make_torrent(
        title="Dune: Awakening [v 1.0.2.2 + DLCs] (2025) PC | RePack от FitGirl",
        category="PC/Games",
        tracker="byrutor",
        uploader=None,
        details_url="http://byrutor.org/" + "x" * 60,
        published_at=datetime.date(2023, 1, 1),
        seeders=0,
    )
    ctx = SearchContext(
        qh=QH, index=0, total=19, filter_code="120", category="9f1c2b3a",
        from_cache=True, is_admin=True,
    )  # fmt: skip

    state = search_state(torrent, ctx, CardMeta(), rev=1, is_fav=False, today=TODAY)

    assert state["ti"].startswith("Dune: Awakening")
    assert "tr" not in state and "te" not in state
    assert state["y"] == 2025
    assert "q" not in state
    assert state["cn"] == "Игры"
    assert state["tk"] == "Byrutor"
    assert (state["fa"], state["fc"], state["adm"]) == (1, 1, 1)
    assert "dr" not in state and "up" not in state and "u" not in state
    assert not {"ml", "pp", "aa", "pl", "pm", "fav", "en"} & state.keys()


def test_search_state_title_split_and_pending() -> None:
    torrent = make_torrent(
        title="Dune: Prophecy / Дюна: Пророчество (2024) [S1E1-6 of 6, WEB-DL 1080p] "
        "6 x MVO (LostFilm) &amp; Sub",
        category="TV/HD",
        tracker="NoNaMe Club",
    )

    state = search_state(
        torrent, CTX, CardMeta(pending=True), rev=2, is_fav=False, today=TODAY
    )

    assert state["tr"] == "Дюна: Пророчество" and state["te"] == "Dune: Prophecy"
    assert state["q"] == ["1080p", "WEB-DL", "S1E1-6", "MVO"]
    assert state["cn"] == "Сериалы HD"
    assert state["tk"] == "NoNaMe"
    assert state["en"] == 1


def test_search_state_meta_titles_override_the_split() -> None:
    meta = CardMeta(title_ru="Дюна 2", title_en="Dune Two", year=2025, chips=("4K",))

    state = search_state(make_torrent(), CTX, meta, rev=1, is_fav=False, today=TODAY)

    assert (state["tr"], state["te"], state["y"], state["q"]) == (
        "Дюна 2",
        "Dune Two",
        2025,
        ["4K"],
    )


def worst_case_state() -> dict[str, Any]:
    return {
        "v": 1, "r": 999, "qh": "a1b2c3d4", "i": 300, "n": 999, "s": "se",
        "sl": "Размер ↓", "f": "333", "c": "9f1c2b3a", "fa": 1, "fc": 1, "adm": 1,
        "h": "8f3a1c2b", "ti": "ж" * 120, "y": 2024, "q": ["ж" * 12] * 5,
        "cn": "Сериалы HD", "hu": 178, "tk": "PC-torrent", "sd": 99999, "pr": 99999,
        "sz": "999,9 МБ", "dt": "01.03.2024", "dr": "30 дн. назад", "up": "ж" * 16,
        "ml": "ж" * 60, "pp": "ж" * 60, "aa": "ж" * 50, "pl": "ж" * 80, "pm": 1,
        "u": "https://" + "x" * 56, "fav": 1, "en": 1, "m": "a" * 72,
    }  # fmt: skip


def test_pack_drops_least_valuable_keys_first_and_keeps_more_reachable() -> None:
    state = worst_case_state()
    assert state_size(state) > MAX_STATE_BYTES

    packed = pack(dict(state))

    assert state_size(packed) <= MAX_STATE_BYTES
    dropped = [key for key in DROP_ORDER if key in state and key not in packed]
    assert dropped == list(DROP_ORDER[: len(dropped)])
    assert "pl" in dropped and packed["pm"] == 1
    assert packed["ti"] == "ж" * 120 and packed["tk"] == "PC-torrent"


def test_pack_leaves_a_fitting_state_untouched() -> None:
    state = {"v": 1, "r": 1, "pl": "x", "m": "y"}

    assert pack(dict(state)) == state


def test_more_answer_caps_plot_and_people() -> None:
    meta = CardMeta(
        plot="слово " * 100,
        director="Кристофер Нолан",
        cast=("Киллиан Мёрфи", "Эмили Блант", "Мэтт Деймон", "Роберт Дауни", "Х"),
    )

    answer = more_answer(meta)

    assert len(answer["pl"]) <= 360 and answer["pl"].endswith("…")
    assert answer["pp"] == (
        "Реж. Кристофер Нолан · Киллиан Мёрфи, Эмили Блант, Мэтт Деймон, Роберт Дауни"
    )
    assert len(json.dumps(answer, ensure_ascii=False).encode()) <= 1024
    assert more_answer(CardMeta()) == {"err": "Описание недоступно"}


def test_fmt_size_units_and_decimals() -> None:
    assert fmt_size(45 * GB) == "45 ГБ"
    assert fmt_size(int(8.2 * GB)) == "8,2 ГБ"
    assert fmt_size(int(14.2 * GB)) == "14,2 ГБ"
    assert fmt_size(912 * 1024**2) == "912 МБ"
    assert fmt_size(500) == "500 Б"
    assert fmt_size(3 * 1024) == "3 КБ"
    assert fmt_size(2 * 1024**4) == "2 ТБ"


def test_fmt_int_plural_and_cut() -> None:
    assert fmt_int(1543) == f"1{THIN_SPACE}543"
    assert fmt_int(12) == "12"
    assert [plural(n, "сид", "сида", "сидов") for n in (1, 2, 5, 11, 21, 112)] == [
        "сид", "сида", "сидов", "сидов", "сид", "сидов",
    ]  # fmt: skip
    assert cut("short", 10) == "short"
    assert cut("Дюна: Часть вторая (саундтрек)", 20) == "Дюна: Часть вторая…"
    assert cut("ж" * 30, 10) == "ж" * 9 + "…"


def test_relative_day_window() -> None:
    assert relative_day(TODAY, TODAY) == "сегодня"
    assert relative_day(TODAY - datetime.timedelta(days=1), TODAY) == "вчера"
    assert relative_day(TODAY - datetime.timedelta(days=30), TODAY) == "30 дн. назад"
    assert relative_day(TODAY - datetime.timedelta(days=31), TODAY) is None
    assert relative_day(TODAY + datetime.timedelta(days=1), TODAY) is None


def test_tracker_names_from_ids_and_display_names() -> None:
    assert tracker_name("rutracker") == "RuTracker"
    assert tracker_name("RuTracker.org") == "RuTracker"
    assert tracker_name("NoNaMe Club") == "NoNaMe"
    assert tracker_name("noname-club") == "NoNaMe"
    assert tracker_name("nnmclub") == "NoNaMe"
    assert tracker_name("PC-torrent") == "PC-torrent"
    assert tracker_name("pctorrent") == "PC-torrent"
    assert [tracker_name(t) for t in ("RuTor", "MegaPeer", "Tapochek", "Byrutor")] == [
        "RuTor", "MegaPeer", "Tapochek", "Byrutor",
    ]  # fmt: skip
    assert tracker_name("Some Unknown Tracker &amp; Co") == "Some Unknown…"


def test_category_view_map() -> None:
    assert category_view("Movies/HD") == ("Фильмы HD", 262)
    assert category_view("Movies") == ("Фильмы", 262)
    assert category_view("TV/UHD") == ("Сериалы 4K", 178)
    assert category_view("PC/Games") == ("Игры", 36)
    assert category_view("Audio/MP3") == ("Музыка", 322)
    assert category_view("Books/EBook") == ("Книги", 82)
    assert category_view("PC/Mac") == ("PC/Mac", None)


def test_title_helpers() -> None:
    assert split_title("Дюна / Dune (2024) BDRemux") == ("Дюна", "Dune")
    assert split_title("Dune / Дюна [2024]") == ("Дюна", "Dune")
    assert split_title("Dune (2024)") == (None, None)
    assert title_year("Dune [1984] x264") == 1984
    assert title_year("Dune 2024") is None
    assert quality_chips("Film 1080p HDR10+ WEB-DL HEVC S01E05 MVO Sub") == [
        "1080p", "HDR10+", "WEB-DL", "HEVC", "S01E05", "MVO",
    ]  # fmt: skip


def list_torrents(count: int, title: str = TITLE) -> list[Torrent]:
    return [
        make_torrent(id=i, hash=f"{i:08x}" + "0" * 24, title=title, seeders=1543 - i)
        for i in range(count)
    ]


def pathological_list(seeders: str) -> dict[str, Any]:
    rows = [["ж" * 36, f"999,9 МБ|01.03.24|{seeders}|PC-torrent"] for _ in range(10)]
    return {
        "v": 1,
        "r": 5,
        "qh": "a1b2c3d4",
        "q": "ж" * 24,
        "i0": 290,
        "n": 999,
        "s": "se",
        "sl": "Размер ↓",
        "f": "333",
        "c": "9f1c2b3a",
        "fa": 1,
        "rows": rows,
    }


def test_pack_list_shrinks_strings_in_order_and_never_rows() -> None:
    state = pathological_list("9 999")
    assert state_size(state) > MAX_STATE_BYTES

    packed = pack_list(state)

    # fits once the query text, 28-char titles and the tracker are gone
    assert state_size(packed) <= MAX_STATE_BYTES
    assert len(packed["rows"]) == 10
    assert "q" not in packed
    assert all(len(row[0]) == 28 and row[1].count("|") == 2 for row in packed["rows"])

    heavier = pack_list(pathological_list("999 999"))

    assert state_size(heavier) <= MAX_STATE_BYTES
    assert all(len(row[0]) == 20 for row in heavier["rows"])


def test_pack_list_leaves_realistic_pages_alone() -> None:
    ctx = SearchContext(qh=QH, index=0, total=302)

    state = list_state(list_torrents(10), ctx, rev=1, query_text="дюна")

    assert state["q"] == "дюна"
    assert state["rows"][0][1].count("|") == 3


def test_done_state_with_context_line_and_cap() -> None:
    state = done_state(QH, 9, "Открыта карточка 7", q="дюна", sl="Сиды ↓")

    assert state == {
        "v": 1, "r": 9, "qh": "a1b2c3d4", "q": "дюна", "sl": "Сиды ↓",
        "done": "Открыта карточка 7",
    }  # fmt: skip
    assert len(done_state(QH, 1, "слово " * 30)["done"]) <= 80


def test_filters_state_categories_capped_sorted_and_optional() -> None:
    ctx = SearchContext(qh=QH, index=11, total=302, filter_code="120", category="x" * 8)
    categories = [(f"Movies/Cat{i} with a very long name", 100 - i) for i in range(12)]
    categories.append(("TV/HD", 500))

    state = filters_state(ctx, rev=3, categories=categories)

    assert (state["i"], state["s"], state["f"], state["c"], state["n"]) == (
        11, "se", "120", "x" * 8, 302,
    )  # fmt: skip
    assert len(state["cats"]) == 10
    assert state["cats"][0] == ["ALL", "Все", 302]
    assert state["cats"][1][1:] == ["Сериалы HD", 500]
    assert all(len(label) <= 18 for _, label, _ in state["cats"])
    assert all(len(token) == 8 for token, _, _ in state["cats"][1:])
    assert state_size(state) <= MAX_STATE_BYTES
    assert "cats" not in filters_state(ctx, rev=3, categories=[("Movies", 9)])


def test_filters_texts() -> None:
    filters = ResultFilters.from_code("120")

    assert filters_done_text(filters, "Movies/HD", 48) == (
        "Применено: сиды 1+ · 1–5 ГБ · Фильмы HD · 48 результатов"
    )
    assert filters_done_text(ResultFilters(), None, 302) == (
        "Применено: фильтры сброшены · 302 результата"
    )
    assert filters_done_text(ResultFilters(date=3), None, 1) == (
        "Применено: за год · 1 результат"
    )
    assert filters_caption("дюна", ResultFilters(), None) == (
        "Фильтры поиска «дюна» · Активно: не заданы"
    )
    assert filters_caption(None, ResultFilters(seeders=3), "TV") == (
        "Фильтры поиска «» · Активно: сиды 50+ · Сериалы"
    )


def test_list_state_rows_meta_and_caption() -> None:
    ctx = SearchContext(qh=QH, index=10, total=302, filter_code="100")

    state = list_state(list_torrents(10), ctx, rev=8, query_text="дюна" * 10)

    assert (state["v"], state["r"], state["qh"]) == (1, 8, "a1b2c3d4")
    assert state["q"] == "дюнадюнадюнадюнадюнадюн…"
    assert (state["i0"], state["n"], state["s"], state["sl"]) == (
        10, 302, "se", "Сиды ↓",
    )  # fmt: skip
    assert state["f"] == "100" and state["fa"] == 1
    assert len(state["rows"]) == 10
    assert state["rows"][0] == [
        "Дюна: Часть вторая",
        f"45 ГБ|01.03.24|1{THIN_SPACE}543|RuTracker",
    ]
    assert state_size(state) <= MAX_STATE_BYTES
    assert list_caption(state, "дюна") == (
        "Результаты 11–20 из 302 по запросу «дюна» · Сиды ↓"
    )
    assert list_caption({"i0": 0, "rows": [], "n": 0, "sl": "Сиды ↓"}, None) == (
        "Результаты 1–0 из 0 · Сиды ↓"
    )
