"""NoNaMe Club topic page: phpBB post with bold labels and <var class=postImg>."""

from lxml import html

from tj_bot.services.enrich.model import TorrentMeta
from tj_bot.services.enrich.parsers.common import (
    apply_fields,
    dedupe,
    fields_of,
    find_ids,
    image_refs,
    lines_of,
    meta_from_title,
    pick_images,
    root_of,
)

HOSTS: frozenset[str] = frozenset(
    {"nnmclub.to", "nnmclub.ro", "nnm-club.me", "nnmclub.name"}
)
TRACKER = "nnmclub"
TITLE_SUFFIX = " :: NNM-Club"


def parse(html_text: str, url: str) -> TorrentMeta:
    doc = root_of(html_text)
    meta = TorrentMeta(tracker=TRACKER, source_url=url)
    titles = doc.xpath('//a[@class="maintitle"]')
    title = titles[0].text_content() if titles else (doc.findtext(".//title") or "")
    meta_from_title(meta, title.removesuffix(TITLE_SUFFIX))
    bodies = doc.xpath('//div[contains(@class, "postbody")]')
    if not bodies:
        return meta
    body = bodies[0]
    posters, meta.screenshots = pick_images(image_refs(body, url))
    # the post embeds its own og:image with the exact poster
    og_image = body.xpath('.//meta[@property="og:image"]/@content')
    meta.poster_urls = dedupe([str(og_image[0])] + posters if og_image else posters)[:2]
    apply_fields(meta, fields_of(lines_of(body)))
    meta.imdb_id, meta.kp_id = find_ids(html.tostring(body, encoding="unicode"))
    return meta
