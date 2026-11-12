from unittest.mock import MagicMock

from tj_bot.services.enrichment import CardMeta, NullEnricher


async def test_null_enricher_returns_empty_meta_and_ignores_the_rest() -> None:
    enricher = NullEnricher()
    torrent = MagicMock()
    meta = await enricher.card_meta(torrent, budget=5)
    assert meta == CardMeta()
    assert meta.pending is False
    enricher.prefetch([torrent])
    enricher.on_ready(MagicMock())
