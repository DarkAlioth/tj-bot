import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Delete,
    delete,
    func,
    or_,
    select,
    update,
)
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
    WidgetMessage,
)


@dataclass(frozen=True)
class SearchStats:
    torrents: int
    queries: int
    events_window: int
    users_window: int
    top_queries: list[tuple[str, int]]


@dataclass(frozen=True)
class HistoryEntry:
    query_id: int
    text: str
    created_at: datetime.datetime
    result_count: int


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

    async def get_user_history_entries(
        self, user_id: int, limit: int = 10
    ) -> list[HistoryEntry]:
        """Recent searches of the user, unique by text, newest first."""
        stmt = (
            select(
                SearchQuery.id,
                SearchQuery.query_text,
                SearchEvent.created_at,
                SearchQuery.result_count,
            )
            .join(SearchEvent, SearchEvent.query_id == SearchQuery.id)
            .where(SearchEvent.user_id == user_id, SearchQuery.query_text.is_not(None))
            .order_by(SearchEvent.created_at.desc())
            .limit(50)
        )
        rows = (await self.session.execute(stmt)).all()
        seen: set[str] = set()
        history: list[HistoryEntry] = []
        for query_id, query_text, created_at, result_count in rows:
            if query_text in seen:
                continue
            seen.add(query_text)
            history.append(HistoryEntry(query_id, query_text, created_at, result_count))
            if len(history) >= limit:
                break
        return history

    async def get_user_history(
        self, user_id: int, limit: int = 10
    ) -> list[tuple[int, str]]:
        """Recent unique (query_id, query_text) pairs for the user, newest first."""
        entries = await self.get_user_history_entries(user_id, limit)
        return [(entry.query_id, entry.text) for entry in entries]

    async def clear_user_history(self, user_id: int) -> int:
        """Forget the user's searches; returns the number of deleted events."""
        return await self._delete_rows(
            delete(SearchEvent).where(SearchEvent.user_id == user_id)
        )

    async def get_query_text(self, query_id: int) -> str | None:
        stmt = select(SearchQuery.query_text).where(SearchQuery.id == query_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_stats(
        self, window: datetime.timedelta, top_limit: int = 5
    ) -> SearchStats:
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
            .limit(top_limit)
        )
        top = [(row[0], row[1]) for row in (await self.session.execute(top_stmt)).all()]
        return SearchStats(
            torrents=torrents,
            queries=queries,
            events_window=events,
            users_window=users,
            top_queries=top,
        )

    async def search_counts_by_day(self, days: int) -> list[int]:
        """Searches per UTC calendar day for the last ``days`` days, oldest first."""
        first = datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(
            days=days - 1
        )
        start = datetime.datetime.combine(first, datetime.time.min, tzinfo=datetime.UTC)
        day = func.date(func.timezone("UTC", SearchEvent.created_at))
        stmt = (
            select(day, func.count(SearchEvent.id))
            .where(SearchEvent.created_at >= start)
            .group_by(day)
        )
        counts = {row[0]: row[1] for row in (await self.session.execute(stmt)).all()}
        return [counts.get(first + datetime.timedelta(days=i), 0) for i in range(days)]

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

    async def get_torrent_by_hash_prefix(
        self, query_hash: str, prefix: str
    ) -> Torrent | None:
        """A result of the search whose hash starts with ``prefix``.

        Widget requests carry only the first 8 hex characters of a hash; the
        result set keeps the prefix unambiguous. An exact hash wins.
        """
        stmt = (
            select(Torrent)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(
                *self._results_filter(query_hash, None),
                Torrent.hash.startswith(prefix, autoescape=True),
            )
            .order_by((Torrent.hash == prefix).desc(), Torrent.hash)
            .limit(1)
        )
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

    async def list_all_favorites(self) -> list[Favorite]:
        """Every favorite of every user, for the tracker-update watcher."""
        stmt = select(Favorite).order_by(Favorite.id)
        return list((await self.session.execute(stmt)).scalars())

    async def update_favorite_snapshot(
        self, favorite_id: int, item: TorrentData
    ) -> None:
        """Refresh a favorite from a fresh tracker result (same topic)."""
        favorite = await self.session.get(Favorite, favorite_id)
        if favorite is None:
            return
        favorite.title = item.title
        favorite.torrent_hash = item.hash
        favorite.download_url = item.download_url
        favorite.seeders = item.seeders
        favorite.size = item.size
        favorite.published_at = item.published_at
        favorite.updated_at = datetime.datetime.now(datetime.UTC)
        await self.session.flush()

    @staticmethod
    def _favorite_updated() -> ColumnElement[bool]:
        """Changed on the tracker since the owner last acted on it."""
        return Favorite.updated_at.is_not(None) & (
            Favorite.seen_at.is_(None) | (Favorite.updated_at > Favorite.seen_at)
        )

    async def mark_favorites_seen(
        self, user_id: int, favorite_id: int | None = None
    ) -> None:
        """Clear the "updated" flag of one favorite or of all the user's."""
        stmt = (
            update(Favorite)
            .where(Favorite.user_id == user_id)
            .values(seen_at=datetime.datetime.now(datetime.UTC))
        )
        if favorite_id is not None:
            stmt = stmt.where(Favorite.id == favorite_id)
        await self.session.execute(stmt)

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
        self, user_id: int, limit: int, offset: int = 0, updated_only: bool = False
    ) -> list[Favorite]:
        stmt = (
            select(Favorite)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc(), Favorite.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if updated_only:
            stmt = stmt.where(self._favorite_updated())
        return list((await self.session.execute(stmt)).scalars())

    async def count_favorites(self, user_id: int) -> int:
        stmt = select(func.count(Favorite.id)).where(Favorite.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def count_updated_favorites(self, user_id: int) -> int:
        stmt = select(func.count(Favorite.id)).where(
            Favorite.user_id == user_id, self._favorite_updated()
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def _delete_rows(self, statement: Delete) -> int:
        result = await self.session.execute(statement)
        # DELETE always yields a CursorResult, which carries rowcount
        return int(cast(CursorResult[int], result).rowcount or 0)

    async def delete_stale(
        self, cache_ttl: datetime.timedelta, history_ttl: datetime.timedelta
    ) -> int:
        """Purge the result cache after ``cache_ttl`` and history after ``history_ttl``.

        Search queries live as long as the history: search_events cascade
        with them, and deleting queries on the short cache TTL used to wipe
        the users' search history within a week.
        """
        now = datetime.datetime.now(datetime.UTC)
        cache_cutoff = now - cache_ttl
        history_cutoff = now - history_ttl
        deleted_queries = await self._delete_rows(
            delete(SearchQuery).where(SearchQuery.created_at < history_cutoff)
        )
        deleted_torrents = await self._delete_rows(
            delete(Torrent).where(Torrent.updated_at < cache_cutoff)
        )
        deleted_downloads = await self._delete_rows(
            delete(DownloadEvent).where(DownloadEvent.created_at < history_cutoff)
        )
        # a widget untouched for the whole history window is dead in the chat
        deleted_widgets = await self._delete_rows(
            delete(WidgetMessage).where(WidgetMessage.updated_at < history_cutoff)
        )
        return deleted_queries + deleted_torrents + deleted_downloads + deleted_widgets

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

    def _users_conditions(
        self, group: str, super_ids: Sequence[int]
    ) -> list[ColumnElement[bool]]:
        """Filter for a /users group: "all", "admins" or "blocked".

        Super-admins live in .env, not in the admin flag, so the admins group
        matches either source.
        """
        if group == "admins":
            admin_flag = BotUser.admin.is_(True)
            if super_ids:
                return [or_(admin_flag, BotUser.user_id.in_(super_ids))]
            return [admin_flag]
        if group == "blocked":
            return [BotUser.blocked.is_(True)]
        if group == "regular":
            conditions: list[ColumnElement[bool]] = [
                BotUser.admin.is_(False),
                BotUser.blocked.is_(False),
            ]
            if super_ids:
                conditions.append(BotUser.user_id.notin_(super_ids))
            return conditions
        return []

    async def count_users(
        self, group: str = "all", super_ids: Sequence[int] = ()
    ) -> int:
        stmt = select(func.count(BotUser.user_id)).where(
            *self._users_conditions(group, super_ids)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def list_users_page(
        self,
        limit: int,
        offset: int,
        group: str = "all",
        super_ids: Sequence[int] = (),
    ) -> list[BotUser]:
        stmt = (
            select(BotUser)
            .where(*self._users_conditions(group, super_ids))
            .order_by(BotUser.last_seen.desc(), BotUser.user_id.desc())
            .limit(limit)
            .offset(offset)
        )
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

    async def set_classic_ui(self, user_id: int, enabled: bool) -> None:
        user = await self.get_user(user_id)
        if user is not None:
            user.classic_ui = enabled
            await self.session.flush()

    async def uses_classic_ui(self, user_id: int) -> bool:
        stmt = select(BotUser.classic_ui).where(BotUser.user_id == user_id)
        return bool((await self.session.execute(stmt)).scalar_one_or_none())

    async def save_widget_message(
        self,
        chat_id: int,
        message_id: int,
        kind: str,
        context: dict[str, Any],
        rev: int = 0,
    ) -> None:
        """Remember what a sent widget is about (its queries carry no state)."""
        stmt = (
            pg_insert(WidgetMessage)
            .values(
                chat_id=chat_id,
                message_id=message_id,
                kind=kind,
                context=context,
                rev=rev,
            )
            .on_conflict_do_update(
                index_elements=[WidgetMessage.chat_id, WidgetMessage.message_id],
                set_={"kind": kind, "context": context, "rev": rev},
            )
        )
        await self.session.execute(stmt)

    async def get_widget_message(
        self, chat_id: int, message_id: int
    ) -> WidgetMessage | None:
        return await self.session.get(WidgetMessage, (chat_id, message_id))

    async def update_widget_message(
        self,
        chat_id: int,
        message_id: int,
        context: dict[str, Any] | None = None,
        rev: int | None = None,
    ) -> bool:
        """Replace the context and/or revision; False when the message is unknown."""
        row = await self.get_widget_message(chat_id, message_id)
        if row is None:
            return False
        if context is not None:
            row.context = context
        if rev is not None:
            row.rev = rev
        await self.session.flush()
        return True

    async def delete_widget_message(self, chat_id: int, message_id: int) -> None:
        await self.session.execute(
            delete(WidgetMessage).where(
                WidgetMessage.chat_id == chat_id, WidgetMessage.message_id == message_id
            )
        )

    async def widget_messages_of_kind(
        self, kind: str, limit: int = 200
    ) -> list[WidgetMessage]:
        """Most recently touched widgets of one kind (for enrichment pushes)."""
        stmt = (
            select(WidgetMessage)
            .where(WidgetMessage.kind == kind)
            .order_by(WidgetMessage.updated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())
