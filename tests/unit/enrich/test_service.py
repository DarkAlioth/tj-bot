"""EnrichmentService against fake fetchers and an in-memory meta repo."""

import asyncio
import datetime
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.config import Settings
from tj_bot.db.models import Torrent, TorrentMetaRow
from tj_bot.services.enrich import flaresolverr
from tj_bot.services.enrich import service as service_module
from tj_bot.services.enrich.model import (
    ChallengeError,
    LoginRequiredError,
    NotFoundError,
    ScrapeError,
)
from tj_bot.services.enrich.parsers import parser_for
from tj_bot.services.enrich.service import (
    EnrichmentService,
    HostBreaker,
    build_enricher,
)
from tj_bot.services.enrich.tapochek import TapochekCredentials
from tj_bot.services.enrichment import CardMeta

from .conftest import fixture_html

NNM_URL = "https://nnmclub.to/forum/viewtopic.php?t=1722958"
TITLE = "Дюна: Часть вторая / Dune: Part Two (2024) BDRemux [H.264/1080p]"


def make_settings(**overrides: Any) -> Settings:  # noqa: ANN401  # test helper
    values: dict[str, Any] = {
        "bot_token": "42:TEST",
        "postgres_user": "u",
        "postgres_password": "p",
        "postgres_db": "d",
        "db_host": "db",
    }
    values.update(overrides)
    # type ignore: _env_file is a settings-source argument invisible to the
    # generated __init__ signature
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def torrent(
    torrent_hash: str = "h1", url: str = NNM_URL, category: str = "Movies/HD"
) -> Torrent:
    return Torrent(
        hash=torrent_hash,
        title=TITLE,
        tracker="NoNaMe Club",
        details_url=url,
        category=category,
    )


class FakeMetaRepo:
    rows: dict[str, TorrentMetaRow] = {}

    def __init__(self, session: object) -> None:
        pass

    async def get(self, torrent_hash: str) -> TorrentMetaRow | None:
        return self.rows.get(torrent_hash)

    async def upsert(self, **values: Any) -> TorrentMetaRow:  # noqa: ANN401
        row = TorrentMetaRow(
            hash=values["torrent_hash"],
            tracker=values["tracker"],
            details_url=values["details_url"],
            status=values["status"],
            fetched_at=datetime.datetime.now(datetime.UTC),
            meta=values["meta"],
            error=values.get("error"),
        )
        self.rows[row.hash] = row
        return row


class FakeSession:
    async def commit(self) -> None:
        return None


class FakePool:
    def __call__(self) -> "FakePool":
        return self

    async def __aenter__(self) -> FakeSession:
        return FakeSession()

    async def __aexit__(self, *exc: object) -> None:
        return None


class Net:
    """Scripted fetch/download behaviour and call counters."""

    def __init__(self) -> None:
        self.page_calls: list[str] = []
        self.page: str | Exception = fixture_html("nnmclub", "1722958")
        self.delay = 0.0

    async def fetch_page(self, session: object, url: str, **kwargs: Any) -> str:  # noqa: ANN401
        self.page_calls.append(url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.page, Exception):
            raise self.page
        return self.page


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    scripted = Net()
    FakeMetaRepo.rows = {}
    monkeypatch.setattr(service_module, "MetaRepo", FakeMetaRepo)
    monkeypatch.setattr(service_module, "fetch_page", scripted.fetch_page)
    return scripted


@pytest.fixture
async def service(net: Net) -> AsyncGenerator[EnrichmentService]:
    pool = cast(async_sessionmaker[AsyncSession], FakePool())
    svc = EnrichmentService(pool, flaresolverr_url=None, concurrency=2)
    await svc.start()
    yield svc
    await svc.close()


class Ready:
    def __init__(self, service: EnrichmentService) -> None:
        self.events: list[tuple[str, CardMeta]] = []
        self.signal = asyncio.Event()
        service.on_ready(self)

    async def __call__(self, torrent_hash: str, meta: CardMeta) -> None:
        self.events.append((torrent_hash, meta))
        self.signal.set()

    async def wait(self, count: int = 1) -> None:
        while len(self.events) < count:
            self.signal.clear()
            await asyncio.wait_for(self.signal.wait(), 5)


async def test_card_meta_within_budget_returns_page_data_and_stores_row(
    service: EnrichmentService, net: Net
) -> None:
    ready = Ready(service)
    meta = await service.card_meta(torrent(), budget=5)
    assert meta.pending is False
    assert (meta.title_ru, meta.title_en, meta.year) == (
        "Дюна: Часть вторая",
        "Dune: Part Two",
        2024,
    )
    assert meta.chips == ("1080p", "BDRemux", "H.264")
    assert meta.genres == ("фантастика", "боевик", "драма", "приключения")
    assert meta.director == "Дени Вильнёв" and len(meta.cast) == 8
    row = FakeMetaRepo.rows["h1"]
    assert row.status == "ok" and row.meta["imdb_id"] == "tt15239678"
    assert "poster_urls" not in row.meta
    await ready.wait()
    assert ready.events[0][0] == "h1" and ready.events[0][1].pending is False
    assert net.page_calls == [NNM_URL]


async def test_card_meta_over_budget_is_pending_then_callback_delivers(
    service: EnrichmentService, net: Net
) -> None:
    net.delay = 0.3
    ready = Ready(service)
    meta = await service.card_meta(torrent(), budget=0.05)
    assert meta.pending is True
    assert meta.chips == ("1080p", "BDRemux", "H.264")
    assert meta.title_ru == "Дюна: Часть вторая" and meta.genres == ()
    await ready.wait()
    _, delivered = ready.events[0]
    assert delivered.pending is False and delivered.genres
    # a second request is served from the cache without another fetch
    again = await service.card_meta(torrent(), budget=0)
    assert again.pending is False and again.plot == delivered.plot
    assert net.page_calls == [NNM_URL]


async def test_zero_budget_schedules_without_waiting(
    service: EnrichmentService, net: Net
) -> None:
    ready = Ready(service)
    meta = await service.card_meta(torrent(), budget=0)
    assert meta.pending is True
    await ready.wait()
    assert "h1" in FakeMetaRepo.rows


async def test_expired_row_is_refetched_but_fresh_row_is_not(
    service: EnrichmentService, net: Net
) -> None:
    repo = FakeMetaRepo(None)
    row = await repo.upsert(
        torrent_hash="h1",
        tracker="x",
        details_url=NNM_URL,
        status="ok",
        meta={"title_ru": "Из кэша", "genres": ["драма"]},
    )
    meta = await service.card_meta(torrent(), budget=1)
    assert meta.title_ru == "Из кэша" and meta.genres == ("драма",)
    assert meta.title_ru is not None and net.page_calls == []
    row.fetched_at -= datetime.timedelta(days=31)
    meta = await service.card_meta(torrent(), budget=5)
    assert meta.title_ru == "Дюна: Часть вторая" and net.page_calls == [NNM_URL]


async def test_prefetch_dedupes_jobs_and_card_request_bumps_priority(
    service: EnrichmentService, net: Net
) -> None:
    net.delay = 0.2
    ready = Ready(service)
    service.prefetch([torrent("a"), torrent("a"), torrent("b"), torrent("c")])
    assert len(service._jobs) == 3  # noqa: SLF001  # queue internals under test
    meta = await service.card_meta(torrent("c"), budget=1)
    assert meta.pending is False
    await ready.wait(3)
    assert sorted(h for h, _ in ready.events) == ["a", "b", "c"]
    assert len(net.page_calls) == 3


async def test_consecutive_failures_open_the_breaker(
    service: EnrichmentService, net: Net
) -> None:
    net.page = ScrapeError("boom")
    ready = Ready(service)
    for index in range(3):
        meta = await service.card_meta(torrent(f"h{index}"), budget=5)
        assert meta.pending is False and meta.genres == ()
        assert FakeMetaRepo.rows[f"h{index}"].status == "error"
    assert len(net.page_calls) == 3
    meta = await service.card_meta(torrent("h9"), budget=5)
    await ready.wait(4)
    assert meta.pending is False and "h9" not in FakeMetaRepo.rows
    assert len(net.page_calls) == 3  # breaker open: no fetch attempted
    assert all(not m.pending for _, m in ready.events)


def test_host_breaker_closes_after_the_pause() -> None:
    breaker = HostBreaker(failures=2, open_seconds=0.0)
    breaker.failure("x")
    assert not breaker.is_open("x")
    breaker.failure("x")
    assert not breaker.is_open("x")  # opened, but for zero seconds
    breaker.success("x")
    slow = HostBreaker(failures=1, open_seconds=60)
    slow.failure("y")
    assert slow.is_open("y") and not slow.is_open("x")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("404"), "not_found"),
        (LoginRequiredError("wall"), "login"),
        (ChallengeError("cf"), "challenge"),
    ],
)
async def test_scrape_errors_map_to_statuses(
    service: EnrichmentService, net: Net, error: Exception, status: str
) -> None:
    net.page = error
    meta = await service.card_meta(torrent(), budget=5)
    row = FakeMetaRepo.rows["h1"]
    assert row.status == status and row.error and row.meta == {}
    assert meta.pending is False and meta.title_en == "Dune: Part Two"


async def test_unsupported_tracker_is_recorded_without_a_fetch(
    service: EnrichmentService, net: Net
) -> None:
    meta = await service.card_meta(torrent(url="https://example.org/t/1"), budget=5)
    assert meta.pending is False and net.page_calls == []
    assert FakeMetaRepo.rows["h1"].status == "unsupported"


async def test_challenge_is_solved_through_flaresolverr(
    net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    net.page = ChallengeError("cf")
    solved: list[str] = []

    async def solve(
        session: object,
        base_url: str,
        url: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> flaresolverr.Solution:
        solved.append(base_url)
        return flaresolverr.Solution(
            html=fixture_html("nnmclub", "1722958"),
            status=200,
            user_agent="SolverUA",
            cookies=[],
        )

    monkeypatch.setattr(flaresolverr, "solve", solve)
    pool = cast(async_sessionmaker[AsyncSession], FakePool())
    service = EnrichmentService(pool, flaresolverr_url="http://solver:8191")
    await service.start()
    try:
        meta = await service.card_meta(torrent(), budget=5)
    finally:
        await service.close()
    assert solved == ["http://solver:8191"] and meta.genres
    assert service._user_agent == "SolverUA"  # noqa: SLF001  # pinned for later requests


async def test_worker_survives_a_crashing_job(
    service: EnrichmentService, net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(url: str) -> None:
        raise RuntimeError("parser bug")

    monkeypatch.setattr(service_module, "parser_for", broken)
    ready = Ready(service)
    meta = await service.card_meta(torrent(), budget=5)
    assert meta.pending is False and meta.title_ru == "Дюна: Часть вторая"
    await ready.wait()
    monkeypatch.setattr(service_module, "parser_for", parser_for)
    assert (await service.card_meta(torrent("h2"), budget=5)).genres


def test_build_enricher_respects_the_switch_and_credentials() -> None:
    pool = cast(async_sessionmaker[AsyncSession], FakePool())
    assert build_enricher(make_settings(enrich_enabled=False), pool) is None
    service = build_enricher(
        make_settings(tapochek_username="u", tapochek_password="p"), pool
    )
    assert isinstance(service, EnrichmentService)
    assert service._tapochek == TapochekCredentials("u", "p")  # noqa: SLF001
    assert service._flaresolverr_url == "http://flaresolverr:8191"  # noqa: SLF001


async def test_unstarted_service_refuses_to_fetch() -> None:
    pool = cast(async_sessionmaker[AsyncSession], FakePool())
    service = EnrichmentService(pool)
    with pytest.raises(RuntimeError, match="not started"):
        service._require_session()  # noqa: SLF001
    await service.close()  # idempotent without a session


def test_target_snapshot_is_detached_from_the_orm_row() -> None:
    target = service_module.Target.of(torrent())
    assert (target.hash, target.category) == ("h1", "Movies/HD")
