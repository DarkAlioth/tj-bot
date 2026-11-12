"""FlareSolverr client: solve a challenge once, reuse its cookies and user agent."""

from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL

from tj_bot.services.enrich.model import NotFoundError, ScrapeError

DEFAULT_TIMEOUT_MS = 55_000


@dataclass(slots=True)
class Solution:
    html: str
    status: int
    user_agent: str
    cookies: list[dict[str, Any]]  # FlareSolverr cookie objects (name, value, domain)


async def solve(
    session: aiohttp.ClientSession,
    base_url: str,
    url: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    proxy: str | None = None,
) -> Solution:
    """``request.get`` through FlareSolverr; the clearance cookies land in the jar.

    Later direct requests to the same host pass the challenge as long as they
    send the solver's ``user_agent`` with those cookies.
    """
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    if proxy:
        payload["proxy"] = {"url": proxy}
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000 + 10)
    try:
        async with session.post(
            f"{base_url.rstrip('/')}/v1", json=payload, timeout=timeout
        ) as resp:
            body: dict[str, Any] = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
        msg = f"flaresolverr unreachable: {exc}"
        raise ScrapeError(msg) from exc
    if body.get("status") != "ok":
        msg = f"flaresolverr: {body.get('message', 'unknown error')}"
        raise ScrapeError(msg)
    sol: dict[str, Any] = body.get("solution") or {}
    status = int(sol.get("status", 0))
    if status in (404, 410):
        msg = f"{status} for {url}"
        raise NotFoundError(msg)
    solution = Solution(
        html=str(sol.get("response", "")),
        status=status,
        user_agent=str(sol.get("userAgent", "")),
        cookies=list(sol.get("cookies", [])),
    )
    apply_cookies(session, solution)
    return solution


def apply_cookies(session: aiohttp.ClientSession, solution: Solution) -> None:
    for cookie in solution.cookies:
        domain = str(cookie.get("domain", "")).lstrip(".")
        if not domain or "name" not in cookie:
            continue
        session.cookie_jar.update_cookies(
            {str(cookie["name"]): str(cookie.get("value", ""))},
            response_url=URL(f"https://{domain}/"),
        )
