"""Polite HTML fetching: per-host throttle, retries with backoff, charset handling."""

import asyncio
import random
import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import aiohttp

from tj_bot.services.enrich.model import ChallengeError, NotFoundError, ScrapeError

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130 Safari/537.36"
)
DEFAULT_HEADERS: Mapping[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en",
}
MIN_INTERVAL = 1.0  # seconds between two requests to the same host
MAX_BACKOFF = 4.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
RETRY_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
)
_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "cf_chl_",
    "_cf_chl_opt",
    "challenge-platform",
    "<title>just a moment",
    "checking your browser",
    "attention required! | cloudflare",
    "ddos-guard",
    "__ddg",
)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?\s*([a-zA-Z0-9_\-]+)", re.I)


class HostThrottle:
    """Serialises requests per host and enforces a minimum interval between them."""

    def __init__(self, min_interval: float = MIN_INTERVAL) -> None:
        self._min_interval = min_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._last.get(host, 0.0) + self._min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


# pages and images of every tracker share one limiter: 1 request per second per host
throttle = HostThrottle()


def backoff_delay(attempt: int) -> float:
    """0.5 s, 1 s, 2 s … capped at MAX_BACKOFF, plus up to 25 % jitter."""
    base = min(MAX_BACKOFF, 0.5 * 2.0 ** (attempt - 1))
    return base + random.uniform(0, base / 4)  # noqa: S311  # nosec B311  # jitter only


def check_proxy(proxy: str | None) -> None:
    """Only http(s) proxies can be passed per request; socks needs a connector."""
    if proxy and not proxy.startswith(("http://", "https://")):
        msg = "SCRAPE_PROXY_URL must be an http:// or https:// proxy"
        raise ValueError(msg)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def decode_body(data: bytes, header_charset: str | None) -> str:
    """Decode with the header charset, else <meta charset>, else UTF-8, else cp1251."""
    charset = header_charset
    if not charset:
        match = _META_CHARSET.search(data[:8192])
        charset = match.group(1).decode("ascii") if match else None
    if charset:
        try:
            return data.decode(charset.lower(), errors="replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def is_challenge(status: int, headers: Mapping[str, str], text: str) -> bool:
    if headers.get("cf-mitigated") == "challenge":
        return True
    head = text[:20000].lower()
    if status == 403 and "cf-ray" in headers:
        return True
    return any(marker in head for marker in _CHALLENGE_MARKERS) and (
        status in (403, 429, 503) or "<title>just a moment" in head
    )


def request_headers(user_agent: str | None = None) -> dict[str, str]:
    """Browser-like defaults; the user agent is pinned after a FlareSolverr solve."""
    headers = dict(DEFAULT_HEADERS)
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    *,
    proxy: str | None = None,
    retries: int = 3,
    timeout_seconds: float = 20.0,
    user_agent: str | None = None,
) -> str:
    """Fetch an HTML page; ``retries`` is the total number of attempts.

    Retries on timeouts, connection errors, 5xx and 429; never on 404
    (NotFoundError) or on an anti-bot challenge (ChallengeError: the caller
    goes to FlareSolverr).
    """
    check_proxy(proxy)
    host = host_of(url) or url
    headers = request_headers(user_agent)
    client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    last_error = "no attempts"
    for attempt in range(1, retries + 1):
        await throttle.wait(host)
        try:
            async with session.get(
                url, headers=headers, proxy=proxy, timeout=client_timeout
            ) as resp:
                data = await resp.read()
                text = decode_body(data, resp.charset)
                if is_challenge(resp.status, resp.headers, text):
                    msg = f"anti-bot challenge at {url} ({resp.status})"
                    raise ChallengeError(msg)
                if resp.status in (404, 410):
                    msg = f"{resp.status} for {url}"
                    raise NotFoundError(msg)
                if resp.status < 300:
                    return text
                if resp.status not in RETRY_STATUSES:
                    msg = f"HTTP {resp.status} for {url}"
                    raise ScrapeError(msg)
                last_error = f"HTTP {resp.status}"
        except RETRY_ERRORS as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            await asyncio.sleep(backoff_delay(attempt))
    msg = f"giving up on {url} after {retries} attempts: {last_error}"
    raise ScrapeError(msg)
