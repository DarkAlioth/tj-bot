"""Enrichment service: scrape tracker pages in the background, cache in PostgreSQL.

A card request returns the cached row at once; otherwise the page fetch is
queued and awaited for at most ``budget`` seconds, after which the card goes
out ``pending`` and the ready callbacks deliver the result later.
"""

import asyncio
import dataclasses
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.config import Settings
from tj_bot.db.meta_repo import MetaRepo, is_expired
from tj_bot.db.models import Torrent, TorrentMetaRow
from tj_bot.services.enrich import flaresolverr, images
from tj_bot.services.enrich.fetch import fetch_page, host_of, is_challenge
from tj_bot.services.enrich.model import (
    ChallengeError,
    LoginRequiredError,
    NotFoundError,
    ScrapeError,
    TorrentMeta,
)
from tj_bot.services.enrich.parsers import parser_for
from tj_bot.services.enrich.parsers.tapochek import HOSTS as TAPOCHEK_HOSTS
from tj_bot.services.enrich.tapochek import TapochekCredentials, fetch_tapochek
from tj_bot.services.enrich.title_parser import parse_title
from tj_bot.services.enrichment import CardMeta, ReadyCallback

logger = logging.getLogger(__name__)

CONCURRENCY = 4
BREAKER_FAILURES = 3
BREAKER_SECONDS = 600
POSTER_TIMEOUT_SECONDS = 5.0
PRIORITY_CARD = 0
PRIORITY_PREFETCH = 1
# TorrentMeta fields the card uses (ids kept for a later rating lookup)
META_KEYS = (
    "title_ru",
    "title_en",
    "year",
    "genres",
    "countries",
    "director",
    "cast",
    "duration_min",
    "plot",
    "translation",
    "subtitles",
    "video",
    "audio",
    "imdb_id",
    "kp_id",
    "season",
    "episodes",
)
# backdrop hue by Torznab category prefix, same table as the search widget
CATEGORY_HUES = (
    ("Movies", 262),
    ("TV", 178),
    ("PC", 36),
    ("Console", 36),
    ("Audio", 322),
    ("Books", 82),
)
DEFAULT_HUE = 212


def category_hue(category: str) -> int:
    for prefix, hue in CATEGORY_HUES:
        if category.startswith(prefix):
            return hue
    return DEFAULT_HUE


@dataclass(frozen=True, slots=True)
class Target:
    """The torrent columns a job needs, detached from the per-update session."""

    hash: str
    title: str
    tracker: str
    details_url: str
    category: str

    @classmethod
    def of(cls, torrent: Torrent) -> "Target":
        return cls(
            hash=torrent.hash,
            title=torrent.title,
            tracker=torrent.tracker,
            details_url=torrent.details_url,
            category=torrent.category,
        )


@dataclass(slots=True)
class _Job:
    target: Target
    started: bool = False
    result: CardMeta | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class HostBreaker:
    """Per-host circuit breaker: open for a while after consecutive failures."""

    def __init__(
        self, failures: int = BREAKER_FAILURES, open_seconds: float = BREAKER_SECONDS
    ) -> None:
        self._threshold = failures
        self._open_seconds = open_seconds
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, host: str) -> bool:
        return self._open_until.get(host, 0.0) > time.monotonic()

    def success(self, host: str) -> None:
        self._failures.pop(host, None)

    def failure(self, host: str) -> None:
        count = self._failures.get(host, 0) + 1
        if count < self._threshold:
            self._failures[host] = count
            return
        self._failures.pop(host, None)
        self._open_until[host] = time.monotonic() + self._open_seconds
        logger.warning(
            "Enrichment paused for %s after %s failures", host, self._threshold
        )


def base_card(target: Target, *, pending: bool = False) -> CardMeta:
    """What the Jackett title alone gives: titles, year and quality chips."""
    parsed = parse_title(target.title)
    return CardMeta(
        title_ru=parsed.title_ru,
        title_en=parsed.title_en,
        year=parsed.year,
        chips=tuple(parsed.chips),
        pending=pending,
    )


def card_from_row(target: Target, row: TorrentMetaRow) -> CardMeta:
    parsed = parse_title(target.title)
    meta = row.meta if row.status == "ok" else {}
    return CardMeta(
        title_ru=meta.get("title_ru") or parsed.title_ru,
        title_en=meta.get("title_en") or parsed.title_en,
        year=meta.get("year") or parsed.year,
        chips=tuple(parsed.chips),
        genres=tuple(meta.get("genres") or ()),
        countries=tuple(meta.get("countries") or ()),
        duration_min=meta.get("duration_min"),
        director=meta.get("director"),
        cast=tuple(meta.get("cast") or ()),
        translation=tuple(meta.get("translation") or ()),
        subtitles=tuple(meta.get("subtitles") or ()),
        plot=meta.get("plot"),
        mosaic=row.mosaic,
        poster_file_id=row.poster_file_id,
        poster_bytes=row.poster_bytes,
    )


def meta_json(meta: TorrentMeta) -> dict[str, object]:
    data = dataclasses.asdict(meta)
    return {key: data[key] for key in META_KEYS}


def _poster_title(target: Target, meta: TorrentMeta | None) -> str:
    if meta is not None and (meta.title_ru or meta.title_en):
        return meta.title_ru or meta.title_en or target.title
    parsed = parse_title(target.title)
    return parsed.title_ru or parsed.title_en or target.title


class EnrichmentService:
    """``Enricher`` backed by workers, one aiohttp session and ``torrent_meta``."""

    def __init__(
        self,
        session_pool: async_sessionmaker[AsyncSession],
        *,
        proxy: str | None = None,
        flaresolverr_url: str | None = None,
        tapochek: TapochekCredentials | None = None,
        fetch_timeout_seconds: float = 20.0,
        concurrency: int = CONCURRENCY,
        breaker: HostBreaker | None = None,
    ) -> None:
        self._pool = session_pool
        self._proxy = proxy
        self._flaresolverr_url = flaresolverr_url
        self._tapochek = tapochek
        self._timeout = fetch_timeout_seconds
        self._concurrency = concurrency
        self._breaker = breaker or HostBreaker()
        self._session: aiohttp.ClientSession | None = None
        self._queue: asyncio.PriorityQueue[tuple[int, int, str]] = (
            asyncio.PriorityQueue()
        )
        self._seq = 0
        self._jobs: dict[str, _Job] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._callbacks: list[ReadyCallback] = []
        # pinned to the FlareSolverr browser after a solve so its cookies stay valid
        self._user_agent: str | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=8), cookie_jar=aiohttp.CookieJar()
        )
        self._workers = [
            asyncio.create_task(self._worker()) for _ in range(self._concurrency)
        ]

    async def close(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # --- Enricher protocol -------------------------------------------------

    def on_ready(self, callback: ReadyCallback) -> None:
        self._callbacks.append(callback)

    async def card_meta(self, torrent: Torrent, *, budget: float = 0.0) -> CardMeta:
        target = Target.of(torrent)
        row = await self._load_row(target.hash)
        if row is not None and not is_expired(row):
            return card_from_row(target, row)
        job = self._enqueue(target, PRIORITY_CARD if budget > 0 else PRIORITY_PREFETCH)
        if budget > 0:
            try:
                await asyncio.wait_for(asyncio.shield(job.done.wait()), budget)
            except TimeoutError:
                pass
        return job.result or base_card(target, pending=True)

    def prefetch(self, torrents: Sequence[Torrent]) -> None:
        for torrent in torrents:
            self._enqueue(Target.of(torrent), PRIORITY_PREFETCH)

    async def remember_poster(self, torrent_hash: str, file_id: str) -> None:
        async with self._pool() as session:
            await MetaRepo(session).set_poster_file_id(torrent_hash, file_id)
            await session.commit()

    # --- queue ---------------------------------------------------------------

    def _enqueue(self, target: Target, priority: int) -> _Job:
        job = self._jobs.get(target.hash)
        if job is None:
            job = _Job(target)
            self._jobs[target.hash] = job
        elif job.started or priority != PRIORITY_CARD:
            return job
        # a card request re-queues a waiting prefetch at card priority
        self._seq += 1
        self._queue.put_nowait((priority, self._seq, target.hash))
        return job

    async def _worker(self) -> None:
        while True:
            _, _, torrent_hash = await self._queue.get()
            job = self._jobs.get(torrent_hash)
            if job is None or job.started:
                continue
            job.started = True
            try:
                job.result = await self._process(job.target)
            except Exception:  # a bug in one job must not kill the worker
                logger.exception("Enrichment of %s crashed", torrent_hash)
                job.result = base_card(job.target)
            finally:
                self._jobs.pop(torrent_hash, None)
                job.done.set()
            await self._notify(torrent_hash, job.result)

    async def _notify(self, torrent_hash: str, meta: CardMeta) -> None:
        for callback in self._callbacks:
            try:
                await callback(torrent_hash, meta)
            except Exception:  # one failing listener must not starve the others
                logger.exception("Enrichment callback failed for %s", torrent_hash)

    # --- pipeline ------------------------------------------------------------

    async def _load_row(self, torrent_hash: str) -> TorrentMetaRow | None:
        async with self._pool() as session:
            return await MetaRepo(session).get(torrent_hash)

    async def _process(self, target: Target) -> CardMeta:
        row = await self._load_row(target.hash)
        if row is not None and not is_expired(row):
            return card_from_row(target, row)
        host = host_of(target.details_url)
        if self._breaker.is_open(host):
            # nothing is stored: the card is retried once the breaker closes
            return base_card(target)
        status, meta, error = await self._scrape(target, host)
        poster, mosaic = await self._poster(target, meta)
        async with self._pool() as session:
            row = await MetaRepo(session).upsert(
                torrent_hash=target.hash,
                tracker=target.tracker,
                details_url=target.details_url,
                status=status,
                meta=meta_json(meta) if meta is not None else {},
                poster_bytes=poster,
                mosaic=mosaic,
                error=error,
            )
            await session.commit()
        logger.info("Enriched %s (%s): %s", target.hash[:8], host, status)
        return card_from_row(target, row)

    async def _scrape(
        self, target: Target, host: str
    ) -> tuple[str, TorrentMeta | None, str | None]:
        """Fetch and parse the details page; the status names what went wrong."""
        parser = parser_for(target.details_url)
        if parser is None:
            return "unsupported", None, None
        try:
            page = await self._fetch(target.details_url, host)
            meta = parser(page, target.details_url)
        except NotFoundError as exc:
            return "not_found", None, str(exc)[:200]
        except LoginRequiredError as exc:
            return "login", None, str(exc)[:200]
        except ChallengeError as exc:
            self._breaker.failure(host)
            return "challenge", None, str(exc)[:200]
        except ScrapeError as exc:
            self._breaker.failure(host)
            return "error", None, str(exc)[:200]
        self._breaker.success(host)
        return "ok", meta, None

    async def _fetch(self, url: str, host: str) -> str:
        session = self._require_session()
        if host in TAPOCHEK_HOSTS:
            return await fetch_tapochek(
                session,
                url,
                self._tapochek,
                proxy=self._proxy,
                timeout_seconds=self._timeout,
            )
        try:
            return await fetch_page(
                session,
                url,
                proxy=self._proxy,
                timeout_seconds=self._timeout,
                user_agent=self._user_agent,
            )
        except ChallengeError:
            if not self._flaresolverr_url:
                raise
            return await self._solve(session, url)

    async def _solve(self, session: aiohttp.ClientSession, url: str) -> str:
        try:
            solution = await flaresolverr.solve(
                session, self._flaresolverr_url or "", url, proxy=self._proxy
            )
        except ScrapeError as exc:
            msg = f"flaresolverr failed for {url}: {exc}"
            raise ChallengeError(msg) from exc
        self._user_agent = solution.user_agent or self._user_agent
        if is_challenge(solution.status, {}, solution.html):
            msg = f"flaresolverr returned a challenge page for {url}"
            raise ChallengeError(msg)
        return solution.html

    async def _poster(
        self, target: Target, meta: TorrentMeta | None
    ) -> tuple[bytes, str | None]:
        """Prepared JPEG + mosaic: poster, else first screenshot, else a cover."""
        candidates = meta.poster_urls[:1] + meta.screenshots[:1] if meta else []
        for url in candidates:
            try:
                raw = await images.download_image(
                    self._require_session(),
                    url,
                    proxy=self._proxy,
                    retries=2,
                    referer=target.details_url,
                    timeout_seconds=POSTER_TIMEOUT_SECONDS,
                    user_agent=self._user_agent,
                )
                return await asyncio.to_thread(_prepare, raw)
            except ScrapeError as exc:
                logger.info("Poster %s unusable: %s", url, exc)
        cover = await asyncio.to_thread(
            images.generated_cover,
            _poster_title(target, meta),
            category_hue(target.category),
        )
        return cover, None

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("EnrichmentService is not started")
        return self._session


def _prepare(raw: bytes) -> tuple[bytes, str]:
    return images.prepare_photo(raw), images.mosaic(raw)


def build_enricher(
    settings: Settings, session_pool: async_sessionmaker[AsyncSession]
) -> EnrichmentService | None:
    """The service per settings, or None when enrichment is switched off."""
    if not settings.enrich_enabled:
        logger.info("Search-card enrichment disabled")
        return None
    creds = None
    if settings.tapochek_username and settings.tapochek_password:
        creds = TapochekCredentials(
            settings.tapochek_username, settings.tapochek_password
        )
    return EnrichmentService(
        session_pool,
        proxy=settings.scrape_proxy_url,
        flaresolverr_url=settings.flaresolverr_url or None,
        tapochek=creds,
        fetch_timeout_seconds=settings.enrich_fetch_timeout_seconds,
    )
