from collections.abc import Callable

import pytest

from tj_bot.services.enrich.model import LoginRequiredError, ScrapeError, TorrentMeta
from tj_bot.services.enrich.parsers import (
    byrutor,
    megapeer,
    nnmclub,
    parser_for,
    pctorrent,
    rutor,
    rutracker,
    tapochek,
)

from .conftest import fixture_html

NNM = "https://nnmclub.to/forum/viewtopic.php?t={}"
RUTOR = "https://rutor.info/torrent/{}/x"
TAP = "https://tapochek.net/viewtopic.php?t={}"
RT = "https://rutracker.org/forum/viewtopic.php?t={}"
MP = "https://megapeer.vip/torrent/{}/x"
PCT = "https://pc-torrents.games/{}-x.html"
BR = "https://byrutgame.org/{}-x.html"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://rutracker.org/forum/viewtopic.php?t=1", rutracker.parse),
        ("https://www.rutracker.org/forum/viewtopic.php?t=1", rutracker.parse),
        ("https://nnmclub.to/forum/viewtopic.php?t=1", nnmclub.parse),
        ("https://tapochek.net/viewtopic.php?t=1", tapochek.parse),
        ("http://rutor.info/torrent/1/x", rutor.parse),
        ("https://rutor.is/torrent/1/x", rutor.parse),
        ("https://megapeer.vip/torrent/1/x", megapeer.parse),
        ("https://www.pc-torrents.games/1-x.html", pctorrent.parse),
        ("https://byrutgame.org/1-x.html", byrutor.parse),
    ],
)
def test_parser_for_known_hosts(url: str, expected: object) -> None:
    assert parser_for(url) is expected


def test_parser_for_unknown_host_returns_none() -> None:
    assert parser_for("https://example.com/x") is None
    assert parser_for("not a url") is None


@pytest.mark.parametrize("parse", [megapeer.parse, pctorrent.parse, byrutor.parse])
def test_structured_parsers_raise_on_foreign_page(
    parse: Callable[[str, str], TorrentMeta],
) -> None:
    with pytest.raises(ScrapeError):
        parse("<html><body><p>nothing here</p></body></html>", "https://example.org/")


# --- nnmclub ---------------------------------------------------------------


def test_nnmclub_movie_extracts_full_card() -> None:
    meta = nnmclub.parse(fixture_html("nnmclub", "1722958"), NNM.format(1722958))
    assert meta.tracker == "nnmclub"
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Часть вторая",
        "Dune: Part Two",
        2024,
    )
    assert meta.poster_urls == [
        "https://i123.fastpic.org/big/2024/0513/09/a6c01a9df1e69fb02e39bfffb1fa2b09.jpg"
    ]
    assert meta.screenshots[0] == (
        "https://i.ibb.co/rw7VvdD/vlcsnap-2024-05-13-12h37m01s396.png"
    )
    assert len(meta.screenshots) <= 6
    assert all("image.php" not in u for u in meta.poster_urls + meta.screenshots)
    assert meta.genres == ["фантастика", "боевик", "драма", "приключения"]
    assert meta.countries[:3] == ["США", "Канада", "ОАЭ"]
    assert meta.director == "Дени Вильнёв"
    assert meta.cast[0] == "Тимоти Шаламе" and len(meta.cast) == 8
    assert meta.duration_min == 166
    assert meta.plot is not None
    assert meta.plot.startswith("Герцог Пол Атрейдес присоединяется")
    assert meta.translation == [
        "Дублированный",
        "Профессиональный многоголосый",
        "Авторский",
    ]
    assert meta.subtitles == ["рус", "укр", "англ"]
    assert meta.video == "BDRemux 1080p | AVC/H.264, 1920x1080, 21.2 Mbps, 23.976 fps"
    assert meta.audio is not None
    assert meta.audio.startswith("AC-3, 6 ch, 384 Kbps | Русский Dub iTunes")
    assert (meta.imdb_id, meta.kp_id) == ("tt15239678", 4540126)
    assert (meta.season, meta.episodes) == (None, None)
    assert meta.extra["age"] == "PG-13"
    assert meta.extra["release"] == "NNMClub"


def test_nnmclub_series_reads_season_and_episodes_from_title() -> None:
    meta = nnmclub.parse(fixture_html("nnmclub", "1761100"), NNM.format(1761100))
    assert (meta.title_ru, meta.title_en) == ("Дюна: Пророчество", "Dune: Prophecy")
    assert (meta.season, meta.episodes) == (1, "1-6 of 6")
    assert meta.duration_min == 60
    assert meta.poster_urls == [
        "https://i8.imageban.ru/out/2024/11/18/208698015121f3d06d0e89f291802598.webp"
    ]
    assert meta.director == "Анна Ферстер, Джон Камерон, Ричард Дж. Льюис"
    assert meta.subtitles == ["рус", "англ", "болг", "рум"]
    assert (meta.imdb_id, meta.kp_id) == ("tt10466872", 1274452)
    assert meta.extra["release"] == "EniaHD"


def test_nnmclub_without_post_body_returns_title_only() -> None:
    meta = nnmclub.parse(
        "<html><title>Что-то / Something (2020) :: NNM-Club</title></html>",
        NNM.format(1),
    )
    assert (meta.title_ru, meta.title_en, meta.year) == ("Что-то", "Something", 2020)
    assert meta.poster_urls == [] and meta.plot is None


# --- rutor -----------------------------------------------------------------


def test_rutor_movie_with_collapsed_screenshots() -> None:
    meta = rutor.parse(fixture_html("rutor", "983790"), RUTOR.format(983790))
    assert meta.tracker == "rutor"
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Часть вторая",
        "Dune: Part Two",
        2024,
    )
    assert meta.poster_urls == ["https://i.postimg.cc/m253K3HP/cover.jpg"]
    assert meta.screenshots[0] == (
        "https://i123.fastpic.org/big/2024/0510/42/_9454865df98a71ed7d3aabd578466742.png"
    )
    assert len(meta.screenshots) == 5
    assert meta.genres == ["фантастика", "боевик", "драма", "приключения"]
    assert meta.countries == ["США", "Канада"]
    assert meta.director == "Дени Вильнёв"
    assert meta.cast[:2] == ["Тимоти Шаламе", "Зендея"] and len(meta.cast) == 8
    assert meta.duration_min == 165
    assert meta.plot is not None
    assert meta.plot.startswith("Примкнув к фременам, Пол Атрейдес")
    assert meta.translation == [
        "Дублированный (профессиональный) | Bravo Records Georgia / Movie Dubbing",
        "Дублированный (неофициальный) | Red Head Sound",
    ]
    assert meta.subtitles == ["рус", "англ"]
    assert (
        meta.video == "BDRip (1080p) | 1920x804 at 23.976 fps, x264@L4.1, ~10500 kbps"
    )
    assert meta.audio is not None
    assert meta.audio.startswith("AC3 / 5.1 / 48 kHz / 384 kbps")
    assert (meta.imdb_id, meta.kp_id) == ("tt15239678", 4540126)
    assert meta.extra["category"] == "Зарубежные фильмы"
    assert meta.extra["size"] == "14.29 GB"
    assert meta.extra["format"] == "Matroska"
    assert "site_rating" not in meta.extra


def test_rutor_series_with_s01_tag_and_language_coded_audio() -> None:
    meta = rutor.parse(fixture_html("rutor", "1016793"), RUTOR.format(1016793))
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Пророчество",
        "Dune: Prophecy",
        2024,
    )
    assert (meta.season, meta.episodes) == (1, None)
    assert meta.poster_urls == [
        "http://i124.fastpic.org/big/2024/1117/49/3e0b6e04622a442494d9c8abdd032749.jpg"
    ]
    assert meta.duration_min == 60
    assert len(meta.translation) == 2
    assert meta.audio is not None
    assert meta.audio.startswith("RUS AC-3, 5.1, 48 kHz, 640 kbps")
    assert meta.subtitles == ["рус", "англ", "болг", "рум"]
    assert (meta.imdb_id, meta.kp_id) == ("tt10466872", 1274452)
    assert meta.extra["category"] == "Зарубежные сериалы"
    assert meta.extra["size"] == "22.01 GB"


def test_rutor_page_without_details_table_keeps_title() -> None:
    meta = rutor.parse(
        "<html><body><h1>Фильм / Film (2001) BDRip</h1></body></html>", RUTOR.format(1)
    )
    assert (meta.title_ru, meta.title_en, meta.year) == ("Фильм", "Film", 2001)
    assert meta.poster_urls == []


# --- tapochek --------------------------------------------------------------


def test_tapochek_game_repack() -> None:
    meta = tapochek.parse(fixture_html("tapochek", "289457"), TAP.format(289457))
    assert meta.tracker == "tapochek"
    assert (meta.title_ru, meta.title_en, meta.year) == (None, "Dune: Awakening", 2025)
    assert meta.poster_urls == [
        "https://i7.imageban.ru/out/2026/09/19/606f99d7476a3733ee3f696821ba2d7d.jpg"
    ]
    assert meta.screenshots[0] == (
        "https://i6.imageban.ru/out/2026/09/19/ac1905da9a44df64ee3260ea38876ca1.jpg"
    )
    assert len(meta.screenshots) == 6
    assert meta.genres == ["action", "survival", "third-person", "3d"]
    assert meta.plot is not None
    assert meta.plot.startswith("Dune: Awakening — это эпическая игра")
    # "Видео: NVIDIA GeForce..." is a system requirement, not a codec line
    assert meta.video is None
    assert meta.extra["developer"] == "Funcom"
    assert meta.extra["release_date"] == "10 июня 2025"
    assert meta.extra["title"] == "Dune: Awakening (RUS/ENG/MULTI14) [Repack]"


def test_tapochek_members_only_topic_raises_login_required() -> None:
    with pytest.raises(LoginRequiredError, match="login required"):
        tapochek.parse(fixture_html("tapochek", "266710"), TAP.format(266710))


def test_tapochek_members_movie_dune_part_two() -> None:
    meta = tapochek.parse(
        fixture_html("tapochek", "members_266710"), TAP.format(266710)
    )
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Часть вторая",
        "Dune: Part Two",
        2024,
    )
    assert meta.poster_urls[0] == (
        "https://i4.imageban.ru/out/2024/03/04/4d9b5119fbfa7b7c27170f3bd9be18a7.jpg"
    )
    assert len(meta.screenshots) == 6
    assert meta.genres == ["фантастика", "боевик", "драма", "приключения"]
    assert meta.countries == ["США", "Канада", "ОАЭ", "Венгрия", "Италия"]
    assert meta.director == "Дени Вильнёв"
    assert meta.cast[:3] == ["Тимоти Шаламе", "Зендея", "Ребекка Фергюсон"]
    assert len(meta.cast) == 8
    assert meta.duration_min == 166
    assert meta.plot == (
        "Герцог Пол Атрейдес присоединяется к фременам, чтобы стать Муад Дибом, "
        "одновременно пытаясь остановить наступление войны."
    )
    assert meta.translation[0] == "Профессиональный (полное дублирование) [Jaskier]"
    assert len(meta.translation) == 6
    assert meta.subtitles == ["рус", "укр", "англ"]
    assert meta.video is not None
    assert meta.video.startswith("BDRemux 2160p | 4K | HDR | HEVC / 51.3 Mb/s")
    assert meta.audio is not None
    assert meta.audio.startswith("48 kHz, AC-3, 192 kb/s, 2 channels")
    assert (meta.imdb_id, meta.kp_id) == ("tt15239678", 4540126)
    assert (meta.season, meta.episodes) == (None, None)
    assert meta.extra["age"] == "12+"
    assert meta.extra["format"] == "MKV"


# --- rutracker -------------------------------------------------------------


def test_rutracker_movie_uses_first_post_only() -> None:
    meta = rutracker.parse(fixture_html("rutracker", "synthetic_movie"), RT.format(1))
    assert meta.tracker == "rutracker"
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Планета Дюна",
        "Planet Dune",
        2021,
    )
    assert meta.poster_urls == [
        "https://i115.fastpic.org/big/2021/0917/2c/8b1f9f0e5ad4a0b58b96d5b5c33f6a2c.jpg"
    ]
    assert meta.screenshots == [  # fastpic thumb -> big, .jpeg -> .jpg
        "https://i115.fastpic.org/big/2021/0917/3b/8e9c0a1f2b3d4e5f6a7b8c9d0e1f2a3b.jpg",
        "https://i115.fastpic.org/big/2021/0917/6d/1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d.jpg",
    ]
    assert "imgur" not in str(meta.screenshots)  # second post ignored
    assert meta.genres == ["фантастика", "боевик"]
    assert meta.countries == ["США"]
    assert meta.director == "Глен Миллер / Glenn Miller"
    assert len(meta.cast) == 5
    assert meta.duration_min == 86
    assert meta.translation == ["Профессиональный (многоголосый закадровый)"]
    assert meta.subtitles == ["англ"]
    assert meta.video == "BDRemux | MPEG-4 AVC, 1920x1080, 23.976 fps, 24.7 Mb/s"
    assert (meta.imdb_id, meta.kp_id) == ("tt15161850", 4621436)
    assert meta.extra["format"] == "MKV"


def test_rutracker_series_native_title_gives_season_and_episodes() -> None:
    meta = rutracker.parse(fixture_html("rutracker", "synthetic_series"), RT.format(2))
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Пророчество",
        "Dune: Prophecy",
        2024,
    )
    assert (meta.season, meta.episodes) == (1, "1-6 of 6")
    assert meta.poster_urls == ["https://imgbox.com/i/poster1234.jpg"]
    assert meta.countries == ["США", "Канада"]
    assert len(meta.cast) == 8
    assert meta.duration_min == 60
    assert meta.video is not None
    assert meta.video.startswith("WEB-DL 2160p | HEVC, 3840x1600")
    assert (meta.imdb_id, meta.kp_id) == ("tt10466872", 1274452)


# --- megapeer --------------------------------------------------------------


def test_megapeer_movie_remux() -> None:
    meta = megapeer.parse(fixture_html("megapeer", "179547"), MP.format(179547))
    assert meta.tracker == "megapeer"
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Часть вторая",
        "Dune: Part Two",
        2024,
    )
    assert meta.poster_urls == [
        "https://i123.fastpic.org/big/2024/0810/b5/736ad9cdfa96f04652b1e094586dacb5.jpg"
    ]
    assert len(meta.screenshots) == 4
    assert all(
        s.startswith("https://i123.fastpic.org/thumb/") for s in meta.screenshots
    )
    assert meta.genres == [
        "фантастика",
        "боевик",
        "драма",
        "приключения",
        "экранизация",
    ]
    assert meta.countries == ["США", "Канада"]
    assert meta.director == "Дени Вильнёв"
    assert len(meta.cast) == 8 and meta.cast[0] == "Тимоти Шаламе"
    assert meta.duration_min == 165
    assert meta.plot is not None
    assert meta.plot.startswith("Потеряв все, Пол Атрейдес") and len(meta.plot) <= 1000
    assert meta.translation[:2] == [
        "Дубляж | Bravo Records Georgia",
        "Дубляж (неофиц.) | Red Head Sound",
    ]
    assert len(meta.translation) == 6
    assert meta.subtitles == ["рус", "англ", "укр"]
    assert meta.video == "Blu-Ray Remux 2160p HEVC HDR10 Dolby Vision"
    assert meta.audio == "E-AC3 5.1 / AC3 5.1 / AC3 2.0 / TrueHD Atmos 7.1"
    assert (meta.imdb_id, meta.kp_id) == ("tt15239678", 4540126)
    assert meta.extra["studio"].startswith("Legendary")


def test_megapeer_tv_series() -> None:
    meta = megapeer.parse(fixture_html("megapeer", "183471"), MP.format(183471))
    assert (meta.title_ru, meta.title_en, meta.year, meta.season) == (
        "Дюна: Пророчество",
        "Dune: Prophecy",
        2024,
        1,
    )
    assert meta.duration_min == 65
    assert meta.translation == ["MVO | Продубляж", "MVO | Sunnysiders"]
    assert meta.video is not None
    assert meta.video.startswith("WEB-DL 1080p") and "H.264" in meta.video
    assert meta.audio == "AC3 5.1 / E-AC3 JOC 5.1"
    assert meta.extra["container"] == "MKV"
    assert len(meta.screenshots) == 6


# --- pctorrent -------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "title", "year", "developer", "version"),
    [
        ("7826", "Dune: Awakening", 2025, "Funcom", "1.5.15.3"),
        ("5499", "Dune: Spice Wars", 2023, "Shiro Games", None),
    ],
)
def test_pctorrent_game_pages(
    topic: str, title: str, year: int, developer: str, version: str | None
) -> None:
    meta = pctorrent.parse(fixture_html("pctorrent", topic), PCT.format(topic))
    assert meta.tracker == "pctorrent"
    assert (meta.title_en, meta.title_ru, meta.year) == (title, None, year)
    assert meta.poster_urls
    assert meta.poster_urls[0].startswith("https://pc-torrents.games/uploads/posts/")
    assert len(meta.screenshots) == 3
    assert meta.genres and all(g == g.lower() and "года" not in g for g in meta.genres)
    assert meta.extra["developer"] == developer
    assert meta.extra.get("version") == version
    assert {"publisher", "language", "os", "cpu", "ram", "gpu", "disk", "size"} <= set(
        meta.extra
    )
    assert meta.plot is not None and 100 < len(meta.plot) <= 1000
    assert not meta.plot.startswith("Описание")


def test_pctorrent_plot_joins_paragraphs() -> None:
    meta = pctorrent.parse(fixture_html("pctorrent", "7826"), PCT.format(7826))
    assert meta.plot is not None
    assert meta.plot.startswith("Dune: Awakening — это масштабная игра")
    assert meta.plot.endswith("…") and len(meta.plot) <= 1000


# --- byrutor ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("topic", "title", "year", "developer", "language"),
    [
        ("23129", "Dune: Spice Wars", 2023, "Shiro Games", "Русский"),
        ("40094", "Dune: Awakening", 2025, "Funcom", "Русский"),
    ],
)
def test_byrutor_game_pages(
    topic: str, title: str, year: int, developer: str, language: str
) -> None:
    meta = byrutor.parse(fixture_html("byrutor", topic), BR.format(topic))
    assert meta.tracker == "byrutor"
    assert (meta.title_en, meta.year) == (title, year)
    assert len(meta.poster_urls) == 1
    assert meta.poster_urls[0].startswith("https://img.byrutgame.org/uploads/posts/")
    assert 1 <= len(meta.screenshots) <= 6
    assert meta.genres and all(g == g.lower() for g in meta.genres)
    assert meta.extra["developer"] == developer
    assert meta.extra["language"] == language
    assert meta.extra["version"].startswith("v ")
    assert "[Новая версия]" not in meta.extra["version"]
    assert {"os", "cpu", "ram", "gpu", "disk", "steam_rating"} <= set(meta.extra)
    assert meta.translation == []  # 'Озвучка' is a language, not a translation
    assert meta.plot is not None and len(meta.plot) <= 1000


def test_byrutor_extra_details() -> None:
    meta = byrutor.parse(fixture_html("byrutor", "23129"), BR.format(23129))
    assert meta.extra["voice"] == "Английская"
    assert meta.extra["subs"] == "На русском языке"
    assert meta.extra["hltb"] == "12 / 27 / 80"
    assert meta.extra["size"] == "5.7 ГБ"
    assert meta.extra["edition"] == "Пиратка Portable (Таблетка: Вшита)"
    assert meta.genres == [
        "менеджмент",
        "научная фантастика",
        "стратегии в реальном времени",
    ]


def test_meta_lists_are_capped() -> None:
    for tracker, topic, parse, base in (
        ("megapeer", "179547", megapeer.parse, MP),
        ("byrutor", "23129", byrutor.parse, BR),
        ("tapochek", "members_266710", tapochek.parse, TAP),
    ):
        meta: TorrentMeta = parse(fixture_html(tracker, topic), base.format(topic))
        assert len(meta.screenshots) <= 6
        assert len(meta.cast) <= 8
        assert len(meta.translation) <= 6
