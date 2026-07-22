import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
async def qbit_env() -> AsyncGenerator[
    Callable[[web.Application], Awaitable[QbittorrentClient]]
]:
    servers: list[TestServer] = []
    clients: list[QbittorrentClient] = []

    async def factory(app: web.Application) -> QbittorrentClient:
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        client = QbittorrentClient(
            base_url=str(server.make_url("/")),
            username="user",
            password="pass",
            timeout_seconds=5,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()
    for server in servers:
        await server.close()


def login_app(**routes: Handler) -> web.Application:
    async def login(request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("username") == "user" and form.get("password") == "pass":
            return web.Response(text="Ok.")
        return web.Response(text="Fails.")

    app = web.Application()
    app.router.add_post("/api/v2/auth/login", login)
    for path, handler in routes.items():
        # torrents/info is a GET endpoint; add/delete are POST
        app.router.add_route("*", "/api/v2/" + path.replace("__", "/"), handler)
    return app


async def test_login_success(
    qbit_env: Callable[[web.Application], Awaitable[QbittorrentClient]],
) -> None:
    client = await qbit_env(login_app())
    await client.login()  # no exception


async def test_login_bad_credentials(
    qbit_env: Callable[[web.Application], Awaitable[QbittorrentClient]],
) -> None:
    client = QbittorrentClient(
        base_url="", username="user", password="wrong", timeout_seconds=5
    )
    app = login_app()
    server = TestServer(app)
    await server.start_server()
    client._base_url = str(server.make_url("")).rstrip("/")  # noqa: SLF001
    try:
        with pytest.raises(QbittorrentError, match="rejected the credentials"):
            await client.login()
    finally:
        await client.close()
        await server.close()


async def test_add_torrent_sends_file_and_tag(
    qbit_env: Callable[[web.Application], Awaitable[QbittorrentClient]],
) -> None:
    captured: dict[str, Any] = {}

    async def add(request: web.Request) -> web.Response:
        form = await request.post()
        field = form["torrents"]
        assert isinstance(field, web.FileField)  # noqa: S101
        captured["file"] = field.file.read()
        captured["filename"] = field.filename
        captured["tags"] = form["tags"]
        captured["category"] = form["category"]
        return web.Response(text="Ok.")

    client = await qbit_env(login_app(torrents__add=add))
    await client.add_torrent_file(
        b"d4:info...", "movie.torrent", tag="t1", category="c"
    )

    assert captured["file"] == b"d4:info..."
    assert captured["filename"] == "movie.torrent"
    assert captured["tags"] == "t1"
    assert captured["category"] == "c"


async def test_add_torrent_reauthenticates_on_403(
    qbit_env: Callable[[web.Application], Awaitable[QbittorrentClient]],
) -> None:
    state = {"attempts": 0}

    async def add(request: web.Request) -> web.Response:
        state["attempts"] += 1
        if state["attempts"] == 1:
            return web.Response(status=403)
        return web.Response(text="Ok.")

    client = await qbit_env(login_app(torrents__add=add))
    await client.add_torrent_file(b"x", "a.torrent", tag="t")

    assert state["attempts"] == 2


async def test_torrents_by_tag_parses_json(
    qbit_env: Callable[[web.Application], Awaitable[QbittorrentClient]],
) -> None:
    async def info(request: web.Request) -> web.Response:
        assert request.method == "GET"  # noqa: S101
        assert request.query["tag"] == "t1"  # noqa: S101
        return web.Response(
            text=json.dumps([{"hash": "abc", "progress": 1.0, "name": "x"}]),
            content_type="application/json",
        )

    client = await qbit_env(login_app(torrents__info=info))
    torrents = await client.torrents_by_tag("t1")

    assert torrents[0]["hash"] == "abc"
    assert torrents[0]["progress"] == 1.0
