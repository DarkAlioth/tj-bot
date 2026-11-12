"""Tapochek topic page: phpBB post, UTF-8; video sections are members-only."""

from lxml import html

from tj_bot.services.enrich.model import LoginRequiredError, TorrentMeta
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

HOSTS: frozenset[str] = frozenset({"tapochek.net"})
TRACKER = "tapochek"
# the full login form, not the header quick-login box every page carries
LOGIN_WALL_MARKERS = ('name="login_username"', 'name="redirect"')


def needs_login(html_text: str) -> bool:
    """True when the page is tapochek's login wall (members-only topic, guest)."""
    return all(marker in html_text for marker in LOGIN_WALL_MARKERS)


def parse(html_text: str, url: str) -> TorrentMeta:
    doc = root_of(html_text)
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    bodies = doc.xpath('//div[contains(@class, "post_body")]')
    if not bodies:
        if needs_login(html_text):
            msg = f"tapochek: login required for {url}"
            raise LoginRequiredError(msg)
        return meta
    titles = doc.xpath("//h1//a")
    meta_from_title(
        meta, titles[0].text_content() if titles else doc.findtext(".//title")
    )
    body = bodies[0]
    meta.poster_urls, meta.screenshots = pick_images(image_refs(body, url))
    apply_fields(meta, fields_of(lines_of(body)))
    meta.imdb_id, meta.kp_id = find_ids(html.tostring(body, encoding="unicode"))
    return meta
