import json
from typing import Any

import pytest

import tj_bot.services.qbit_views as views
from tj_bot.widgets.state import dumps_state

KB = 1024
MB = KB * 1024
GB = MB * 1024
HASH = "3f9a1c2b" + "0" * 32
NOW = 1_762_943_405


def size_of(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


def make_file(
    index: int, name: str, size: int = GB, priority: int = 1
) -> dict[str, Any]:
    return {"index": index, "name": name, "size": size, "priority": priority}


def season_files() -> list[dict[str, Any]]:
    files: list[tuple[str, str, int]] = []
    for e in range(1, 9):
        files.append(
            ("Season 2", f"House.of.the.Dragon.S02E{e:02d}.1080p.WEB-DL.mkv", 4 * GB)
        )
    for e in range(1, 9):
        files.append(("Season 2", f"House.of.the.Dragon.S02E{e:02d}.AC3.mka", 400 * MB))
    for e in range(1, 9):
        files.append(("Subs", f"House.of.the.Dragon.S02E{e:02d}.Rus.srt", 90 * KB))
    files.append(("Extras", "Poster.jpg", 2 * MB))
    files.append(("Extras", "Release.nfo", 9 * KB))
    return [
        make_file(i, f"Root/{folder}/{name}", size)
        for i, (folder, name, size) in enumerate(files)
    ]


def make_torrent(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401  # test helper
    base: dict[str, Any] = {
        "hash": HASH,
        "name": "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p",
        "state": "downloading",
        "progress": 0.3724,
        "size": 64_906_854_400,
        "total_size": 64_906_854_400,
        "downloaded": 24_145_349_836,
        "uploaded": 2_900_000_000,
        "dlspeed": 12_400_000,
        "upspeed": 1_200_000,
        "eta": 3290,
        "num_seeds": 12,
        "num_leechs": 48,
        "num_complete": 40,
        "num_incomplete": 15,
        "ratio": 0.1234,
        "category": "movies",
        "added_on": 1_762_939_805,
        "save_path": "/mnt/media/movies/",
        "force_start": False,
    }
    base.update(overrides)
    return base


# --- formatting ---------------------------------------------------------------


def test_russian_formatters() -> None:
    assert views.size_ru(0) == "0 Б"
    assert views.size_ru(12_400_000) == "11,8 МБ"
    assert views.size_ru(120 * GB) == "120 ГБ"
    assert views.speed_ru(12_400_000) == "11,8 МБ/с"
    assert views.eta_ru(-1) == "—"
    assert views.eta_ru(8_640_000) == "—"
    assert views.eta_ru(30) == "1 м"
    assert views.eta_ru(4320) == "1 ч 12 м"
    assert views.eta_ru(90_000) == "1 д 1 ч"
    assert views.cut("abc", 3) == "abc"
    assert views.cut("abcd", 3) == "ab…"


def test_plural_forms() -> None:
    forms = ("торрент", "торрента", "торрентов")
    assert [views.plural(n, *forms) for n in (1, 2, 5, 11, 21, 112)] == [
        "торрент",
        "торрента",
        "торрентов",
        "торрентов",
        "торрент",
        "торрентов",
    ]


# --- picker -------------------------------------------------------------------


def test_file_classes_by_extension() -> None:
    assert views.file_class("a.MKV") == "v"
    assert views.file_class("a.flac") == "a"
    assert views.file_class("a.srt") == "s"
    assert views.file_class("a.iso") == "r"
    assert views.file_class("README") == "o"
    assert views.file_class("a.png") == "o"


def test_common_prefix_stripping_rules() -> None:
    names = [f"House.of.the.Dragon.S02E0{e}.mkv" for e in (1, 2, 3)]
    assert views.strip_common_prefix(names) == [
        "S02E01.mkv",
        "S02E02.mkv",
        "S02E03.mkv",
    ]
    # fewer than 3 files, a short prefix or a prefix swallowing a name: untouched
    assert views.strip_common_prefix(names[:2]) == names[:2]
    short = ["a.01.mkv", "a.02.mkv", "a.03.mkv"]
    assert views.strip_common_prefix(short) == short
    swallow = ["Episode.", "Episode.1", "Episode.2"]
    assert views.strip_common_prefix(swallow) == swallow


def test_picker_view_groups_by_folder_and_strips_root() -> None:
    view = views.picker_view(season_files())

    assert view.folders == ["Season 2", "Subs", "Extras"]
    assert [f.folder for f in view.files] == [0] * 16 + [1] * 8 + [2] * 2
    assert view.files[0].name == "S02E01.1080p.WEB-DL.mkv"
    assert view.files[16].name == "S02E01.Rus.srt"
    assert view.files[24].name == "Poster.jpg"  # 2 files: no prefix stripping
    assert view.files[0].size_kb == 4 * MB
    assert view.pages == 5
    assert view.page_of(17) == 2
    assert view.by_index(99) is None


def test_picker_view_root_files_first_without_folder() -> None:
    files = [
        make_file(0, "Pack/Extras/a.mkv"),
        make_file(1, "Pack/readme.txt"),
        make_file(2, "Pack/Extras/b.mkv"),
    ]

    view = views.picker_view(files)

    assert [(f.index, f.folder) for f in view.files] == [(1, -1), (0, 0), (2, 0)]
    assert view.folders == ["Extras"]


def test_picker_view_single_file_has_no_folders() -> None:
    view = views.picker_view([make_file(0, "Dune.Part.Two.2024.mkv", 60 * GB)])

    assert view.folders == []
    assert view.files[0].folder == -1
    assert view.files[0].name == "Dune.Part.Two.2024.mkv"


def test_picker_view_merges_overflowing_folders() -> None:
    files = [
        make_file(i, f"R/Album {i:02d} with a long name/{i}.flac") for i in range(9)
    ]

    view = views.picker_view(files)

    assert len(view.folders) == 6
    assert view.folders[5] == views.OTHER_FOLDERS
    assert [f.folder for f in view.files] == [0, 1, 2, 3, 4, 5, 5, 5, 5]
    assert len(view.in_folder(5)) == 4


def test_picker_state_shape_and_caps() -> None:
    files = season_files()
    files[0]["priority"] = 0
    view = views.picker_view(files)
    name = "Дом Дракона / " + "х" * 100

    state = views.picker_state(name, view, 120 * GB, page=0, rev=3)

    assert state["v"] == 1 and state["r"] == 3 and state["m"] == "pick"
    assert len(state["n"]) == 90 and state["n"].endswith("…")
    assert (state["c"], state["k"]) == (26, 25)
    assert state["s"] == view.total_kb and state["z"] == view.total_kb - 4 * MB
    assert state["fs"] == 120 * MB
    assert (state["p"], state["q"]) == (0, 5)
    assert state["cl"] == [["v", 8, 7], ["a", 8, 8], ["s", 8, 8], ["o", 2, 2]]
    assert state["d"] == [["Season 2", 16, 15], ["Subs", 8, 8], ["Extras", 2, 2]]
    assert len(state["f"]) == 6
    assert state["f"][0] == [0, "S02E01.1080p.WEB-DL.mkv", 4 * MB, "v", 0, 0]
    assert size_of(state) <= 1024


def test_picker_state_unknown_free_space_and_page_clamp() -> None:
    view = views.picker_view(season_files())

    state = views.picker_state("x", view, -1, page=99, rev=1)

    assert state["fs"] == -1
    assert state["p"] == 4
    assert [row[0] for row in state["f"]] == [24, 25]


def test_picker_state_cuts_long_names() -> None:
    files = [make_file(i, f"Root/{'Папка' * 5}/{'Файл' * 10}{i}.mkv") for i in range(3)]

    state = views.picker_state("n", views.picker_view(files), GB, 0, 1)

    assert state["d"][0][0] == "Папка" * 3 + "…"
    assert len(state["f"][0][1]) == 28 and state["f"][0][1].endswith("…")


def test_picker_state_compacts_names_when_over_budget() -> None:
    # 6 Cyrillic max-length names, 6 long folders and 11-digit sizes overflow
    files = [
        make_file(
            i,
            f"Root/{'Папка ' + 'ж' * 20}{i}/{'ж' * 60}.mkv",
            size=99_999_999_999 * KB,
        )
        for i in range(60)
    ]
    view = views.picker_view(files)

    state = views.picker_state("ж" * 90, view, GB, 0, 999_999)

    assert size_of(state) <= 1024
    assert len(state["f"][0][1]) == views.COMPACT_FILE_NAME_LIMIT
    assert len(state["d"][0][0]) == views.COMPACT_FOLDER_NAME_LIMIT


def test_picker_fragment_and_page_answers() -> None:
    view = views.picker_view(season_files())

    fragment = views.picker_fragment(view, 1, 4)
    page = views.picker_page(view, 2, 4)

    assert set(fragment) == {"r", "p", "k", "z", "cl", "d", "f"}
    assert fragment["p"] == 1 and [r[0] for r in fragment["f"]][0] == 6
    assert page == {"r": 4, "p": 2, "f": [f.row(28) for f in view.page(2)]}
    assert size_of(fragment) <= 1024


def test_picker_terminal_and_captions() -> None:
    assert views.picker_terminal("Dune", "go", 5) == {
        "v": 1,
        "r": 5,
        "m": "go",
        "n": "Dune",
    }
    view = views.picker_view(
        [make_file(0, "a <x>.mkv", 2 * GB, 0), make_file(1, "b.mkv")]
    )
    assert views.picker_caption("T <b>", view) == (
        "📤 <b>T &lt;b&gt;</b> — выберите файлы · 1 из 2, 1 ГБ из 3 ГБ"
    )
    assert views.picker_started_caption("A & B") == "⬇️ Запущено: <b>A &amp; B</b>"


def test_toggle_priority_and_selected_counts() -> None:
    view = views.picker_view([make_file(0, "a.mkv"), make_file(1, "b.mkv", priority=0)])

    assert views.toggle_priority(view.files) == 1
    assert views.toggle_priority(view.in_class("v")[:1]) == 0
    assert views.selected_counts(
        [make_file(0, "a"), make_file(1, "b", priority=0)]
    ) == (
        1,
        2,
    )


# --- progress -----------------------------------------------------------------


def test_progress_snapshot_maps_info_row() -> None:
    snapshot = views.progress_snapshot(make_torrent(), fc=1, ft=3)

    assert snapshot == {
        "h": "3f9a1c2b",
        "n": "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p",
        "st": "downloading",
        "pr": 0.372,
        "sz": 64_906_854_400,
        "dn": 24_145_349_836,
        "ds": 12_400_000,
        "us": 1_200_000,
        "eta": 3290,
        "se": 12,
        "pe": 48,
        "ra": 0.12,
        "fc": 1,
        "ft": 3,
        "ad": 1_762_939_805,
        "fo": 0,
    }


def test_progress_snapshot_infinite_eta_force_and_name_cap() -> None:
    forced = views.progress_snapshot(
        make_torrent(state="forcedDL", eta=8_640_000, name="x" * 200), 1, 1
    )
    assert forced["eta"] == -1 and forced["fo"] == 1
    assert len(forced["n"]) == 120
    flagged = views.progress_snapshot(make_torrent(force_start=True, eta=-2), 1, 1)
    assert flagged["fo"] == 1 and flagged["eta"] == -1
    assert views.progress_snapshot({}, 0, 0)["st"] == ""


def test_progress_state_and_gone_fit_the_budget() -> None:
    state = views.progress_state(make_torrent(name="ж" * 120), 1, 1, rev=7, now=NOW)
    gone = views.progress_gone("ж" * 300, HASH, 8, NOW)

    assert (state["v"], state["r"], state["t"]) == (1, 7, NOW)
    assert state["st"] == "downloading"
    assert gone == {
        "v": 1,
        "r": 8,
        "t": NOW,
        "h": "3f9a1c2b",
        "n": gone["n"],
        "st": "gone",
    }
    assert len(gone["n"]) == 120
    assert size_of(state) <= 1024


@pytest.mark.parametrize(
    ("state", "progress", "phase"),
    [
        ("downloading", 0.2, "active"),
        ("stalledDL", 0.2, "active"),
        ("metaDL", 0, "active"),
        ("stoppedDL", 0.5, "paused"),
        ("pausedDL", 0.5, "paused"),
        ("uploading", 1, "done"),
        ("stalledUP", 1, "done"),
        ("downloading", 1, "done"),
        ("stoppedUP", 1, "stopped"),
        ("error", 0.4, "error"),
        ("missingFiles", 0.4, "error"),
    ],
)
def test_progress_phase(state: str, progress: float, phase: str) -> None:
    assert views.progress_phase({"state": state, "progress": progress}) == phase


def test_progress_captions() -> None:
    active = views.progress_snapshot(make_torrent(name="A <b>"), 1, 1)
    assert views.progress_caption(active) == (
        "⬇️ <b>A &lt;b&gt;</b> — 37,2 %, 11,8 МБ/с, осталось 54 м"
    )
    paused = views.progress_snapshot(make_torrent(state="stoppedDL", name="N"), 1, 1)
    assert views.progress_caption(paused) == "⏸ <b>N</b> — 37,2 %, на паузе"
    done = views.progress_snapshot(make_torrent(state="uploading", progress=1), 1, 1)
    assert views.progress_caption(done).startswith("✅ Загрузка завершена: <b>")
    error = views.progress_snapshot(make_torrent(state="missingFiles"), 1, 1)
    assert views.progress_caption(error).endswith("— Файлы не найдены")
    assert views.progress_caption(views.progress_gone("N", HASH, 1, NOW)) == (
        "🗑 Удалено из qBittorrent: <b>N</b>"
    )


# --- console ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "code", "tab"),
    [
        ("downloading", "d", "dl"),
        ("forcedDL", "df", "dl"),
        ("stalledDL", "ds", "dl"),
        ("queuedDL", "dq", "dl"),
        ("checkingDL", "dk", "dl"),
        ("allocating", "dk", "dl"),
        ("metaDL", "dm", "dl"),
        ("uploading", "u", "up"),
        ("forcedUP", "uf", "up"),
        ("stalledUP", "us", "up"),
        ("queuedUP", "uq", "up"),
        ("checkingUP", "uk", "up"),
        ("moving", "uk", "up"),
        ("stoppedDL", "p", "st"),
        ("pausedDL", "p", "st"),
        ("stoppedUP", "c", "st"),
        ("pausedUP", "c", "st"),
        ("error", "e", "er"),
        ("missingFiles", "ex", "er"),
        ("unknown", "e", "er"),
    ],
)
def test_console_state_codes(state: str, code: str, tab: str) -> None:
    assert views.state_code(state) == code
    assert views.tab_of(code) == tab


def test_console_row_is_positional() -> None:
    row = views.console_row(
        make_torrent(name="Дюна: Часть вторая / Dune: Part Two (2024)")
    )
    assert row == [
        "3f9a1c2b",
        "Дюна: Часть вторая / Dune: Part Two…",
        "d",
        0.372,
        12_400_000,
        3290,
        0.12,
    ]
    assert len(row[1]) == 36

    seeding = views.console_row(
        make_torrent(state="uploading", progress=1, eta=8_640_000)
    )
    assert seeding[2:6] == ["u", 1.0, 1_200_000, -1]


def console_torrents() -> list[dict[str, Any]]:
    states = [
        "downloading",
        "downloading",
        "stalledDL",
        "uploading",
        "uploading",
        "stalledUP",
        "uploading",
        "stoppedUP",
        "pausedDL",
        "error",
    ]
    return [
        make_torrent(hash=f"{i:08x}" + "a" * 32, name=f"T{i}", state=state)
        for i, state in enumerate(states)
    ]


def test_console_state_counts_tabs_and_pages() -> None:
    transfer = {
        "dl_info_speed": 15_500_000,
        "up_info_speed": 4_030_000,
        "dl_info_data": 13_207_024_435,
        "up_info_data": 4_831_838_208,
        "connection_status": "firewalled",
    }

    state = views.console_state(
        console_torrents(), transfer, 1_330_409_069_608, True, "all", 1, 12, NOW
    )

    assert (state["v"], state["r"], state["t"]) == (1, 12, NOW)
    assert (state["tab"], state["pg"], state["np"]) == ("all", 1, 2)
    assert state["cnt"] == {"all": 10, "dl": 3, "up": 4, "st": 2, "er": 1}
    assert state["tr"] == {
        "d": 15_500_000,
        "u": 4_030_000,
        "dd": 13_207_024_435,
        "ud": 4_831_838_208,
        "fs": 1_330_409_069_608,
        "ft": 0,
        "alt": 1,
        "cs": 1,
    }
    assert [row[1] for row in state["rows"]] == ["T6", "T7", "T8", "T9"]

    up = views.console_state(console_torrents(), transfer, -1, False, "up", 5, 1, NOW)
    assert (up["tab"], up["pg"], up["np"]) == ("up", 0, 1)
    assert [row[2] for row in up["rows"]] == ["u", "u", "us", "u"]
    assert up["tr"]["fs"] == 0 and up["tr"]["alt"] == 0

    assert views.console_state([], {}, 0, False, "bogus", -1, 1, NOW) == {
        "v": 1,
        "r": 1,
        "t": NOW,
        "tab": "all",
        "pg": 0,
        "np": 1,
        "cnt": {"all": 0, "dl": 0, "up": 0, "st": 0, "er": 0},
        "tr": {"d": 0, "u": 0, "dd": 0, "ud": 0, "fs": 0, "ft": 0, "alt": 0, "cs": 0},
        "rows": [],
    }


def test_console_state_worst_case_fits_and_compacts() -> None:
    torrents = [
        make_torrent(
            hash=f"{i:08x}" + "f" * 32,
            name="ж" * 50,
            progress=0.999,
            dlspeed=99_999_999_999_999,
            eta=8_639_999,
            ratio=99999.99,
        )
        for i in range(10_000)
    ]
    transfer = dict.fromkeys(
        ("dl_info_speed", "up_info_speed", "dl_info_data", "up_info_data"),
        99_999_999_999_999,
    )

    state = views.console_state(
        torrents, transfer, 99_999_999_999_999, True, "all", 0, 999_999, NOW
    )

    assert size_of(state) <= 1024
    assert len(state["rows"][0][1]) == views.COMPACT_CONSOLE_NAME_LIMIT


def test_console_unavailable_details_and_patch() -> None:
    assert views.console_unavailable(3, NOW) == {
        "v": 1,
        "r": 3,
        "t": NOW,
        "err": "qBittorrent недоступен",
    }

    details = views.console_details(make_torrent(save_path="/mnt/" + "x" * 80))
    assert details["x"]["h"] == "3f9a1c2b"
    assert details["x"]["l"].startswith(
        "Загружается · 60,4 ГБ · ↓ 22,5 ГБ · ↑ 2,7 ГБ · ×0,12 · "
        "сиды 12 (40) · пиры 48 (15) · movies · "
    )
    assert len(details["x"]["sp"]) == 60
    assert views.console_details(make_torrent(category="", state="weird"))["x"][
        "l"
    ].startswith("weird · ")

    state = views.console_state(console_torrents(), {}, 0, False, "all", 0, 5, NOW)
    patch = views.console_patch(state, make_torrent())
    assert set(patch) == {"r", "t", "cnt", "tr", "row", "x"}
    assert patch["r"] == 5 and patch["row"][0] == "3f9a1c2b"
    assert set(views.console_patch(state, None)) == {"r", "t", "cnt", "tr"}


def test_console_caption() -> None:
    transfer = {"dl_info_speed": 15_500_000, "up_info_speed": 4_030_000}
    assert views.console_caption(console_torrents(), transfer) == (
        "qBittorrent · ↓ 14,8 МБ/с · ↑ 3,8 МБ/с · 10 торрентов (3 загружаются)"
    )
    one = [make_torrent()]
    assert views.console_caption(one, {}).endswith("1 торрент (1 загружается)")
    assert views.console_caption([], transfer) == "qBittorrent · нет задач"


def test_every_builder_output_serializes_within_the_limit() -> None:
    view = views.picker_view(season_files())
    for payload in (
        views.picker_state("n", view, GB, 0, 1),
        views.picker_fragment(view, 0, 1),
        views.picker_page(view, 0, 1),
        views.picker_terminal("n", "x", 1),
        views.progress_state(make_torrent(), 1, 1, 1, NOW),
        views.progress_gone("n", HASH, 1, NOW),
        views.console_state(console_torrents(), {}, 0, False, "all", 0, 1, NOW),
        views.console_unavailable(1, NOW),
        views.console_details(make_torrent()),
    ):
        assert len(dumps_state(payload).encode()) <= 1024
