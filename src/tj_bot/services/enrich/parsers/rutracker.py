"""RuTracker topic page: phpBB post, windows-1251, labels in <span class=post-b>."""

from lxml import html

from tj_bot.services.enrich.model import TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    apply_fields,
    fields_of,
    find_ids,
    image_refs,
    lines_of,
    meta_from_title,
    pick_images,
    root_of,
)

HOSTS: frozenset[str] = frozenset({"rutracker.org", "rutracker.net", "rutracker.nl"})
TRACKER = "rutracker"
TITLE_SUFFIX = " :: RuTracker.org"


def parse(html_text: str, url: str) -> TorrentMeta:
    doc = root_of(html_text)
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    titles = doc.xpath('//a[@id="topic-title"]')
    title = titles[0].text_content() if titles else (doc.findtext(".//title") or "")
    meta_from_title(meta, title.removesuffix(TITLE_SUFFIX))
    bodies = doc.xpath('//div[contains(@class, "post_body")]')
    if not bodies:
        return meta
    body = bodies[0]
    meta.poster_urls, meta.screenshots = pick_images(image_refs(body, url))
    apply_fields(meta, fields_of(lines_of(body)))
    meta.imdb_id, meta.kp_id = find_ids(html.tostring(body, encoding="unicode"))
    return meta
