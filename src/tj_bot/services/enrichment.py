"""Interface between the widgets and the tracker-page enrichment service.

The search card is sent with whatever is cached; a fetch that is still
running sets ``pending`` and the enricher calls the ready callback later so
the card's chat_data can be edited in place.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from tj_bot.db.models import Torrent


@dataclass(frozen=True, slots=True)
class CardMeta:
    """Metadata for one torrent card; every field is optional."""

    title_ru: str | None = None
    title_en: str | None = None
    year: int | None = None
    # quality chips from the title («2160p», «HDR10», «BDRemux», «S1E1-6»), ≤ 5
    chips: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    duration_min: int | None = None
    director: str | None = None
    cast: tuple[str, ...] = ()
    translation: tuple[str, ...] = ()
    subtitles: tuple[str, ...] = ()
    plot: str | None = None
    # 4×6 ambient mosaic, 72 hex chars (12-bit rgb per cell, row-major)
    mosaic: str | None = None
    # Telegram file_id once the poster was sent at least once
    poster_file_id: str | None = None
    # prepared JPEG for the first sendPhoto; dropped once a file_id is known
    poster_bytes: bytes | None = None
    # True while the tracker page is still being fetched in the background
    pending: bool = False


ReadyCallback = Callable[[str, CardMeta], Awaitable[None]]


class Enricher(Protocol):
    """What the handlers need from the enrichment service."""

    async def card_meta(self, torrent: Torrent, *, budget: float = 0.0) -> CardMeta:
        """Metadata for ``torrent``: cached, or fetched within ``budget`` seconds.

        With ``budget`` 0 nothing is awaited; a fetch is scheduled and the
        result comes back through the ready callback, ``pending`` is True.
        """

    def prefetch(self, torrents: Sequence[Torrent]) -> None:
        """Schedule background fetches (the next cards of a result set)."""

    async def remember_poster(self, torrent_hash: str, file_id: str) -> None:
        """Store the file_id after the first successful sendPhoto."""

    def on_ready(self, callback: ReadyCallback) -> None:
        """Register the callback fired with ``(torrent_hash, meta)`` after a fetch."""


class NullEnricher:
    """Enrichment disabled: cards render from Jackett data only."""

    async def card_meta(self, torrent: Torrent, *, budget: float = 0.0) -> CardMeta:
        return CardMeta()

    def prefetch(self, torrents: Sequence[Torrent]) -> None:
        return None

    async def remember_poster(self, torrent_hash: str, file_id: str) -> None:
        return None

    def on_ready(self, callback: ReadyCallback) -> None:
        return None
