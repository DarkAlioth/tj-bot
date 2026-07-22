import datetime
from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    parse_result,
)

RESULT = {
    "Title": "Ubuntu 24.04 <LTS>",
    "Description": "Uploader: alice <br>Best distro",
    "CategoryDesc": "PC/ISO",
    "PublishDate": "2026-07-14T19:15:32+00:00",
    "TrackerId": "noname-club",
    "Details": "https://tracker.example/details/1",
    "Link": "http://jackett:9117/dl/1",
    "Seeders": 8,
    "Peers": 1,
    "Size": 4601968640,
}


def test_parse_result_extracts_and_escapes() -> None:
    data = parse_result(RESULT)

    assert data is not None
    assert data.title == "Ubuntu 24.04 &lt;LTS&gt;"
    assert data.uploader == "alice"
    assert data.category == "PC/ISO"
    assert data.published_at == datetime.date(2026, 7, 14)
    assert data.size == 4601968640


def test_parse_result_without_link_is_skipped() -> None:
    assert parse_result({**RESULT, "Link": None}) is None


@pytest.fixture
async def jackett_env() -> AsyncGenerator[
    Callable[[web.Application], Awaitable[JackettClient]]
]:
    servers: list[TestServer] = []
    clients: list[JackettClient] = []

    async def factory(app: web.Application) -> JackettClient:
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        client = JackettClient(
            base_url=str(server.make_url("/")),
            api_key="k",
            timeout_seconds=5,
            download_max_bytes=1024,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()
    for server in servers:
        await server.close()


async def test_search_encodes_query_and_parses(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(request.query)
        return web.json_response({"Results": [RESULT, {**RESULT, "Link": None}]})

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", handler)
    client = await jackett_env(app)

    items = await client.search("война & мир?x=1")

    assert len(items) == 1
    assert seen["apikey"] == "k"
    assert seen["Query"] == '"война & мир?x=1"'  # decoded back intact


async def test_search_http_error_raises(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=500)

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", handler)
    client = await jackett_env(app)

    with pytest.raises(JackettError, match="HTTP 500"):
        await client.search("x")


async def test_download_within_cap(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 512)

    app = web.Application()
    app.router.add_get("/dl", handler)
    client = await jackett_env(app)

    content = await client.download(str(app_url(client)) + "dl")

    assert content == b"x" * 512


async def test_download_above_cap_rejected(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 4096)

    app = web.Application()
    app.router.add_get("/dl", handler)
    client = await jackett_env(app)

    with pytest.raises(DownloadTooLargeError):
        await client.download(str(app_url(client)) + "dl")


def app_url(client: JackettClient) -> str:
    return client._base_url + "/"  # noqa: SLF001  # test reaches into the client


def test_parse_result_uploader_fallback_without_prefix() -> None:
    # no "Uploader:" prefix — the <br> fallback branch extracts the token
    result = {**RESULT, "Description": "SomeUser <br>rest of description"}
    data = parse_result(result)
    assert data is not None
    assert data.uploader == "SomeUser"
