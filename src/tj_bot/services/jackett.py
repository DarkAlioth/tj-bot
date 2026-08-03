import asyncio
import datetime
import hashlib
import html
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from dateutil import parser as date_parser

from tj_bot.db.repo import TorrentData

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 200
MAX_DESCRIPTION_CHARS = 500
# cap applied before any regex work: the uploader patterns backtrack across
# the whole string, and a tracker can return a multi-hundred-KB description
MAX_RAW_DESCRIPTION_CHARS = 4096
# one retry on transient connection failures (not timeouts: those are slow)
RETRY_DELAY_SECONDS = 2.0
# .torrent files are tiny; do not let a download wait out the long search
# timeout the session is configured with
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.\- ]", flags=re.UNICODE)
# Jackett indexer ids are ASCII slugs; a leading alphanumeric also rules out
# ".." path segments (yarl decodes %2F back, so quoting cannot be trusted)
_INDEXER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def safe_torrent_filename(title: str) -> str:
    """Build a safe .torrent filename from a torrent title.

    Strips path separators, control characters and traversal sequences down to
    an allow-list, so the name is inert wherever it is used (multipart field,
    Telegram document name).
    """
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", title).strip(" ._")
    cleaned = cleaned[:120] or "torrent"
    return f"{cleaned}.torrent"


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


class JackettError(Exception):
    """Search or download against Jackett failed."""


class DownloadTooLargeError(JackettError):
    """The tracker returned a file above the configured size cap."""


class UntrustedDownloadError(JackettError):
    """The download URL points outside the trusted Jackett origin (SSRF guard)."""


def parse_result(torrent: dict[str, Any]) -> TorrentData | None:
    """Convert one Jackett result to TorrentData.

    Returns None when the result is not downloadable or cannot be parsed — a
    single malformed result (bad date, non-numeric size, missing field) must
    never abort the whole search batch.
    """
    try:
        return _parse_result(torrent)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        logger.warning("Skipping unparseable result %r: %r", torrent.get("Title"), exc)
        return None


def _parse_result(torrent: dict[str, Any]) -> TorrentData | None:
    if torrent.get("Link") is None:
        return None
    title = html.escape(torrent.get("Title") or "None")
    description: str | None = torrent.get("Description") or None
    if description and len(description) > MAX_RAW_DESCRIPTION_CHARS:
        description = description[:MAX_RAW_DESCRIPTION_CHARS]
    uploader: str | None = None
    if description:
        match = re.search(r"Uploader:\s*(\S+)", description)
        if match:
            uploader = match.group(1)
            description = re.sub(
                r"Uploader:\s*[\s\S]*?<br\s*/?>|[\s\S]*?<br\s*/?>", "", description
            )
        if not uploader:
            match = re.search(r"([^\s<]+)(?=\s*<br>)", description)
            if match:
                uploader = match.group(1)
    if description and len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip() + "…"
    description = html.escape(description) if description else None
    uploader = html.escape(uploader) if uploader else None
    raw_date = torrent.get("PublishDate")
    published_at = (
        date_parser.isoparse(raw_date).date() if raw_date else datetime.date.today()
    )
    tracker_id = torrent.get("TrackerId") or "None"
    raw_hash = f"{title}{tracker_id}{published_at}"
    torrent_hash = hashlib.md5(
        raw_hash.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return TorrentData(
        hash=torrent_hash,
        title=title,
        uploader=uploader,
        description=description,
        # escaped like title/description: these render inside HTML messages
        category=html.escape(torrent.get("CategoryDesc") or "None"),
        tracker=html.escape(tracker_id),
        details_url=html.escape(torrent.get("Details") or "None"),
        download_url=torrent["Link"],
        seeders=torrent.get("Seeders") or 0,
        peers=torrent.get("Peers") or 0,
        published_at=published_at,
        size=int(torrent.get("Size") or 0),
    )


class JackettClient:
    """Thin async client over the Jackett aggregate search API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 90,
        download_max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._download_max_bytes = download_max_bytes
        self._session: aiohttp.ClientSession | None = None
        self._observed_errors: dict[str, str] = {}

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # bound concurrent outbound requests to protect Jackett and trackers
            connector = aiohttp.TCPConnector(limit=8)
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, connector=connector
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _is_trusted_url(self, url: str) -> bool:
        """Only URLs on the configured Jackett origin may be fetched.

        Jackett proxies every download through its own ``/dl`` endpoint, so a
        legitimate ``Link`` always shares Jackett's scheme/host/port. Rejecting
        anything else closes the SSRF vector where a hostile indexer returns a
        ``Link`` aimed at an internal service.
        """
        base = urlsplit(self._base_url)
        target = urlsplit(url)
        return (
            target.scheme in ("http", "https")
            and target.scheme == base.scheme
            and target.hostname == base.hostname
            and (target.port or _default_port(target.scheme))
            == (base.port or _default_port(base.scheme))
        )

    async def _fetch_json(
        self, url: str, params: dict[str, str], what: str
    ) -> dict[str, Any]:
        """GET returning parsed JSON; one retry on transient connection drops."""
        for attempt in range(2):
            try:
                async with self._get_session().get(url, params=params) as resp:
                    if resp.status != 200:
                        msg = f"Jackett {what} returned HTTP {resp.status}"
                        raise JackettError(msg)
                    payload = await resp.json()
            except aiohttp.ClientConnectionError as exc:
                if attempt == 0:
                    logger.warning(
                        "Jackett %s connection failed, retrying: %r", what, exc
                    )
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                msg = f"Jackett {what} failed: {exc!r}"
                raise JackettError(msg) from exc
            except (aiohttp.ClientError, TimeoutError) as exc:
                msg = f"Jackett {what} failed: {exc!r}"
                raise JackettError(msg) from exc
            except ValueError as exc:
                msg = f"Jackett {what} returned invalid JSON"
                raise JackettError(msg) from exc
            if not isinstance(payload, dict):
                msg = f"Jackett {what} returned unexpected payload"
                raise JackettError(msg)
            return payload
        msg = f"Jackett {what} failed after retry"
        raise JackettError(msg)

    def observed_errors(self) -> dict[str, str]:
        """Indexer failures noticed during real searches: id -> display name."""
        return dict(self._observed_errors)

    def take_observed_errors(self) -> dict[str, str]:
        """Consume the observed failures (the monitor picks them up once)."""
        observed, self._observed_errors = self._observed_errors, {}
        return observed

    def _record_observed_errors(self, payload: dict[str, Any]) -> None:
        for indexer in payload.get("Indexers") or []:
            if isinstance(indexer, dict) and indexer.get("Error"):
                idx = str(indexer.get("ID") or "")
                if idx:
                    self._observed_errors[idx] = str(indexer.get("Name") or idx)

    async def search(self, query: str) -> list[TorrentData]:
        url = f"{self._base_url}/api/v2.0/indexers/all/results"
        # Query is quoted to keep the legacy phrase-search behavior;
        # aiohttp percent-encodes all parameter values safely.
        params = {"apikey": self._api_key, "Query": f'"{query[:MAX_QUERY_LENGTH]}"'}
        payload = await self._fetch_json(url, params, "search")
        # every search reports per-indexer health for free — feed the monitor
        self._record_observed_errors(payload)
        results = payload.get("Results") or []
        parsed = [item for item in map(parse_result, results) if item is not None]
        logger.info(
            "Jackett search %r: %s results, %s downloadable",
            query,
            len(results),
            len(parsed),
        )
        return parsed

    async def indexers(self) -> list[dict[str, Any]]:
        """Indexer health from the search endpoint's ``Indexers`` array.

        The management endpoint (``/api/v2.0/indexers``) requires a dashboard
        session and only redirects for an API key, so probe the results
        endpoint — which accepts the key and reports each queried indexer's
        name, error and hit count — with an empty query.
        """
        url = f"{self._base_url}/api/v2.0/indexers/all/results"
        params = {"apikey": self._api_key, "Query": ""}
        payload = await self._fetch_json(url, params, "indexers")
        indexers = payload.get("Indexers")
        if not isinstance(indexers, list):
            raise JackettError("Unexpected indexers payload")
        return indexers

    async def probe_indexer(self, indexer_id: str) -> str | None:
        """Re-test one indexer; returns its error text, or None when healthy.

        Querying an indexer makes Jackett re-attempt it, so the probe doubles
        as the recovery action after transient failures (expired session,
        tracker hiccup) without touching the healthy indexers.
        """
        if not _INDEXER_ID.fullmatch(indexer_id):
            logger.warning("Refusing to probe suspicious indexer id %r", indexer_id)
            return "invalid indexer id"
        url = f"{self._base_url}/api/v2.0/indexers/{indexer_id}/results"
        params = {"apikey": self._api_key, "Query": ""}
        payload = await self._fetch_json(url, params, f"probe {indexer_id}")
        for indexer in payload.get("Indexers") or []:
            if isinstance(indexer, dict) and str(indexer.get("ID") or "") == indexer_id:
                error = indexer.get("Error")
                return str(error) if error else None
        return None

    async def download(self, url: str) -> bytes:
        """Fetch a .torrent file from Jackett, enforcing origin and size caps."""
        if not self._is_trusted_url(url):
            logger.warning("Blocked download from untrusted URL: %r", url)
            raise UntrustedDownloadError
        try:
            async with self._get_session().get(url, timeout=DOWNLOAD_TIMEOUT) as resp:
                if resp.status != 200:
                    msg = f"Download returned HTTP {resp.status}"
                    raise JackettError(msg)
                if (
                    resp.content_length is not None
                    and resp.content_length > self._download_max_bytes
                ):
                    raise DownloadTooLargeError
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    received += len(chunk)
                    if received > self._download_max_bytes:
                        raise DownloadTooLargeError
                    chunks.append(chunk)
                return b"".join(chunks)
        except (aiohttp.ClientError, TimeoutError) as exc:
            msg = f"Download failed: {exc!r}"
            raise JackettError(msg) from exc
