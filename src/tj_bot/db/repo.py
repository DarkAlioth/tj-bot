import datetime
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from sqlalchemy import ColumnElement, CursorResult, Delete, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tj_bot.db.filters import ResultFilters
from tj_bot.db.models import (
    BotUser,
    DownloadEvent,
    Favorite,
    SearchEvent,
    SearchQuery,
    SearchQueryTorrent,
    Torrent,
)


@dataclass(frozen=True)
class SearchStats:
    torrents: int
    queries: int
    events_window: int
    users_window: int
    top_queries: list[tuple[str, int]]


@dataclass(frozen=True)
class TorrentData:
    hash: str
    title: str
    uploader: str | None
    description: str | None
    category: str
    tracker: str
    details_url: str
    download_url: str
    seeders: int
    peers: int
    published_at: datetime.date
    size: int


class TorrentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        """Commit the per-update transaction early, releasing its row locks.

        Handlers call this right before long external I/O (Jackett searches
        and downloads): an open transaction would pin a pool connection and
        the caller's ``bot_users`` row lock from ``touch_user`` for the whole
        call, serializing every other update from the same user behind it.
        """
        await self.session.commit()

    async def upsert_torrents(self, items: list[TorrentData]) -> list[int]:
        """Insert new torrents or refresh volatile fields of known ones.

        Returns database ids in the order of ``items``.
        """
        if not items:
            return []
        # A batch ON CONFLICT DO UPDATE cannot touch the same row twice, so
        # collapse duplicate hashes within the batch — Jackett returns the same
        # release from multiple indexers, and the hash (title+tracker+date) then
        # repeats. Keep the first occurrence.
        deduped: dict[str, TorrentData] = {}
        for item in items:
            deduped.setdefault(item.hash, item)
        stmt = pg_insert(Torrent).values(
            [
                {
                    "hash": item.hash,
                    "title": item.title,
                    "uploader": item.uploader,
                    "description": item.description,
                    "category": item.category,
                    "tracker": item.tracker,
                    "details_url": item.details_url,
                    "download_url": item.download_url,
                    "seeders": item.seeders,
                    "peers": item.peers,
                    "published_at": item.published_at,
                    "size": item.size,
                }
                for item in deduped.values()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Torrent.hash],
            set_={
                "seeders": stmt.excluded.seeders,
                "peers": stmt.excluded.peers,
                "download_url": stmt.excluded.download_url,
                "updated_at": func.now(),
            },
        )
        result = await self.session.execute(stmt.returning(Torrent.id, Torrent.hash))
        id_by_hash = {row.hash: row.id for row in result}
        return [id_by_hash[item.hash] for item in items]

    async def create_search(
        self, query_hash: str, torrent_ids: list[int], query_text: str | None = None
    ) -> int:
        """Create the search record; returns its id (existing or new)."""
        insert_query = (
            pg_insert(SearchQuery)
            .values(
                hash=query_hash,
                result_count=len(torrent_ids),
                query_text=query_text,
            )
            .on_conflict_do_nothing(index_elements=[SearchQuery.hash])
            .returning(SearchQuery.id)
        )
        query_id = (await self.session.execute(insert_query)).scalar()
        if query_id is None:
            existing = await self.get_search(query_hash)
            assert existing is not None  # noqa: S101  # conflict implies presence
            return existing.id
        if torrent_ids:
            links = pg_insert(SearchQueryTorrent).values(
                [{"query_id": query_id, "torrent_id": tid} for tid in torrent_ids]
            )
            await self.session.execute(links.on_conflict_do_nothing())
        return query_id

    async def get_search(self, query_hash: str) -> SearchQuery | None:
        stmt = select(SearchQuery).where(SearchQuery.hash == query_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def _results_filter(
        self,
        query_hash: str,
        category: str | None,
        filters: ResultFilters | None = None,
    ) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [
            SearchQueryTorrent.torrent_id == Torrent.id,
            SearchQueryTorrent.query_id == SearchQuery.id,
            SearchQuery.hash == query_hash,
        ]
        if category is not None:
            conditions.append(Torrent.category == category)
        if filters is not None:
            conditions.extend(filters.conditions())
        return conditions

    async def count_results(
        self,
        query_hash: str,
        category: str | None,
        filters: ResultFilters | None = None,
    ) -> int:
        stmt = (
            select(func.count(Torrent.id))
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category, filters))
        )
        return (await self.session.execute(stmt)).scalar_one()

    SORT_ORDERS: ClassVar[dict[str, tuple[ColumnElement[Any], ...]]] = {
        "se": (Torrent.seeders.desc(), Torrent.peers.desc(), Torrent.id.desc()),
        "sz": (Torrent.size.desc(), Torrent.id.desc()),
        "za": (Torrent.size.asc(), Torrent.id.desc()),
        "dt": (Torrent.published_at.desc(), Torrent.id.desc()),
        "da": (Torrent.published_at.asc(), Torrent.id.desc()),
    }

    async def get_result_page(
        self,
        query_hash: str,
        category: str | None,
        offset: int,
        order: str = "se",
        filters: ResultFilters | None = None,
    ) -> Torrent | None:
        stmt = (
            select(Torrent)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category, filters))
            .order_by(*self.SORT_ORDERS.get(order, self.SORT_ORDERS["se"]))
            .limit(1)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_result_list(
        self,
        query_hash: str,
        limit: int,
        offset: int = 0,
        category: str | None = None,
        order: str = "se",
        filters: ResultFilters | None = None,
    ) -> list[Torrent]:
        """A page of results of a cached search in the requested order."""
        stmt = (
            select(Torrent)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category, filters))
            .order_by(*self.SORT_ORDERS.get(order, self.SORT_ORDERS["se"]))
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def find_recent_search(
        self, query_text: str, max_age: datetime.timedelta
    ) -> SearchQuery | None:
        cutoff = datetime.datetime.now(datetime.UTC) - max_age
        stmt = (
            select(SearchQuery)
            .where(
                SearchQuery.query_text == query_text,
                SearchQuery.created_at >= cutoff,
            )
            .order_by(SearchQuery.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def record_search_event(self, user_id: int, query_id: int) -> None:
        self.session.add(SearchEvent(user_id=user_id, query_id=query_id))
        await self.session.flush()

    async def get_user_history(
        self, user_id: int, limit: int = 10
    ) -> list[tuple[int, str]]:
        """Recent unique (query_id, query_text) pairs for the user, newest first."""
        stmt = (
            select(SearchQuery.id, SearchQuery.query_text, SearchEvent.created_at)
            .join(SearchEvent, SearchEvent.query_id == SearchQuery.id)
            .where(SearchEvent.user_id == user_id, SearchQuery.query_text.is_not(None))
            .order_by(SearchEvent.created_at.desc())
            .limit(50)
        )
        rows = (await self.session.execute(stmt)).all()
        seen: set[str] = set()
        history: list[tuple[int, str]] = []
        for query_id, query_text, _ in rows:
            if query_text in seen:
                continue
            seen.add(query_text)
            history.append((query_id, query_text))
            if len(history) >= limit:
                break
        return history

    async def get_query_text(self, query_id: int) -> str | None:
        stmt = select(SearchQuery.query_text).where(SearchQuery.id == query_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_stats(self, window: datetime.timedelta) -> SearchStats:
        cutoff = datetime.datetime.now(datetime.UTC) - window
        torrents = (
            await self.session.execute(select(func.count(Torrent.id)))
        ).scalar_one()
        queries = (
            await self.session.execute(select(func.count(SearchQuery.id)))
        ).scalar_one()
        events = (
            await self.session.execute(
                select(func.count(SearchEvent.id)).where(
                    SearchEvent.created_at >= cutoff
                )
            )
        ).scalar_one()
        users = (
            await self.session.execute(
                select(func.count(func.distinct(SearchEvent.user_id))).where(
                    SearchEvent.created_at >= cutoff
                )
            )
        ).scalar_one()
        top_stmt = (
            select(SearchQuery.query_text, func.count(SearchEvent.id).label("cnt"))
            .join(SearchEvent, SearchEvent.query_id == SearchQuery.id)
            .where(
                SearchEvent.created_at >= cutoff, SearchQuery.query_text.is_not(None)
            )
            .group_by(SearchQuery.query_text)
            .order_by(func.count(SearchEvent.id).desc())
            .limit(5)
        )
        top = [(row[0], row[1]) for row in (await self.session.execute(top_stmt)).all()]
        return SearchStats(
            torrents=torrents,
            queries=queries,
            events_window=events,
            users_window=users,
            top_queries=top,
        )

    async def get_categories(self, query_hash: str) -> list[tuple[str, int]]:
        stmt = (
            select(Torrent.category, func.count(Torrent.id))
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, None))
            .group_by(Torrent.category)
            .order_by(Torrent.category)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def db_size(self) -> int:
        stmt = select(func.pg_database_size(func.current_database()))
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_torrent_by_hash(self, torrent_hash: str) -> Torrent | None:
        stmt = select(Torrent).where(Torrent.hash == torrent_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_favorite(self, user_id: int, torrent: Torrent) -> bool:
        """Star a cached torrent; returns False when already starred."""
        stmt = (
            pg_insert(Favorite)
            .values(
                user_id=user_id,
                torrent_hash=torrent.hash,
                title=torrent.title,
                category=torrent.category,
                tracker=torrent.tracker,
                details_url=torrent.details_url,
                download_url=torrent.download_url,
                seeders=torrent.seeders,
                size=torrent.size,
                published_at=torrent.published_at,
            )
            .on_conflict_do_nothing(
                index_elements=[Favorite.user_id, Favorite.torrent_hash]
            )
            .returning(Favorite.id)
        )
        return (await self.session.execute(stmt)).scalar() is not None

    async def is_favorite(self, user_id: int, torrent_hash: str) -> bool:
        stmt = (
            select(Favorite.id)
            .where(Favorite.user_id == user_id, Favorite.torrent_hash == torrent_hash)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def remove_favorite_by_hash(self, user_id: int, torrent_hash: str) -> None:
        await self.session.execute(
            delete(Favorite).where(
                Favorite.user_id == user_id, Favorite.torrent_hash == torrent_hash
            )
        )

    async def remove_favorite(self, user_id: int, favorite_id: int) -> None:
        await self.session.execute(
            delete(Favorite).where(
                Favorite.user_id == user_id, Favorite.id == favorite_id
            )
        )

    async def get_favorite(self, user_id: int, favorite_id: int) -> Favorite | None:
        """The user's own favorite by id — the user filter doubles as authz."""
        stmt = select(Favorite).where(
            Favorite.user_id == user_id, Favorite.id == favorite_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_favorites(
        self, user_id: int, limit: int, offset: int = 0
    ) -> list[Favorite]:
        stmt = (
            select(Favorite)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc(), Favorite.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def count_favorites(self, user_id: int) -> int:
        stmt = select(func.count(Favorite.id)).where(Favorite.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def _delete_rows(self, statement: Delete) -> int:
        result = await self.session.execute(statement)
        # DELETE always yields a CursorResult, which carries rowcount
        return int(cast(CursorResult[int], result).rowcount or 0)

    async def delete_stale(self, ttl: datetime.timedelta) -> int:
        """Delete queries and torrents untouched for longer than ``ttl``."""
        cutoff = datetime.datetime.now(datetime.UTC) - ttl
        deleted_queries = await self._delete_rows(
            delete(SearchQuery).where(SearchQuery.created_at < cutoff)
        )
        deleted_torrents = await self._delete_rows(
            delete(Torrent).where(Torrent.updated_at < cutoff)
        )
        return deleted_queries + deleted_torrents

    async def touch_user(
        self, user_id: int, username: str | None, full_name: str | None
    ) -> BotUser:
        """Upsert the user's identity on every interaction; return the row."""
        stmt = (
            pg_insert(BotUser)
            .values(user_id=user_id, username=username, full_name=full_name)
            .on_conflict_do_update(
                index_elements=[BotUser.user_id],
                set_={
                    "username": username,
                    "full_name": full_name,
                    "last_seen": func.now(),
                },
            )
            .returning(BotUser)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def get_user(self, user_id: int) -> BotUser | None:
        return await self.session.get(BotUser, user_id)

    async def is_blocked(self, user_id: int) -> bool:
        stmt = select(BotUser.blocked).where(BotUser.user_id == user_id)
        return bool((await self.session.execute(stmt)).scalar_one_or_none())

    async def admin_user_ids(self) -> list[int]:
        stmt = select(BotUser.user_id).where(BotUser.admin.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def active_user_ids(self) -> list[int]:
        stmt = select(BotUser.user_id).where(BotUser.blocked.is_(False))
        return list((await self.session.execute(stmt)).scalars())

    async def list_recent_users(self, limit: int = 15) -> list[BotUser]:
        stmt = select(BotUser).order_by(BotUser.last_seen.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        user = await self.session.get(BotUser, user_id)
        if user is not None:
            user.blocked = blocked
            await self.session.flush()

    async def set_admin(self, user_id: int, admin: bool) -> None:
        user = await self.session.get(BotUser, user_id)
        if user is not None:
            user.admin = admin
            if admin:
                user.blocked = False
            await self.session.flush()

    async def record_download(self, user_id: int, title: str, kind: str) -> None:
        self.session.add(DownloadEvent(user_id=user_id, title=title[:512], kind=kind))
        await self.session.flush()

    async def get_user_searches(
        self, user_id: int, limit: int = 10
    ) -> list[tuple[str, datetime.datetime]]:
        stmt = (
            select(SearchQuery.query_text, SearchEvent.created_at)
            .join(SearchEvent, SearchEvent.query_id == SearchQuery.id)
            .where(SearchEvent.user_id == user_id, SearchQuery.query_text.is_not(None))
            .order_by(SearchEvent.created_at.desc())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]

    async def get_user_downloads(
        self, user_id: int, limit: int = 10
    ) -> list[tuple[str, str, datetime.datetime]]:
        stmt = (
            select(DownloadEvent.title, DownloadEvent.kind, DownloadEvent.created_at)
            .where(DownloadEvent.user_id == user_id)
            .order_by(DownloadEvent.created_at.desc(), DownloadEvent.id.desc())
            .limit(limit)
        )
        return [
            (row[0], row[1], row[2]) for row in (await self.session.execute(stmt)).all()
        ]

    async def user_activity_counts(self, user_id: int) -> tuple[int, int]:
        searches = (
            await self.session.execute(
                select(func.count(SearchEvent.id)).where(SearchEvent.user_id == user_id)
            )
        ).scalar_one()
        downloads = (
            await self.session.execute(
                select(func.count(DownloadEvent.id)).where(
                    DownloadEvent.user_id == user_id
                )
            )
        ).scalar_one()
        return searches, downloads
