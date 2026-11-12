"""MegaPeer topic page: <h1> title, table#details td#descr free-form text.

The poster is the first <img> of the description, labels come as
``<b>Label:</b> value<br/>`` lines in several stylings, screenshots sit in a
``.sp-wrap`` spoiler as lazy thumbnails. Encoding windows-1251.
"""

import re

import lxml.html
from lxml.html import HtmlElement

from tj_bot.services.enrich.model import ScrapeError, TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    MAX_SCREENSHOTS,
    absolute,
    add_translation,
    apply_field,
    clean,
    finalize_av,
    find_ids,
    labeled,
    lines_of,
    root_of,
)
from tj_bot.services.enrich.title_parser import parse_title

HOSTS: frozenset[str] = frozenset({"megapeer.vip"})
TRACKER = "megapeer"
_ICON_HOSTS = (
    "kartinko.org/image_library",
    "imdb.desol.one",
    "rating.kinopoisk.ru",
    "/pic/",
)
_TRANSLATION_ITEM = re.compile(r"^(?:№|#)\s*\d+\s*:\s*(.+)$")


def _is_poster(img: HtmlElement) -> bool:
    src = img.get("src") or ""
    if not src or any(marker in src for marker in _ICON_HOSTS):
        return False
    if src.lower().endswith(".gif"):
        return False
    return "lazy-screenshot" not in (img.get("class") or "") and not img.xpath(
        "ancestor::*[contains(@class,'sp-body')]"
    )


def _translations(lines: list[str]) -> list[str]:
    """Numbered lines after 'Перевод:' — '№ 1: Дублированный | Studio'."""
    return [m.group(1) for line in lines if (m := _TRANSLATION_ITEM.match(line))]


def parse(html_text: str, url: str) -> TorrentMeta:
    root = root_of(html_text)
    descr = root.xpath("//td[@id='descr']")
    if not descr:
        raise ScrapeError("megapeer: td#descr not found (not a topic page?)")
    body = descr[0]
    meta = TorrentMeta(tracker=TRACKER, source_url=url)

    h1 = root.xpath("//h1")
    title = clean(h1[0].text_content()) if h1 else ""
    parsed = parse_title(title)
    meta.title_ru, meta.title_en, meta.year = (
        parsed.title_ru,
        parsed.title_en,
        parsed.year,
    )
    meta.season, meta.episodes = parsed.season, parsed.episodes
    if title:
        meta.extra["title"] = title

    meta.poster_urls = [
        absolute(url, img.get("src") or "")
        for img in body.xpath(".//img")
        if _is_poster(img)
    ][:1]
    meta.screenshots = [
        absolute(url, src)
        for src in body.xpath(".//img[@class='lazy-screenshot']/@data-src")
    ][:MAX_SCREENSHOTS]

    lines = lines_of(body)
    for label, value in labeled(lines):
        apply_field(meta, label, value)
    for item in _translations(lines):
        add_translation(meta, item)
    meta.imdb_id, meta.kp_id = find_ids(lxml.html.tostring(body, encoding="unicode"))
    finalize_av(meta, hint=title)
    if meta.video is None and parsed.source:
        meta.video = " ".join(
            filter(None, [parsed.source, parsed.resolution, parsed.codec])
        )
    return meta
