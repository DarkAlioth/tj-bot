from collections.abc import AsyncGenerator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tj_bot.services.enrich import fetch

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "enrich"


def fixture_html(tracker: str, name: str) -> str:
    return (FIXTURES / tracker / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture
def fast_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No per-host throttling and no backoff sleeps in unit tests."""
    monkeypatch.setattr(fetch, "throttle", fetch.HostThrottle(0.0))
    monkeypatch.setattr(fetch, "backoff_delay", lambda _attempt: 0.0)


async def serve(
    app: web.Application,
) -> AsyncGenerator[tuple[str, aiohttp.ClientSession]]:
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as session:
            yield str(server.make_url("")).rstrip("/"), session
    finally:
        await server.close()
