"""Login flow against a local aiohttp mock of tapochek's login.php (no network)."""

from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from tj_bot.services.enrich.model import LoginRequiredError
from tj_bot.services.enrich.parsers.tapochek import needs_login
from tj_bot.services.enrich.tapochek import TapochekCredentials, fetch_tapochek

from .conftest import fixture_html, serve

LOGIN_PAGE = (
    "<html><head><title>Tapochek.net</title></head><body>"
    '<form action="./login.php" method="post">'
    '<input type="hidden" name="form_token" value="tok123" />'
    '<input type="text" name="login_username" />'
    '<input type="password" name="login_password" />'
    '<input type="hidden" name="redirect" value="viewtopic.php?t=1" />'
    '<input type="submit" name="login" value="Вход" /></form></body></html>'
)
MEMBERS_PAGE = (
    '<html><body><a href="./login.php?logout=1" class="logout-link">Выход</a>'
    '<div class="post_body">Год выпуска: 2024</div></body></html>'
)
CREDS = TapochekCredentials("user", "secret")


def make_app(posted: list[dict[str, Any]]) -> web.Application:
    async def topic(request: web.Request) -> web.Response:
        page = MEMBERS_PAGE if request.cookies.get("bb_data") == "ok" else LOGIN_PAGE
        return web.Response(text=page, content_type="text/html")

    async def login_get(_: web.Request) -> web.Response:
        return web.Response(text=LOGIN_PAGE, content_type="text/html")

    async def login_post(request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}
        posted.append(form)
        good = (
            form.get("login_username") == "user"
            and form.get("login_password") == "secret"
        )
        if (
            not good
            or form.get("login") != "Вход"
            or form.get("form_token") != "tok123"
        ):
            return web.Response(text=LOGIN_PAGE, content_type="text/html")
        resp = web.Response(text=MEMBERS_PAGE, content_type="text/html")
        resp.set_cookie("bb_data", "ok")
        return resp

    app = web.Application()
    app.router.add_get("/viewtopic.php", topic)
    app.router.add_get("/login.php", login_get)
    app.router.add_post("/login.php", login_post)
    return app


@pytest.fixture
def posted() -> list[dict[str, Any]]:
    return []


@pytest.fixture
async def server(
    posted: list[dict[str, Any]],
) -> AsyncGenerator[tuple[str, aiohttp.ClientSession]]:
    async for item in serve(make_app(posted)):
        yield item


async def fetch_via(
    server: tuple[str, aiohttp.ClientSession], creds: TapochekCredentials | None
) -> str:
    base, session = server
    return await fetch_tapochek(
        session, f"{base}/viewtopic.php?t=1", creds, login_url=f"{base}/login.php"
    )


def test_needs_login_only_on_the_login_wall() -> None:
    assert needs_login(fixture_html("tapochek", "266710"))  # guest view, members-only
    assert not needs_login(
        fixture_html("tapochek", "289457")
    )  # public, quick-login box
    assert not needs_login(fixture_html("tapochek", "members_266710"))
    assert needs_login(LOGIN_PAGE) and not needs_login(MEMBERS_PAGE)


@pytest.mark.usefixtures("fast_network")
async def test_fetch_tapochek_logs_in_once_and_refetches(
    server: tuple[str, aiohttp.ClientSession], posted: list[dict[str, Any]]
) -> None:
    page = await fetch_via(server, CREDS)
    assert "post_body" in page and "logout=1" in page
    assert posted == [
        {
            "login_username": "user",
            "login_password": "secret",
            "login": "Вход",
            "autologin": "1",
            "form_token": "tok123",
        }
    ]


@pytest.mark.usefixtures("fast_network")
async def test_fetch_tapochek_wrong_password_raises_login_failed(
    server: tuple[str, aiohttp.ClientSession], posted: list[dict[str, Any]]
) -> None:
    with pytest.raises(LoginRequiredError, match="login failed"):
        await fetch_via(server, TapochekCredentials("user", "wrong"))
    assert len(posted) == 1


@pytest.mark.usefixtures("fast_network")
async def test_fetch_tapochek_without_credentials_raises(
    server: tuple[str, aiohttp.ClientSession], posted: list[dict[str, Any]]
) -> None:
    with pytest.raises(LoginRequiredError, match="login required"):
        await fetch_via(server, None)
    assert posted == []


def test_credentials_repr_hides_secrets() -> None:
    assert "secret" not in repr(CREDS) and "user" not in repr(CREDS)
