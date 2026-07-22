import json
import logging
from types import TracebackType
from typing import Any, Self

import aiohttp

logger = logging.getLogger(__name__)


class QbittorrentError(Exception):
    """A qBittorrent Web API call failed."""


class QbittorrentClient:
    """Minimal async client for the qBittorrent Web API (v2)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # qBittorrent validates the Referer header against its own origin.
            # unsafe cookie jar: aiohttp drops cookies for IP-literal hosts by
            # default, which would silently discard the auth session cookie.
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"Referer": self._base_url},
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
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

    async def login(self) -> None:
        url = f"{self._base_url}/api/v2/auth/login"
        data = {"username": self._username, "password": self._password}
        try:
            async with self._get_session().post(url, data=data) as resp:
                body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            msg = f"qBittorrent login failed: {exc!r}"
            raise QbittorrentError(msg) from exc
        if body.strip() != "Ok.":
            raise QbittorrentError("qBittorrent rejected the credentials")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: aiohttp.FormData | dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> str:
        """Call an endpoint, re-authenticating once if the session expired."""
        url = f"{self._base_url}{path}"
        for attempt in range(2):
            try:
                async with self._get_session().request(
                    method, url, data=data, params=params
                ) as resp:
                    if resp.status == 403 and attempt == 0:
                        await self.login()
                        continue
                    if resp.status != 200:
                        msg = f"{path} returned HTTP {resp.status}"
                        raise QbittorrentError(msg)
                    return await resp.text()
            except (aiohttp.ClientError, TimeoutError) as exc:
                msg = f"{path} failed: {exc!r}"
                raise QbittorrentError(msg) from exc
        msg = f"{path} failed after re-authentication"
        raise QbittorrentError(msg)

    async def add_torrent_file(
        self,
        content: bytes,
        filename: str,
        tag: str,
        category: str | None = None,
        paused: bool = False,
    ) -> None:
        form = aiohttp.FormData()
        form.add_field(
            "torrents",
            content,
            filename=filename,
            content_type="application/x-bittorrent",
        )
        form.add_field("tags", tag)
        if category:
            form.add_field("category", category)
        # "stopped" is the WebAPI 2.11+ name; older builds ignore the extra field
        form.add_field("stopped", str(paused).lower())
        body = await self._request("POST", "/api/v2/torrents/add", data=form)
        if body.strip().lower() != "ok.":
            raise QbittorrentError("qBittorrent rejected the torrent")

    async def torrents_by_tag(self, tag: str) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v2/torrents/info", params={"tag": tag})
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            msg = "qBittorrent returned invalid torrent list"
            raise QbittorrentError(msg) from exc
        if not isinstance(parsed, list):
            raise QbittorrentError("Unexpected torrent list payload")
        return parsed

    async def delete_by_tag(self, tag: str, delete_files: bool = True) -> None:
        torrents = await self.torrents_by_tag(tag)
        hashes = "|".join(t["hash"] for t in torrents if t.get("hash"))
        if not hashes:
            return
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": hashes, "deleteFiles": str(delete_files).lower()},
        )
