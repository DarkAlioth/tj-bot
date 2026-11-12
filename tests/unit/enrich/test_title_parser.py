import pytest

from tj_bot.services.enrich.title_parser import ParsedTitle, is_mojibake, parse_title

CASES: list[tuple[str, dict[str, object]]] = [
    (
        "Дюна: Часть вторая / Dune: Part Two (2024) UHD BDRemux 2160p | 4K | HDR10 "
        "| Dolby Vision | D, P, A",
        {
            "title_ru": "Дюна: Часть вторая",
            "title_en": "Dune: Part Two",
            "year": 2024,
            "resolution": "2160p",
            "hdr": ["HDR10", "Dolby Vision"],
            "source": "UHD BDRemux",
            "audio_tags": ["Dub", "MVO", "AVO"],
        },
    ),
    (
        "Dune: Prophecy / S1E1-6 of 6 [2024, HDR10, Dolby Vision, WEB-DL 2160p] "
        "[Hybrid] 8x MVO + Dub + Original + Sub",
        {
            "title_ru": None,
            "title_en": "Dune: Prophecy",
            "year": 2024,
            "season": 1,
            "episodes": "1-6 of 6",
            "source": "WEB-DL",
            "resolution": "2160p",
            "hdr": ["HDR10", "Dolby Vision"],
            "audio_tags": ["Dub", "MVO", "Original", "Sub"],
        },
    ),
    (
        "Дюна: Пророчество / Dune: Prophecy / Сезон: 1 / Серии: 1-6 из 6 "
        "(Анна Фёрстер) [2024, США, фантастика, WEB-DL 1080p] "
        "[MVO|Дубляж, Sunnysiders]",
        {
            "title_ru": "Дюна: Пророчество",
            "title_en": "Dune: Prophecy",
            "year": 2024,
            "season": 1,
            "episodes": "1-6 of 6",
            "source": "WEB-DL",
            "resolution": "1080p",
            "audio_tags": ["Dub", "MVO"],
        },
    ),
    (
        "Dune: Awakening - Ultimate Edition [v 1.5.3.0.2111270 + DLCs] (2025) PC "
        "| RePack-FitGirl",
        {
            "title_en": "Dune: Awakening - Ultimate Edition",
            "title_ru": None,
            "year": 2025,
            "extras": ["RePack"],
            "resolution": None,
            "source": None,
        },
    ),
    (
        "Дюна / Dune: Part One (2021) UHD BDRip [AV1/2160p] [4K, HDR10, 10-bit]",
        {
            "title_ru": "Дюна",
            "title_en": "Dune: Part One",
            "year": 2021,
            "source": "UHD BDRip",
            "codec": "AV1",
            "resolution": "2160p",
            "hdr": ["HDR10"],
        },
    ),
    (
        "[CD/DL] Total DOS Collection #24 (Blood, Carmageddon, Doom, Dune, Dungeon "
        "Keeper, Fallout)",
        {"title_en": "Total DOS Collection #24", "title_ru": None, "year": None},
    ),
    (
        "Dune: Part Two (”µ ’»‘) [2024 ., „°‚°‚°, ±µ °°, »‡µ BDRemux 2160p] [4K|HDR] "
        "[DUB] RUS",
        {
            "title_en": "Dune: Part Two",
            "title_ru": None,
            "year": 2024,
            "source": "BDRemux",
            "resolution": "2160p",
            "hdr": ["HDR"],
            "audio_tags": ["Dub"],
        },
    ),
    (
        "”°: ‡µ‚ / Dune: Prophecy / µ·: 1 / µ: 6 · 6 (° ¤µ‚µ) [2024, „°‚°‚°, ±µ °°, "
        "»‡µ WEB-DL 1080p] [D] RUS",
        {
            "title_en": "Dune: Prophecy",
            "title_ru": None,
            "year": 2024,
            "source": "WEB-DL",
            "resolution": "1080p",
        },
    ),
    (
        "Дюна: Пророчество / Dune: Prophecy  / S1E1-6 of 6 (2024) WEB-DL [H.264/1080p]"
        "  Продубляж, Sunnysiders",
        {
            "title_ru": "Дюна: Пророчество",
            "title_en": "Dune: Prophecy",
            "season": 1,
            "episodes": "1-6 of 6",
            "codec": "H.264",
            "resolution": "1080p",
            "source": "WEB-DL",
        },
    ),
    (
        "Дюна / Dune (1984) BDRemux [H.264/1080p] [Extended Edition]",
        {
            "title_ru": "Дюна",
            "title_en": "Dune",
            "year": 1984,
            "extras": ["Extended"],
            "source": "BDRemux",
        },
    ),
    (
        "Дюна: Часть вторая / Dune: Part Two (2024) BDRip [H.265/1080p] [10-bit]",
        {"codec": "HEVC", "resolution": "1080p", "source": "BDRip"},
    ),
    (
        "Дюна: Часть вторая / Dune: Part Two (2024) UHD WEB-DL-HEVC 2160p от селезень "
        "| 4K | HDR | Dolby Vision Profile 8 | D, P, A",
        {
            "source": "WEB-DL",
            "codec": "HEVC",
            "resolution": "2160p",
            "hdr": ["Dolby Vision", "HDR"],
            "audio_tags": ["Dub", "MVO", "AVO"],
        },
    ),
    (
        "Дюна: Пророчество / Dune: Prophecy [S01] (2024) WEB-DL 1080p | P "
        "| Продубляж, Sunnysiders",
        {"season": 1, "episodes": None, "audio_tags": ["MVO"], "resolution": "1080p"},
    ),
    (
        "Дюна: Пророчество / Dune: Prophecy [1 сезон] (2024) WEB-DL 1080p | P",
        {"season": 1, "title_ru": "Дюна: Пророчество"},
    ),
    (
        "Dune: Awakening (RUS/ENG/MULTI14) [Repack] RUS",
        {"title_en": "Dune: Awakening", "year": None, "extras": ["RePack"]},
    ),
    (
        "Dune: Prophecy / S1E1-6 of 6 [2024, WEB-DL 1080p] 6 x MVO (LostFilm, HDrezka "
        "Studio, TVShows) + Original + Sub",
        {"audio_tags": ["MVO", "Original", "Sub"], "episodes": "1-6 of 6"},
    ),
    (
        "[DL] Dune: Awakening [P] [RUS + ENG + 12 / ENG] (2025, Action) "
        "(1.5.20.2.2181988) [Portable]",
        {"title_en": "Dune: Awakening", "year": 2025},
    ),
    (
        "Дюна: Часть вторая / Dune: Part Two (2024) Blu-Ray Remux 2160p | 4K | HDR10 "
        "| Dolby Vision | D, P, A",
        {"source": "BDRemux", "hdr": ["HDR10", "Dolby Vision"], "resolution": "2160p"},
    ),
    (
        "Дюна: Часть вторая / Dune: Part Two (2024) Blu-Ray 2160p | 4K | HDR "
        "| Dolby Vision | D",
        {"source": "Blu-Ray", "hdr": ["Dolby Vision", "HDR"], "audio_tags": ["Dub"]},
    ),
    (
        "Дюна / Dune: Part One (2021) BDRip 1080p от селезень | D",
        {
            "title_ru": "Дюна",
            "title_en": "Dune: Part One",
            "source": "BDRip",
            "resolution": "1080p",
        },
    ),
    (
        "Аватар: Путь воды / Avatar: The Way of Water (2022) UHD BDRemux 2160p | 4K "
        "| HDR10+ | Dolby Vision | 3D | IMAX | D",
        {"hdr": ["HDR10+", "Dolby Vision"], "extras": ["3D", "IMAX"]},
    ),
    (
        "Бегущий по лезвию / Blade Runner (1982) BDRip 1080p [Director's Cut] | AVO",
        {"year": 1982, "extras": ["Director's Cut"], "audio_tags": ["AVO"]},
    ),
    (
        "Breaking Bad / S05E12 of 16 [2013, WEBRip 720p] MVO (LostFilm)",
        {
            "season": 5,
            "episodes": "12 of 16",
            "source": "WEBRip",
            "resolution": "720p",
            "title_en": "Breaking Bad",
        },
    ),
    (
        "Клиника / Scrubs / Сезон: 2 / Серии: 1-22 из 22 [2002, HDTVRip 480p] DVO",
        {
            "season": 2,
            "episodes": "1-22 of 22",
            "source": "HDTVRip",
            "resolution": "480p",
            "audio_tags": ["DVO"],
        },
    ),
    (
        "Some Album - Greatest Hits (2019) [FLAC]",
        {
            "title_en": "Some Album - Greatest Hits",
            "year": 2019,
            "resolution": None,
            "source": None,
        },
    ),
    (
        "Матрица / The Matrix (1999) DVDRip x264 | Дубляж",
        {"source": "DVDRip", "codec": "x264", "audio_tags": ["Dub"]},
    ),
    (
        "Фильм без всего",
        {"title_ru": "Фильм без всего", "title_en": None, "year": None, "chips": []},
    ),
    ("", {"title_ru": None, "title_en": None, "year": None}),
    ("(”µ ’»‘)", {"title_ru": None, "title_en": None}),
    (
        "Дюна: Пророчество / Dune: Prophecy / S1E1-6 of 6 "
        "[2024, HDR10, WEB-DL 2160p, HEVC]",
        {"chips": ["2160p", "HDR10", "WEB-DL", "HEVC", "S1 E1-6/6"]},
    ),
]


@pytest.mark.parametrize(("title", "expected"), CASES, ids=[c[0][:40] for c in CASES])
def test_parse_title_matches_expected_fields(
    title: str, expected: dict[str, object]
) -> None:
    parsed = parse_title(title)
    assert isinstance(parsed, ParsedTitle)
    for key, value in expected.items():
        assert getattr(parsed, key) == value, key


def test_chips_capped_at_five_and_ordered() -> None:
    parsed = parse_title(
        "X / Y (2020) UHD BDRemux 2160p HDR10+ Dolby Vision HLG HEVC S1E1-3 of 3 "
        "3D IMAX"
    )
    assert parsed.chips == ["2160p", "HDR10+", "Dolby Vision", "HLG", "UHD BDRemux"]


def test_is_mojibake_detects_cp1251_garbage() -> None:
    assert is_mojibake("”µ ’»‘")
    assert not is_mojibake("Дюна: Часть вторая")
    assert not is_mojibake("Dune — Part Two")  # a single dash is fine
