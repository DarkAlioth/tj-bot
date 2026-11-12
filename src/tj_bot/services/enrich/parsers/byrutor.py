"""Byrutor game page (UTF-8).

Layout: article.main_content → .poster-imgbox img (= og:image), .game_details
h1, .subhnamever version, ul.ul-details (date, developer, genre), .game_desc p
paragraphs, .scrblock a screenshots, .tech_details-item / .tech_details-row
label+value pairs, .info-basictor__label/value.
"""

from lxml.html import HtmlElement

from tj_bot.services.enrich.model import ScrapeError, TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    MAX_SCREENSHOTS,
    absolute,
    apply_field,
    clean,
    parse_year,
    root_of,
    truncate,
)

HOSTS: frozenset[str] = frozenset(
    {
        "byrutgame.org",
        "byrutor.org",
        "byrutdb.org",
        "byrut.org",
        "thebyrut.org",
        "byruthub.org",
    }
)
TRACKER = "byrutor"
_TECH_LABELS = {
    "озвучка": "язык озвучки",
    "интерфейс": "язык интерфейса",
    "тип релиза": "тип издания",
}


def _pairs(
    nodes: list[HtmlElement], label_cls: str, value_cls: str
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for node in nodes:
        label = node.xpath(f".//*[contains(@class,'{label_cls}')]")
        value = node.xpath(f".//*[contains(@class,'{value_cls}')]")
        if label and value:
            name = clean(label[0].text_content()).rstrip(":").lower()
            pairs.append((name, clean(value[0].text_content())))
    return pairs


def parse(html_text: str, url: str) -> TorrentMeta:
    root = root_of(html_text)
    details = root.xpath("//div[contains(@class,'game_details')]")
    if not details:
        raise ScrapeError("byrutor: .game_details not found (not a game page?)")
    block = details[0]
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    h1 = block.xpath(".//h1")
    meta.title_en = clean(h1[0].text_content()) if h1 else None
    version = block.xpath(".//*[contains(@class,'subhnamever')]")
    if version:
        text = clean(version[0].text_content()).replace("[Новая версия]", "")
        meta.extra["version"] = text.strip()

    for li in block.xpath(".//ul[contains(@class,'ul-details')]/li"):
        label = li.xpath(".//*[contains(@class,'ul-details__label')]")
        if not label:
            continue
        label_text = clean(label[0].text_content())
        name = label_text.rstrip(":").lower()
        value = clean(li.text_content()).replace(label_text, "", 1).strip()
        apply_field(meta, name, value)
    meta.genres = [g.lower() for g in meta.genres]
    if meta.year is None and meta.title_en:
        meta.year = parse_year(meta.title_en)

    cover = root.xpath(
        "//div[contains(@class,'poster-imgbox')]//img/@src"
    ) or root.xpath("//meta[@property='og:image']/@content")
    meta.poster_urls = [absolute(url, src) for src in cover[:1]]
    paragraphs = root.xpath("//div[contains(@class,'game_desc')]//p")
    if paragraphs:
        meta.plot = truncate(" ".join(clean(p.text_content()) for p in paragraphs))
    meta.screenshots = [
        absolute(url, href)
        for href in root.xpath("//div[contains(@class,'scrblock')]/a/@href")
    ][:MAX_SCREENSHOTS]

    tech = root.xpath("//div[contains(@class,'tech_details-item')]")
    for label, value in _pairs(tech, "tech_details-label", "tech_details-value"):
        if label == "субтитры":
            meta.extra["subs"] = truncate(value, 40)
        else:
            apply_field(meta, _TECH_LABELS.get(label, label), value)
    minimum = root.xpath(
        "//div[contains(@class,'sysreq__set--min')]//div[contains(@class,'tech_details-row')]"
    )
    for label, value in _pairs(minimum, "tech_details-label", "tech_details-value"):
        apply_field(meta, label, value)
    basics = root.xpath("//div[contains(@class,'info-basictor__info')]")
    for label, value in _pairs(basics, "info-basictor__label", "info-basictor__value"):
        apply_field(meta, _TECH_LABELS.get(label, label), value)

    for key, xpath in (
        (
            "steam_rating",
            "//div[contains(@class,'box-steamrating')]//*[contains(@class,'gr_rate')]",
        ),
        ("size", "//*[contains(@class,'itemdown_size')]"),
        ("hltb", "//div[contains(@class,'ttb')]/@data-v"),
    ):
        node = root.xpath(xpath)
        if node:
            first = node[0]
            meta.extra[key] = clean(
                first if isinstance(first, str) else first.text_content()
            )
    meta.extra.pop("quality", None)
    return meta
