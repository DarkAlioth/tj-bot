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
        tracker="rutracker",
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


async def test_recent_search_and_history_and_stats(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("a"), make_torrent("b")])
    query_id = await repo.create_search("qh", ids, query_text="ubuntu iso")
    await repo.record_search_event(111, query_id)
    await repo.record_search_event(111, query_id)
    await session.commit()

    recent = await repo.find_recent_search("ubuntu iso", datetime.timedelta(hours=1))
    assert recent is not None
    assert recent.hash == "qh"
    assert (
        await repo.find_recent_search("ubuntu iso", datetime.timedelta(seconds=0))
        is None
    )

    history = await repo.get_user_history(111)
    assert history == [(query_id, "ubuntu iso")]
    assert await repo.get_query_text(query_id) == "ubuntu iso"

    stats = await repo.get_stats(datetime.timedelta(days=7))
    assert stats.torrents == 2
    assert stats.events_window == 2
    assert stats.users_window == 1
    assert stats.top_queries == [("ubuntu iso", 2)]


async def test_sort_orders(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    small_new = make_torrent("s", seeders=99)
    big_old = TorrentData(
        hash="b",
        title="Big",
        uploader=None,
        description=None,
        category="Movies",
        tracker="x",
        details_url="d",
        download_url="l",
        seeders=1,
        peers=0,
        published_at=datetime.date(2020, 1, 1),
        size=999_000_000_000,
    )
    ids = await repo.upsert_torrents([small_new, big_old])
    await repo.create_search("qh2", ids)

    top_seeders = await repo.get_result_page("qh2", None, 0, "se")
    top_size = await repo.get_result_page("qh2", None, 0, "sz")
    top_date = await repo.get_result_page("qh2", None, 0, "dt")

    assert top_seeders is not None and top_seeders.hash == "s"
    assert top_size is not None and top_size.hash == "b"
    assert top_date is not None and top_date.hash == "s"


async def test_upsert_deduplicates_batch_with_repeated_hash(
    session: AsyncSession,
) -> None:
    # the same release returned by two indexers → identical hash in one batch.
    # a naive ON CONFLICT DO UPDATE raises CardinalityViolationError; the repo
    # must collapse the duplicate instead of crashing the whole search.
    repo = TorrentRepo(session)
    dup = make_torrent("same", seeders=10)
    dup_again = make_torrent("same", seeders=20)
    other = make_torrent("other", seeders=5)

    ids = await repo.upsert_torrents([dup, dup_again, other])

    # ids returned for every input position; duplicates share one id
    assert len(ids) == 3
    assert ids[0] == ids[1]
    assert len(set(ids)) == 2
    stored = await repo.get_torrent_by_hash("same")
    assert stored is not None


async def test_result_filters_apply(session: AsyncSession) -> None:
    from tj_bot.db.filters import GB, ResultFilters

    repo = TorrentRepo(session)
    big = TorrentData(
        hash="big",
        title="Big",
        uploader=None,
        description=None,
        category="Movies",
        tracker="t",
        details_url="d",
        download_url="l",
        seeders=100,
        peers=1,
        published_at=datetime.date.today(),
        size=10 * GB,
    )
    small_old = TorrentData(
        hash="small",
        title="Small",
        uploader=None,
        description=None,
        category="Movies",
        tracker="t",
        details_url="d",
        download_url="l",
        seeders=2,
        peers=0,
        published_at=datetime.date(2020, 1, 1),
        size=500_000_000,
    )
    ids = await repo.upsert_torrents([big, small_old])
    await repo.create_search("qhf", ids)

    # no filter -> both
    assert await repo.count_results("qhf", None, ResultFilters()) == 2
    # seeders >= 50 -> only big
    only_big = ResultFilters(seeders=3)
    assert await repo.count_results("qhf", None, only_big) == 1
    top = await repo.get_result_page("qhf", None, 0, "se", only_big)
    assert top is not None and top.hash == "big"
    # size 1-5GB -> neither (big is 10GB, small is 0.5GB)
    assert await repo.count_results("qhf", None, ResultFilters(size=2)) == 0
    # date within a year -> only big (small is 2020)
    assert await repo.count_results("qhf", None, ResultFilters(date=3)) == 1


async def test_user_access_lifecycle(session: AsyncSession) -> None:
    repo = TorrentRepo(session)

    user = await repo.touch_user(100, "alice", "Alice")
    assert user.user_id == 100 and not user.blocked and not user.admin
    # upsert refreshes identity (fresh session per request in production)
    await repo.touch_user(100, "alice2", "Alice B")
    session.expire_all()
    stored = await repo.get_user(100)
    assert stored is not None and stored.username == "alice2"

    await repo.set_blocked(100, True)
    assert await repo.is_blocked(100) is True
    await repo.set_admin(100, True)
    # promoting unblocks
    assert await repo.is_blocked(100) is False
    assert await repo.admin_user_ids() == [100]

    await repo.touch_user(200, "bob", "Bob")
    recent = await repo.list_recent_users()
    assert {u.user_id for u in recent} == {100, 200}


async def test_download_events_and_activity(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.touch_user(300, "carol", "Carol")
    await repo.record_download(300, "Movie A", "chat")
    await repo.record_download(300, "Movie B", "server")
    ids = await repo.upsert_torrents([make_torrent("x")])
    qid = await repo.create_search("qa", ids, query_text="movie")
    await repo.record_search_event(300, qid)
    await session.commit()

    searches, downloads = await repo.user_activity_counts(300)
    assert searches == 1 and downloads == 2

    dls = await repo.get_user_downloads(300)
    assert [(t, k) for t, k, _ in dls] == [("Movie B", "server"), ("Movie A", "chat")]

    srch = await repo.get_user_searches(300)
    assert srch[0][0] == "movie"
