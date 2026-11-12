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
        for table in (
            "search_query_torrents",
            "search_queries",
            "torrents",
            "bot_users",
            "download_events",
            "favorites",
            "widget_messages",
        ):
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


async def test_cleanup_separates_cache_and_history_ttls(
    session: AsyncSession,
) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("old"), make_torrent("fresh")])
    await repo.create_search("qh-recent", [ids[0]])
    await repo.create_search("qh-ancient", [ids[1]])
    recent = await repo.get_search("qh-recent")
    assert recent is not None
    await repo.record_search_event(500, recent.id)
    await repo.record_download(500, "Old movie", "chat")
    await session.commit()
    await session.execute(
        text(
            "UPDATE torrents SET updated_at = now() - interval '10 days' "
            "WHERE hash = 'old'"
        )
    )
    await session.execute(
        text(
            "UPDATE search_queries SET created_at = now() - interval '10 days' "
            "WHERE hash = 'qh-recent'"
        )
    )
    await session.execute(
        text(
            "UPDATE search_queries SET created_at = now() - interval '40 days' "
            "WHERE hash = 'qh-ancient'"
        )
    )
    await session.execute(
        text("UPDATE download_events SET created_at = now() - interval '40 days'")
    )
    await session.commit()

    pool = async_sessionmaker(session.bind, expire_on_commit=False)
    deleted = await cleanup_once(
        pool, datetime.timedelta(days=7), datetime.timedelta(days=30)
    )

    # the 10-day-old cache torrent dies, but the 10-day-old query — and the
    # search history hanging off it — survives the shorter cache TTL
    assert deleted == 3  # old torrent + ancient query + ancient download event
    assert await repo.get_torrent_by_hash("old") is None
    assert await repo.get_torrent_by_hash("fresh") is not None
    assert await repo.get_search("qh-recent") is not None
    assert await repo.get_search("qh-ancient") is None
    searches, downloads = await repo.user_activity_counts(500)
    assert searches == 1
    assert downloads == 0


async def test_get_result_list_orders_by_seeders(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents(
        [
            make_torrent("low", seeders=1),
            make_torrent("top", seeders=99),
            make_torrent("mid", seeders=50),
        ]
    )
    await repo.create_search("qh-list", ids)

    torrents = await repo.get_result_list("qh-list", limit=2)

    assert [t.hash for t in torrents] == ["top", "mid"]


async def test_active_user_ids_excludes_blocked(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.touch_user(9001, "alice", "Alice")
    await repo.touch_user(9002, "bob", "Bob")
    await repo.set_blocked(9002, True)
    await session.commit()

    ids = await repo.active_user_ids()

    assert 9001 in ids
    assert 9002 not in ids


async def test_favorites_lifecycle_and_ttl_immunity(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.upsert_torrents([make_torrent("f1")])
    torrent = await repo.get_torrent_by_hash("f1")
    assert torrent is not None

    assert await repo.is_favorite(111, "f1") is False
    assert await repo.add_favorite(111, torrent) is True
    assert await repo.add_favorite(111, torrent) is False  # unique per user
    assert await repo.add_favorite(222, torrent) is True
    await session.commit()

    assert await repo.is_favorite(111, "f1") is True
    assert await repo.is_favorite(333, "f1") is False  # scoped per user

    assert await repo.count_favorites(111) == 1
    mine = await repo.list_favorites(111, limit=10)
    assert mine[0].title == torrent.title
    # authz: user id scopes the lookup
    assert await repo.get_favorite(222, mine[0].id) is None
    assert await repo.get_favorite(111, mine[0].id) is not None

    # favorites survive the cache TTL cleanup
    await session.execute(
        text("UPDATE torrents SET updated_at = now() - interval '10 days'")
    )
    await session.commit()
    pool = async_sessionmaker(session.bind, expire_on_commit=False)
    await cleanup_once(pool, datetime.timedelta(days=7), datetime.timedelta(days=30))
    assert await repo.get_torrent_by_hash("f1") is None
    assert await repo.count_favorites(111) == 1

    await repo.remove_favorite_by_hash(111, "f1")
    assert await repo.count_favorites(111) == 0
    await repo.remove_favorite(222, (await repo.list_favorites(222, 10))[0].id)
    assert await repo.count_favorites(222) == 0


async def test_db_size_reports_positive_bytes(session: AsyncSession) -> None:
    repo = TorrentRepo(session)

    assert await repo.db_size() > 0


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
    await repo.set_blocked(200, True)

    everyone = {u.user_id for u in await repo.list_users_page(50, 0)}
    assert {100, 200} <= everyone

    blocked = {u.user_id for u in await repo.list_users_page(50, 0, "blocked")}
    assert 200 in blocked
    assert 100 not in blocked
    assert await repo.count_users("blocked") >= 1

    # the admin flag and an .env super-admin id both land in the admins group
    admins = {u.user_id for u in await repo.list_users_page(50, 0, "admins", [200])}
    assert {100, 200} <= admins
    admins_flag_only = {u.user_id for u in await repo.list_users_page(50, 0, "admins")}
    assert 200 not in admins_flag_only

    # newest-first ordering and offset paging hold together
    first = await repo.list_users_page(1, 0)
    second = await repo.list_users_page(1, 1)
    assert first[0].user_id != second[0].user_id


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


async def test_widget_message_roundtrip(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.save_widget_message(1, 10, "search", {"qh": "abcd", "i": 0}, rev=1)
    await repo.save_widget_message(1, 10, "search", {"qh": "abcd", "i": 2}, rev=3)
    row = await repo.get_widget_message(1, 10)
    assert row is not None
    assert row.context == {"qh": "abcd", "i": 2}
    assert row.rev == 3
    assert await repo.update_widget_message(1, 10, rev=4) is True
    assert await repo.update_widget_message(1, 99, rev=4) is False
    refreshed = await repo.get_widget_message(1, 10)
    assert refreshed is not None and refreshed.rev == 4
    assert [m.message_id for m in await repo.widget_messages_of_kind("search")] == [10]
    await repo.delete_widget_message(1, 10)
    assert await repo.get_widget_message(1, 10) is None


async def test_widget_messages_purged_with_history(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.save_widget_message(1, 10, "hub", {})
    await session.execute(
        text("UPDATE widget_messages SET updated_at = now() - interval '40 days'")
    )
    await repo.delete_stale(
        cache_ttl=datetime.timedelta(days=7), history_ttl=datetime.timedelta(days=30)
    )
    assert await repo.get_widget_message(1, 10) is None


async def test_classic_ui_flag(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.touch_user(5, "u", "User")
    assert await repo.uses_classic_ui(5) is False
    await repo.set_classic_ui(5, True)
    assert await repo.uses_classic_ui(5) is True
    await repo.set_classic_ui(404, True)
    assert await repo.uses_classic_ui(404) is False


async def test_get_torrent_by_hash_prefix_is_scoped_and_prefers_exact(
    session: AsyncSession,
) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents(
        [
            make_torrent("abcdef0123456789aa"),
            make_torrent("abcdef01"),
            make_torrent("abcdef0199"),
        ]
    )
    await repo.create_search("q1", ids[:2], "one")
    await repo.create_search("q2", ids[2:], "two")

    exact = await repo.get_torrent_by_hash_prefix("q1", "abcdef01")
    assert exact is not None and exact.hash == "abcdef01"
    scoped = await repo.get_torrent_by_hash_prefix("q2", "abcdef01")
    assert scoped is not None and scoped.hash == "abcdef0199"
    assert await repo.get_torrent_by_hash_prefix("q1", "zzz") is None
    # LIKE wildcards in the prefix are literal
    assert await repo.get_torrent_by_hash_prefix("q1", "abcdef_1") is None


async def test_search_counts_by_day_and_top_limit(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("a")])
    first = await repo.create_search("q1", ids, query_text="one")
    second = await repo.create_search("q2", ids, query_text="two")
    await repo.record_search_event(1, first)
    await repo.record_search_event(1, first)
    await repo.record_search_event(2, second)
    await session.execute(
        text(
            "INSERT INTO search_events (user_id, query_id, created_at)"
            " VALUES (:user_id, :query_id, now() - interval '2 days')"
        ),
        {"user_id": 1, "query_id": first},
    )
    await session.execute(
        text(
            "INSERT INTO search_events (user_id, query_id, created_at)"
            " VALUES (:user_id, :query_id, now() - interval '30 days')"
        ),
        {"user_id": 1, "query_id": first},
    )
    await session.commit()

    counts = await repo.search_counts_by_day(7)

    assert len(counts) == 7
    assert counts[-1] == 3
    assert counts[-3] == 1
    assert sum(counts) == 4
    stats = await repo.get_stats(datetime.timedelta(days=7), top_limit=1)
    assert stats.top_queries == [("one", 3)]
    assert len((await repo.get_stats(datetime.timedelta(days=7))).top_queries) == 2


async def test_favorites_updated_and_seen_flags(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    await repo.upsert_torrents([make_torrent("u1"), make_torrent("u2")])
    for torrent_hash in ("u1", "u2"):
        torrent = await repo.get_torrent_by_hash(torrent_hash)
        assert torrent is not None
        await repo.add_favorite(111, torrent)
    await session.commit()
    first, second = await repo.list_favorites(111, limit=10)
    assert first.updated_at is None and first.seen_at is None
    assert await repo.count_updated_favorites(111) == 0
    assert await repo.list_favorites(111, limit=10, updated_only=True) == []

    # the watcher refreshed one snapshot: it is "updated" until seen
    await repo.update_favorite_snapshot(
        first.id, make_torrent(first.torrent_hash, seeders=99)
    )
    assert first.updated_at is not None
    assert await repo.count_updated_favorites(111) == 1
    assert [f.id for f in await repo.list_favorites(111, 10, updated_only=True)] == [
        first.id
    ]
    assert await repo.count_updated_favorites(222) == 0  # scoped per user

    await repo.mark_favorites_seen(111, first.id)
    await session.refresh(first)
    assert first.seen_at is not None and first.seen_at >= first.updated_at
    assert await repo.count_updated_favorites(111) == 0

    # a later refresh flags it again; marking all clears every row
    for favorite in (first, second):
        await repo.update_favorite_snapshot(
            favorite.id, make_torrent(favorite.torrent_hash, seeders=100)
        )
    assert await repo.count_updated_favorites(111) == 2
    await repo.mark_favorites_seen(111)
    assert await repo.count_updated_favorites(111) == 0


async def test_favorites_columns_from_migration(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema"
                ".columns WHERE table_name = 'favorites'"
                " AND column_name IN ('updated_at', 'seen_at') ORDER BY column_name"
            )
        )
    ).all()
    assert [tuple(row) for row in rows] == [
        ("seen_at", "timestamp with time zone", "YES"),
        ("updated_at", "timestamp with time zone", "YES"),
    ]


async def test_history_entries_and_clear(session: AsyncSession) -> None:
    repo = TorrentRepo(session)
    ids = await repo.upsert_torrents([make_torrent("h1")])
    first_id = await repo.create_search("qh1", ids, query_text="dune")
    second_id = await repo.create_search("qh2", [], query_text="silo")
    await repo.record_search_event(111, first_id)
    await repo.record_search_event(111, second_id)
    await repo.record_search_event(111, first_id)  # a repeat: unique by text
    await repo.record_search_event(222, first_id)
    await session.commit()

    entries = await repo.get_user_history_entries(111)
    assert [(e.query_id, e.text, e.result_count) for e in entries] == [
        (first_id, "dune", 1),
        (second_id, "silo", 0),
    ]
    assert entries[0].created_at.tzinfo is not None
    assert await repo.get_user_history_entries(111, limit=1) == entries[:1]

    assert await repo.clear_user_history(111) == 3
    assert await repo.get_user_history_entries(111) == []
    assert await repo.get_user_history(222) == [(first_id, "dune")]  # untouched
    assert await repo.clear_user_history(111) == 0
