import asyncio
import datetime
import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import make_url, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tj_bot.db.maintenance import cleanup_once
from tj_bot.db.meta_repo import MetaRepo, is_expired
from tj_bot.db.migrate import run_migrations
from tj_bot.db.models import TorrentMetaRow

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set (integration tests need real PostgreSQL)",
)


@pytest.fixture(scope="module", autouse=True)
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
async def pool() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(make_url(TEST_DATABASE_URL))
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    async with session_pool() as session:
        await session.execute(text("TRUNCATE torrent_meta"))
        await session.commit()
    yield session_pool
    await engine.dispose()


async def store(
    pool: async_sessionmaker[AsyncSession],
    torrent_hash: str,
    status: str,
    error: str | None = None,
) -> TorrentMetaRow:
    async with pool() as session:
        row = await MetaRepo(session).upsert(
            torrent_hash=torrent_hash,
            tracker="RuTor",
            details_url="https://rutor.info/torrent/1/x",
            status=status,
            meta={"title_ru": "Дюна", "genres": ["драма"]},
            error=error,
        )
        await session.commit()
        return row


async def age(
    pool: async_sessionmaker[AsyncSession], torrent_hash: str, **delta: int
) -> None:
    async with pool() as session:
        await session.execute(
            update(TorrentMetaRow)
            .where(TorrentMetaRow.hash == torrent_hash)
            .values(
                fetched_at=datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(**delta)
            )
        )
        await session.commit()


async def test_upsert_and_get(pool: async_sessionmaker[AsyncSession]) -> None:
    row = await store(pool, "m1", "ok")
    assert row.fetched_at is not None
    async with pool() as session:
        repo = MetaRepo(session)
        loaded = await repo.get("m1")
        assert loaded is not None
        assert loaded.meta == {"title_ru": "Дюна", "genres": ["драма"]}
        assert not is_expired(loaded)
        assert await repo.get("absent") is None
    # a re-scrape replaces the row
    row = await store(pool, "m1", "error", error="HTTP 503")
    assert row.status == "error" and row.error == "HTTP 503"


async def test_purge_expired_follows_the_status_ttls(
    pool: async_sessionmaker[AsyncSession],
) -> None:
    for name, status in (
        ("ok_fresh", "ok"),
        ("ok_old", "ok"),
        ("err_old", "error"),
        ("nf_fresh", "not_found"),
        ("nf_old", "not_found"),
        ("cf_old", "challenge"),
        ("login_old", "login"),
        ("weird_old", "weird"),
    ):
        await store(pool, name, status)
    await age(pool, "ok_old", days=31)
    await age(pool, "err_old", days=2)
    await age(pool, "nf_fresh", days=6)
    await age(pool, "nf_old", days=8)
    await age(pool, "cf_old", hours=2)
    await age(pool, "login_old", hours=2)
    await age(pool, "weird_old", days=2)
    async with pool() as session:
        assert is_expired(await _get(session, "ok_old"))
        assert not is_expired(await _get(session, "nf_fresh"))
        deleted = await MetaRepo(session).purge_expired()
        await session.commit()
    assert deleted == 6
    async with pool() as session:
        remaining = (
            await session.execute(text("SELECT hash FROM torrent_meta ORDER BY hash"))
        ).scalars()
        assert list(remaining) == ["nf_fresh", "ok_fresh"]


async def _get(session: AsyncSession, torrent_hash: str) -> TorrentMetaRow:
    row = await MetaRepo(session).get(torrent_hash)
    assert row is not None
    return row


async def test_cleanup_once_purges_meta_rows(
    pool: async_sessionmaker[AsyncSession],
) -> None:
    await store(pool, "stale", "challenge")
    await age(pool, "stale", hours=3)
    await store(pool, "fresh", "ok")
    deleted = await cleanup_once(
        pool, datetime.timedelta(days=7), datetime.timedelta(days=30)
    )
    assert deleted == 1
    async with pool() as session:
        assert await MetaRepo(session).get("stale") is None
        assert await MetaRepo(session).get("fresh") is not None
