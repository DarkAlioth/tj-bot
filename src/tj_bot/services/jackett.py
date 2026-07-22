import hashlib
import html
import logging
import re
from types import TracebackType
from typing import Any, Self

import aiohttp
from dateutil import parser as date_parser

from tj_bot.db.repo import TorrentData

logger = logging.getLogger(__name__)


class JackettError(Exception):
    """Search or download against Jackett failed."""


class DownloadTooLargeError(JackettError):
    """The tracker returned a file above the configured size cap."""


def parse_result(torrent: dict[str, Any]) -> TorrentData | None:
    """Convert one Jackett result to TorrentData; None when not downloadable."""
    if torrent.get("Link") is None:
        return None
    title = html.escape(torrent.get("Title") or "None")
    description: str | None = torrent.get("Description") or None
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
    description = html.escape(description) if description else None
    uploader = html.escape(uploader) if uploader else None
    published_at = date_parser.isoparse(torrent["PublishDate"]).date()
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
        category=torrent.get("CategoryDesc") or "None",
        details_url=torrent.get("Details") or "None",
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

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def search(self, query: str) -> list[TorrentData]:
        url = f"{self._base_url}/api/v2.0/indexers/all/results"
        # Query is quoted to keep the legacy phrase-search behavior;
        # aiohttp percent-encodes all parameter values safely.
        params = {"apikey": self._api_key, "Query": f'"{query}"'}
        try:
            async with self._get_session().get(url, params=params) as resp:
                if resp.status != 200:
                    msg = f"Jackett search returned HTTP {resp.status}"
                    raise JackettError(msg)
                payload = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            msg = f"Jackett search failed: {exc!r}"
            raise JackettError(msg) from exc
        except ValueError as exc:
            msg = "Jackett search returned invalid JSON"
            raise JackettError(msg) from exc
        results = payload.get("Results") or []
        parsed = [item for item in map(parse_result, results) if item is not None]
        logger.info(
            "Jackett search %r: %s results, %s downloadable",
            query,
            len(results),
            len(parsed),
        )
        return parsed

    async def download(self, url: str) -> bytes:
        """Fetch a .torrent file enforcing the size cap."""
        try:
            async with self._get_session().get(url) as resp:
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
