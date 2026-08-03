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


async def test_search_retries_once_on_connection_drop(
    jackett_env: "Callable[[web.Application], Awaitable[JackettClient]]",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as jsonlib

    monkeypatch.setattr("tj_bot.services.jackett.RETRY_DELAY_SECONDS", 0)
    state = {"attempts": 0}

    async def results(request: web.Request) -> web.Response:
        state["attempts"] += 1
        if state["attempts"] == 1:
            assert request.transport is not None  # noqa: S101
            request.transport.close()  # drop mid-request -> client conn error
        return web.Response(
            text=jsonlib.dumps({"Results": [RESULT]}),
            content_type="application/json",
        )

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", results)
    client = await jackett_env(app)

    items = await client.search("ubuntu")

    assert state["attempts"] == 2
    assert len(items) == 1


async def test_search_gives_up_after_second_connection_drop(
    jackett_env: "Callable[[web.Application], Awaitable[JackettClient]]",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tj_bot.services.jackett.RETRY_DELAY_SECONDS", 0)

    async def results(request: web.Request) -> web.Response:
        assert request.transport is not None  # noqa: S101
        request.transport.close()
        return web.Response()

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", results)
    client = await jackett_env(app)

    with pytest.raises(JackettError, match="failed"):
        await client.search("ubuntu")


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


async def test_download_timeout_is_capped_separately(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downloads must not inherit the long search timeout of the session."""
    import asyncio

    from aiohttp import ClientTimeout

    monkeypatch.setattr(
        "tj_bot.services.jackett.DOWNLOAD_TIMEOUT", ClientTimeout(total=0.05)
    )

    async def slow_handler(request: web.Request) -> web.Response:
        await asyncio.sleep(0.3)
        return web.Response(body=b"x")

    app = web.Application()
    app.router.add_get("/dl", slow_handler)
    client = await jackett_env(app)

    with pytest.raises(JackettError, match="Download failed"):
        await client.download(str(app_url(client)) + "dl")


def app_url(client: JackettClient) -> str:
    return client._base_url + "/"  # noqa: SLF001  # test reaches into the client


def test_parse_result_uploader_fallback_without_prefix() -> None:
    # no "Uploader:" prefix — the <br> fallback branch extracts the token
    result = {**RESULT, "Description": "SomeUser <br>rest of description"}
    data = parse_result(result)
    assert data is not None
    assert data.uploader == "SomeUser"


def test_parse_result_bad_date_skipped_not_raised() -> None:
    # malformed date must skip the single result, never crash the batch
    assert parse_result({**RESULT, "PublishDate": "not-a-date"}) is None


def test_parse_result_missing_date_uses_today() -> None:
    import datetime

    data = parse_result({**RESULT, "PublishDate": None})
    assert data is not None
    assert data.published_at == datetime.date.today()


def test_parse_result_bad_size_skipped() -> None:
    assert parse_result({**RESULT, "Size": "N/A"}) is None


def test_parse_result_truncates_long_description() -> None:
    long_desc = "x" * 5000
    data = parse_result({**RESULT, "Description": long_desc})
    assert data is not None
    assert data.description is not None
    assert len(data.description) <= 520
    assert data.description.endswith("…")


def test_parse_result_caps_pathological_description() -> None:
    # a multi-hundred-KB blob without <br> makes the uploader regexes
    # quadratic; the raw cap keeps parsing bounded (this test hangs without it)
    huge = "Uploader: bob " + "x" * 300_000
    data = parse_result({**RESULT, "Description": huge})
    assert data is not None
    assert data.uploader == "bob"
    assert data.description is not None
    assert len(data.description) <= 520


async def test_search_survives_one_malformed_result(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        good = RESULT
        bad = {**RESULT, "Title": "Broken", "PublishDate": "garbage"}
        return web.json_response({"Results": [good, bad, good]})

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", handler)
    client = await jackett_env(app)

    items = await client.search("x")

    # the malformed result is skipped; the two good ones survive
    assert len(items) == 2


async def test_indexers_extracts_health_from_results(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert request.query["Query"] == ""  # empty health probe
        return web.json_response(
            {
                "Results": [],
                "Indexers": [
                    {"Name": "RuTracker", "Error": None, "Results": 50},
                    {"Name": "Dead", "Error": "timeout", "Results": 0},
                ],
            }
        )

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", handler)
    client = await jackett_env(app)

    indexers = await client.indexers()

    assert [i["Name"] for i in indexers] == ["RuTracker", "Dead"]
    assert indexers[1]["Error"] == "timeout"


async def test_search_records_observed_indexer_errors(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "Results": [RESULT],
                "Indexers": [
                    {"ID": "ok", "Name": "Ok", "Error": None},
                    {"ID": "dead", "Name": "Dead", "Error": "timeout"},
                ],
            }
        )

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/all/results", handler)
    client = await jackett_env(app)

    await client.search("x")

    assert client.observed_errors() == {"dead": "Dead"}
    # take() consumes: the monitor must not re-confirm the same observation
    assert client.take_observed_errors() == {"dead": "Dead"}
    assert client.observed_errors() == {}


async def test_probe_indexer_returns_error_or_none(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    state = {"error": "cf challenge"}

    async def handler(request: web.Request) -> web.Response:
        assert request.query["Query"] == ""
        return web.json_response(
            {
                "Results": [],
                "Indexers": [
                    {
                        "ID": "rutracker",
                        "Name": "RuTracker.org",
                        "Error": state["error"],
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/rutracker/results", handler)
    client = await jackett_env(app)

    assert await client.probe_indexer("rutracker") == "cf challenge"

    state["error"] = ""
    assert await client.probe_indexer("rutracker") is None


async def test_probe_indexer_refuses_suspicious_id(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.json_response({"Results": [], "Indexers": []})

    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handler)
    client = await jackett_env(app)

    # traversal-looking ids never reach the URL (yarl decodes %2F back)
    for hostile in ("../server/config", "..", "", "a/b"):
        assert await client.probe_indexer(hostile) == "invalid indexer id"
    assert seen == []


async def test_search_indexer_queries_one_tracker(
    jackett_env: Callable[[web.Application], Awaitable[JackettClient]],
) -> None:
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(request.query)
        return web.json_response({"Results": [RESULT]})

    app = web.Application()
    app.router.add_get("/api/v2.0/indexers/rutracker/results", handler)
    client = await jackett_env(app)

    items = await client.search_indexer("rutracker", "Series S01")

    assert len(items) == 1
    assert seen["Query"] == "Series S01"
    # hostile ids never reach the URL
    assert await client.search_indexer("../all", "x") == []
