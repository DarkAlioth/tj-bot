"""Tapochek login (members-only video sections) on top of a session's cookie jar."""

from dataclasses import dataclass, field

import aiohttp
from lxml import html
from yarl import URL

from tj_bot.services.enrich import fetch
from tj_bot.services.enrich.model import LoginRequiredError
from tj_bot.services.enrich.parsers.tapochek import needs_login

LOGIN_URL = "https://tapochek.net/login.php"
SESSION_COOKIE = "bb_data"
LOGOUT_MARKER = "logout=1"


@dataclass(frozen=True, slots=True)
class TapochekCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


async def login(
    session: aiohttp.ClientSession,
    creds: TapochekCredentials,
    *,
    login_url: str = LOGIN_URL,
    proxy: str | None = None,
    timeout_seconds: float = 20.0,
) -> None:
    """Log the session in; the ``bb_data`` autologin cookie then lives in its jar."""
    form_page = await fetch.fetch_page(
        session, login_url, proxy=proxy, timeout_seconds=timeout_seconds
    )
    form = {
        "login_username": creds.username,
        "login_password": creds.password,
        "login": "Вход",
        "autologin": "1",
    }
    tokens = html.fromstring(form_page).xpath('//input[@name="form_token"]/@value')
    if isinstance(tokens, list) and tokens:
        form["form_token"] = str(tokens[0])
    await fetch.throttle.wait(fetch.host_of(login_url))
    async with session.post(
        login_url,
        data=form,
        headers=fetch.request_headers(),
        proxy=proxy,
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
    ) as resp:
        body = await resp.text(errors="replace")
    cookies = session.cookie_jar.filter_cookies(URL(login_url))
    if SESSION_COOKIE not in cookies or LOGOUT_MARKER not in body:
        raise LoginRequiredError("tapochek: login failed")


async def fetch_tapochek(
    session: aiohttp.ClientSession,
    url: str,
    creds: TapochekCredentials | None,
    *,
    login_url: str = LOGIN_URL,
    proxy: str | None = None,
    timeout_seconds: float = 20.0,
) -> str:
    """Fetch a tapochek page; on the login wall log in once (with creds) and refetch."""
    page = await fetch.fetch_page(
        session, url, proxy=proxy, timeout_seconds=timeout_seconds
    )
    if not needs_login(page):
        return page
    if creds is None:
        msg = f"tapochek: login required for {url} and no credentials configured"
        raise LoginRequiredError(msg)
    await login(
        session,
        creds,
        login_url=login_url,
        proxy=proxy,
        timeout_seconds=timeout_seconds,
    )
    page = await fetch.fetch_page(
        session, url, proxy=proxy, timeout_seconds=timeout_seconds
    )
    if needs_login(page):
        msg = f"tapochek: still the login page after login for {url}"
        raise LoginRequiredError(msg)
    return page
