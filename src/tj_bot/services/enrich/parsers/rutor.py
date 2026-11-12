"""RuTor page: <h1> title, <table id=details> description cell, header rows."""

from lxml import html
from lxml.html import HtmlElement

from tj_bot.services.enrich.model import TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    apply_fields,
    clean,
    fields_of,
    find_ids,
    image_refs,
    lines_of,
    meta_from_title,
    pick_images,
    root_of,
)

HOSTS: frozenset[str] = frozenset({"rutor.info", "rutor.is", "rutor.org", "6tor.org"})
TRACKER = "rutor"
HEADER_KEYS = {"Категория": "category", "Размер": "size"}


def parse(html_text: str, url: str) -> TorrentMeta:
    doc = root_of(html_text)
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    headings = doc.xpath("//h1")
    meta_from_title(meta, headings[0].text_content() if headings else None)
    tables = doc.xpath('//table[@id="details"]')
    if not tables:
        return meta
    table: HtmlElement = tables[0]
    cells = table.xpath("./tr[1]/td[2]")
    if cells:
        desc: HtmlElement = cells[0]
        refs = image_refs(desc, url)
        # collapsed screenshot block: raw HTML inside a <textarea>
        for area in desc.xpath(".//textarea"):
            if area.text:
                refs.extend(image_refs(root_of(area.text), url))
        meta.poster_urls, meta.screenshots = pick_images(refs)
        apply_fields(meta, fields_of(lines_of(desc)))
        meta.imdb_id, meta.kp_id = find_ids(html.tostring(desc, encoding="unicode"))
    for row in table.xpath('./tr[td[@class="header"]]'):
        key = clean(row.xpath("./td[1]")[0].text_content())
        values = row.xpath("./td[2]")
        value = clean(values[0].text_content()) if values else ""
        if key in HEADER_KEYS and value:
            meta.extra[HEADER_KEYS[key]] = _header_value(key, value)
    return meta


def _header_value(key: str, value: str) -> str:
    if key == "Размер":
        return value.split("(")[0].strip()[:20]
    return value[:40]
