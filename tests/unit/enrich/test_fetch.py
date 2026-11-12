from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from tj_bot.services.enrich import flaresolverr
from tj_bot.services.enrich.fetch import decode_body, fetch_page, is_challenge
from tj_bot.services.enrich.images import ImageError, download_image
from tj_bot.services.enrich.model import ChallengeError, NotFoundError, ScrapeError

from .conftest import serve

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
CP1251_META = (
    '<html><head><meta charset="windows-1251"></head><body>Дюна</body></html>'
).encode("cp1251")


def make_app(calls: dict[str, int]) -> web.Application:
    def count(name: str) -> int:
        calls[name] = calls.get(name, 0) + 1
        return calls[name]

    async def ok1251(_: web.Request) -> web.Response:
        return web.Response(
            body="<html>Дюна</html>".encode("cp1251"),
            content_type="text/html",
            charset="windows-1251",
        )

    async def meta1251(_: web.Request) -> web.Response:
        return web.Response(body=CP1251_META, headers={"Content-Type": "text/html"})

    async def missing(_: web.Request) -> web.Response:
        return web.Response(status=404, text="nope")

    async def flaky(_: web.Request) -> web.Response:
        if count("flaky") < 3:
            return web.Response(status=503, text="busy")
        return web.Response(text="<html>finally</html>", content_type="text/html")

    async def always_down(_: web.Request) -> web.Response:
        count("down")
        return web.Response(status=502, text="bad gateway")

    async def forbidden(_: web.Request) -> web.Response:
        count("forbidden")
        return web.Response(status=403, text="<html>no</html>")

    async def cloudflare(_: web.Request) -> web.Response:
        return web.Response(
            status=403,
            headers={"cf-ray": "a59673b43f11e4d6-RIX"},
            text="<html><head><title>Just a moment...</title></head></html>",
            content_type="text/html",
        )

    async def image_no_referer(request: web.Request) -> web.Response:
        count("img")
        if "Referer" in request.headers:
            return web.Response(status=404)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def image_needs_referer(request: web.Request) -> web.Response:
        if "Referer" not in request.headers:
            return web.Response(status=403)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def image_big(_: web.Request) -> web.Response:
        return web.Response(body=JPEG * 1000, content_type="image/jpeg")

    async def not_image(_: web.Request) -> web.Response:
        return web.Response(text="<html>page</html>", content_type="text/html")

    async def solver(request: web.Request) -> web.Response:
        payload: dict[str, Any] = await request.json()
        assert payload["cmd"] == "request.get"
        return web.json_response(
            {
                "status": "ok",
                "solution": {
                    "url": payload["url"],
                    "status": 200,
                    "response": "<html>solved</html>",
                    "userAgent": "SolverUA/1.0",
                    "cookies": [
                        {
                            "name": "cf_clearance",
                            "value": "abc",
                            "domain": ".example.org",
                            "path": "/",
                        }
                    ],
                },
            }
        )

    async def solver_error(_: web.Request) -> web.Response:
        return web.json_response({"status": "error", "message": "Challenge not solved"})

    app = web.Application()
    app.router.add_get("/ok1251", ok1251)
    app.router.add_get("/meta1251", meta1251)
    app.router.add_get("/404", missing)
    app.router.add_get("/flaky", flaky)
    app.router.add_get("/down", always_down)
    app.router.add_get("/403", forbidden)
    app.router.add_get("/cf", cloudflare)
    app.router.add_get("/img.jpg", image_no_referer)
    app.router.add_get("/img-ref.jpg", image_needs_referer)
    app.router.add_get("/big.jpg", image_big)
    app.router.add_get("/not-image", not_image)
    app.router.add_post("/v1", solver)
    app.router.add_post("/broken/v1", solver_error)
    return app


@pytest.fixture
def calls() -> dict[str, int]:
    return {}


@pytest.fixture
async def server(
    calls: dict[str, int],
) -> AsyncGenerator[tuple[str, aiohttp.ClientSession]]:
    async for item in serve(make_app(calls)):
        yield item


@pytest.mark.usefixtures("fast_network")
async def test_fetch_decodes_header_and_meta_charset(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    assert await fetch_page(session, f"{base}/ok1251") == "<html>Дюна</html>"
    assert "Дюна" in await fetch_page(session, f"{base}/meta1251")


@pytest.mark.usefixtures("fast_network")
async def test_fetch_404_raises_not_found_without_retry(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    with pytest.raises(NotFoundError):
        await fetch_page(session, f"{base}/404")


@pytest.mark.usefixtures("fast_network")
async def test_fetch_retries_5xx_then_succeeds(
    server: tuple[str, aiohttp.ClientSession], calls: dict[str, int]
) -> None:
    base, session = server
    assert await fetch_page(session, f"{base}/flaky") == "<html>finally</html>"
    assert calls["flaky"] == 3


@pytest.mark.usefixtures("fast_network")
async def test_fetch_gives_up_after_retries(
    server: tuple[str, aiohttp.ClientSession], calls: dict[str, int]
) -> None:
    base, session = server
    with pytest.raises(ScrapeError, match="after 2 attempts"):
        await fetch_page(session, f"{base}/down", retries=2)
    assert calls["down"] == 2


@pytest.mark.usefixtures("fast_network")
async def test_fetch_403_without_challenge_is_not_retried(
    server: tuple[str, aiohttp.ClientSession], calls: dict[str, int]
) -> None:
    base, session = server
    with pytest.raises(ScrapeError, match="HTTP 403"):
        await fetch_page(session, f"{base}/403")
    assert calls["forbidden"] == 1


@pytest.mark.usefixtures("fast_network")
async def test_fetch_detects_cloudflare_challenge(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    with pytest.raises(ChallengeError):
        await fetch_page(session, f"{base}/cf")


@pytest.mark.usefixtures("fast_network")
async def test_fetch_connection_error_is_retried_then_raised(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    _, session = server
    with pytest.raises(ScrapeError, match="ClientConnectorError"):
        await fetch_page(session, "http://127.0.0.1:1/", retries=2, timeout_seconds=2)


async def test_fetch_rejects_non_http_proxy(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    with pytest.raises(ValueError, match="http"):
        await fetch_page(session, f"{base}/ok1251", proxy="socks5://127.0.0.1:1080")


@pytest.mark.usefixtures("fast_network")
async def test_download_image_retries_without_referer_on_404(
    server: tuple[str, aiohttp.ClientSession], calls: dict[str, int]
) -> None:
    base, session = server
    data = await download_image(
        session, f"{base}/img.jpg", referer="https://megapeer.vip/t/1"
    )
    assert data == JPEG and calls["img"] == 2
    with_ref = await download_image(
        session, f"{base}/img-ref.jpg", referer="https://x/"
    )
    assert with_ref == JPEG


@pytest.mark.usefixtures("fast_network")
async def test_download_image_never_sends_referer_to_fastpic(
    server: tuple[str, aiohttp.ClientSession], calls: dict[str, int]
) -> None:
    base, session = server
    # the host rule is checked on the URL's host; the test server is not fastpic,
    # so verify the decision helper separately from the transport
    from tj_bot.services.enrich.images import _wants_referer

    assert not _wants_referer("https://i123.fastpic.org/big/x.jpg", "https://t/")
    assert _wants_referer("https://img.byrutgame.org/x.webp", "https://t/")
    assert await download_image(session, f"{base}/img.jpg") == JPEG
    assert calls["img"] == 1


@pytest.mark.usefixtures("fast_network")
async def test_download_image_rejects_large_and_non_images(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    with pytest.raises(ImageError, match="too large|exceeds"):
        await download_image(session, f"{base}/big.jpg", max_bytes=1000)
    with pytest.raises(ImageError, match="not an image"):
        await download_image(session, f"{base}/not-image")
    with pytest.raises(NotFoundError):
        await download_image(session, f"{base}/404")


async def test_flaresolverr_solve_returns_html_and_stores_cookies(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    solution = await flaresolverr.solve(session, base, "https://example.org/topic")
    assert solution.html == "<html>solved</html>"
    assert solution.user_agent == "SolverUA/1.0" and solution.status == 200
    jar = session.cookie_jar.filter_cookies(URL("https://example.org/x"))
    assert jar["cf_clearance"].value == "abc"


async def test_flaresolverr_errors_raise_scrape_error(
    server: tuple[str, aiohttp.ClientSession],
) -> None:
    base, session = server
    with pytest.raises(ScrapeError, match="Challenge not solved"):
        await flaresolverr.solve(session, f"{base}/broken", "https://example.org/")
    with pytest.raises(ScrapeError, match="unreachable"):
        await flaresolverr.solve(session, "http://127.0.0.1:1", "https://example.org/")


def test_decode_body_and_is_challenge_helpers() -> None:
    assert decode_body("Дюна".encode("cp1251"), None) == "Дюна"
    assert decode_body("Дюна".encode(), None) == "Дюна"
    assert decode_body(b"x", "bogus-charset") == "x"
    assert is_challenge(503, {}, "<html><title>Just a moment...</title>")
    assert is_challenge(200, {"cf-mitigated": "challenge"}, "")
    assert is_challenge(403, {"cf-ray": "x"}, "<html>blocked</html>")
    assert not is_challenge(403, {}, "<html>plain forbidden</html>")
    assert not is_challenge(200, {}, "<html>Just a moment of your time")
