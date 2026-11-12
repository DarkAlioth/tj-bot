"""PC-torrent game page (DLE engine, windows-1251).

Layout: h1.dh1, img.dfullimg1 cover (= og:image), .d7-janr genre links,
#d-id1 + .d8 'Label: value<br/>' block, #d-id2 + .d8img screenshots,
#d-id4 + .d8 system requirements.
"""

import re

from lxml.html import HtmlElement

from tj_bot.services.enrich.model import ScrapeError, TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    MAX_SCREENSHOTS,
    absolute,
    apply_field,
    clean,
    labeled,
    lines_of,
    root_of,
    truncate,
)
from tj_bot.services.enrich.title_parser import parse_title

HOSTS: frozenset[str] = frozenset(
    {"pc-torrents.games", "pc-torrent.org", "pc-torrent.pro"}
)
TRACKER = "pctorrent"
_PLOT_LABELS = ("описание", "об игре", "сюжет")
_YEAR_CATEGORY = re.compile(r"^игры \d{4} года$")


def _block_after(root: HtmlElement, anchor_id: str) -> HtmlElement | None:
    found = root.xpath(
        f"//*[@id='{anchor_id}']/following-sibling::div[contains(@class,'d8')][1]"
    )
    return found[0] if found else None


def _plot_from_lines(lines: list[str]) -> str | None:
    """Text after the 'Описание:' line up to the end of the block, paragraphs joined."""
    for index, line in enumerate(lines):
        label, _, rest = line.partition(":")
        if label.strip().lower() in _PLOT_LABELS:
            return truncate(" ".join([rest.strip(), *lines[index + 1 :]]))
    return None


def parse(html_text: str, url: str) -> TorrentMeta:
    root = root_of(html_text)
    h1 = root.xpath("//h1[contains(@class,'dh1')]") or root.xpath("//h1")
    if not h1:
        raise ScrapeError("pctorrent: h1 not found (not a game page?)")
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    title = clean(h1[0].text_content())
    parsed = parse_title(title)
    meta.title_en, meta.title_ru, meta.year = (
        parsed.title_en,
        parsed.title_ru,
        parsed.year,
    )
    meta.extra["title"] = title
    if parsed.extras:
        meta.extra["edition"] = ", ".join(parsed.extras)

    cover = root.xpath("//img[contains(@class,'dfullimg1')]/@src") or root.xpath(
        "//meta[@property='og:image']/@content"
    )
    meta.poster_urls = [absolute(url, src) for src in cover[:1]]
    genres = (
        clean(a.text_content()).lower()
        for a in root.xpath("//div[contains(@class,'d7-janr')]//a")
    )
    meta.genres = [g for g in genres if not _YEAR_CATEGORY.match(g)]

    description = _block_after(root, "d-id1")
    if description is not None:
        lines = lines_of(description)
        for label, value in labeled(lines):
            if label not in _PLOT_LABELS:
                apply_field(meta, label, value)
        meta.plot = _plot_from_lines(lines)
    screenshots = _block_after(root, "d-id2")
    if screenshots is not None:
        meta.screenshots = [
            absolute(url, href) for href in screenshots.xpath(".//a/@href")
        ][:MAX_SCREENSHOTS]
    requirements = _block_after(root, "d-id4")
    if requirements is not None:
        for label, value in labeled(lines_of(requirements)):
            apply_field(meta, label, value)
    for key, xpath in (
        ("size", "//span[@class='dnrazmer']"),
        ("published", "//span[@class='ddate']"),
    ):
        node = root.xpath(xpath)
        if node:
            meta.extra[key] = clean(node[0].text_content())
    meta.extra.pop("quality", None)
    return meta
