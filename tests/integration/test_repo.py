import asyncio
import datetime
import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tj_bot.db.maintenance import cleanup_once
from tj_bot.db.migrate import run_migrations
from tj_bot.db.repo import TorrentData, TorrentRepo

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set (integration tests need real PostgreSQL)",
)


def make_torrent(
    torrent_hash: str = "hash-1",
    category: str = "Movies",
    seeders: int = 10,
) -> TorrentData:
    return TorrentData(
        hash=torrent_hash,
        title=f"Title {torrent_hash}",
        uploader="uploader",
        description="description",
        category=category,
        details_url="https://tracker.example/details",
        download_url="http://jackett:9117/dl/x",
        seeders=seeders,
        peers=3,
        published_at=datetime.date(2026, 7, 1),
        size=4_601_968_640,
    )


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> None:
    url = make_url(TEST_DATABASE_URL)

    async def wipe() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(wipe())
    run_migrations(url)


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(make_url(TEST_DATABASE_URL))
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as db_session:
        for table in ("search_query_torrents", "search_queries", "torrents"):
            await db_session.execute(text(f"TRUNCATE {table} CASCADE"))
        await db_session.commit()
        yield db_session
    await engine.dispose()


async def test_upsert_inserts_and_returns_ids(session: AsyncSession) -> None:
    repo = TorrentRepo(session)

    ids = await repo.upsert_torrents([make_torrent("a"), make_torrent("b")])

    assert len(ids) == 2
    assert len(set(ids)) == 2


async def test_upsert_refreshes_existing_without_duplicates(
    session: AsyncSession,
) -> None:
    repo = TorrentRepo(session)
    (first_id,) = await repo.upsert_torrents([make_torrent("a", seeders=1)])

    (second_id,) = await repo.upsert_torrents([make_torrent("a", seeders=99)])
    stored = await repo.get_torrent_by_hash("a")

    assert first_id == second_id
    assert stored is not None
    assert stored.seeders == 99


async def test_search_lifecycle_pagination_and_categories(
    session: AsyncSession,
) -> None:
    repo = TorrentRepo(session)
    items = [
        make_torrent("a", category="Movies", seeders=50),
        make_torrent("b", category="Movies", seeders=10),
        make_torrent("c", category="Audio", seeders=30),
    ]
    ids = await repo.upsert_torrents(items)
    await repo.create_search("qh", ids)

    search = await repo.get_search("qh")
    assert search is not None
    assert search.result_count == 3

    top = await repo.get_result_page("qh", None, 0)
    assert top is not None
    assert top.hash == "a"  # highest seeders first

    audio_only = await repo.get_result_page("qh", "Audio", 0)
    assert audio_only is not None
    assert audio_only.hash == "c"

    assert await repo.count_results("qh", "Movies") == 2
    assert await repo.get_categories("qh") == [("Audio", 1), ("Movies", 2)]

    beyond = await repo.get_result_page("qh", None, 10)
    assert beyond is None


async def test_create_search_is_idempotent(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("a")])

    await repo.create_search("qh", ids)
    await repo.create_search("qh", ids)

    search = await repo.get_search("qh")
    assert search is not None
    assert await repo.count_results("qh", None) == 1


async def test_cleanup_removes_stale_entries_only(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("old"), make_torrent("fresh")])
    await repo.create_search("qh-old", [ids[0]])
    await session.commit()
    await session.execute(
        text(
            "UPDATE torrents SET updated_at = now() - interval '10 days' "
            "WHERE hash = 'old'"
        )
    )
    await session.execute(
        text("UPDATE search_queries SET created_at = now() - interval '10 days'")
    )
    await session.commit()

    pool = async_sessionmaker(session.bind, expire_on_commit=False)
    deleted = await cleanup_once(pool, datetime.timedelta(days=7))

    assert deleted == 2
    assert await repo.get_torrent_by_hash("old") is None
    assert await repo.get_torrent_by_hash("fresh") is not None
    assert await repo.get_search("qh-old") is None
