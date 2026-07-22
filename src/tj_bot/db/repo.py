import datetime
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from sqlalchemy import ColumnElement, CursorResult, Delete, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tj_bot.db.models import (
    SearchEvent,
    SearchQuery,
    SearchQueryTorrent,
    Subscription,
    SubscriptionSeen,
    Torrent,
)


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

    async def upsert_torrents(self, items: list[TorrentData]) -> list[int]:
        """Insert new torrents or refresh volatile fields of known ones.

        Returns database ids in the order of ``items``.
        """
        if not items:
            return []
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
                for item in items
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
        self, query_hash: str, category: str | None
    ) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [
            SearchQueryTorrent.torrent_id == Torrent.id,
            SearchQueryTorrent.query_id == SearchQuery.id,
            SearchQuery.hash == query_hash,
        ]
        if category is not None:
            conditions.append(Torrent.category == category)
        return conditions

    async def count_results(self, query_hash: str, category: str | None) -> int:
        stmt = (
            select(func.count(Torrent.id))
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category))
        )
        return (await self.session.execute(stmt)).scalar_one()

    SORT_ORDERS: ClassVar[dict[str, tuple[ColumnElement[Any], ...]]] = {
        "se": (Torrent.seeders.desc(), Torrent.peers.desc(), Torrent.id.desc()),
        "sz": (Torrent.size.desc(), Torrent.id.desc()),
        "dt": (Torrent.published_at.desc(), Torrent.id.desc()),
    }

    async def get_result_page(
        self, query_hash: str, category: str | None, offset: int, order: str = "se"
    ) -> Torrent | None:
        stmt = (
            select(Torrent)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category))
            .order_by(*self.SORT_ORDERS.get(order, self.SORT_ORDERS["se"]))
            .limit(1)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

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

    async def get_stats(self, window: datetime.timedelta) -> dict[str, object]:
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
        return {
            "torrents": torrents,
            "queries": queries,
            "events_window": events,
            "users_window": users,
            "top_queries": top,
        }

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

    async def get_torrent_by_hash(self, torrent_hash: str) -> Torrent | None:
        stmt = select(Torrent).where(Torrent.hash == torrent_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

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

    async def get_result_hashes(self, query_hash: str) -> list[str]:
        stmt = (
            select(Torrent.hash)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, None))
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create_subscription(
        self, user_id: int, chat_id: int, query_text: str
    ) -> Subscription | None:
        """Create a subscription; None when a duplicate already exists."""
        existing = await self.session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.query_text == query_text,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None
        subscription = Subscription(
            user_id=user_id, chat_id=chat_id, query_text=query_text
        )
        self.session.add(subscription)
        await self.session.flush()
        return subscription

    async def count_subscriptions(self, user_id: int) -> int:
        stmt = select(func.count(Subscription.id)).where(
            Subscription.user_id == user_id
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def list_subscriptions(self, user_id: int) -> list[Subscription]:
        stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def all_subscriptions(self) -> list[Subscription]:
        stmt = select(Subscription).order_by(Subscription.id)
        return list((await self.session.execute(stmt)).scalars())

    async def delete_subscription(self, subscription_id: int, user_id: int) -> bool:
        result = await self._delete_rows(
            delete(Subscription).where(
                Subscription.id == subscription_id,
                Subscription.user_id == user_id,
            )
        )
        return result > 0

    async def seen_hashes(self, subscription_id: int) -> set[str]:
        stmt = select(SubscriptionSeen.torrent_hash).where(
            SubscriptionSeen.subscription_id == subscription_id
        )
        return set((await self.session.execute(stmt)).scalars())

    async def add_seen_hashes(self, subscription_id: int, hashes: list[str]) -> None:
        if not hashes:
            return
        stmt = pg_insert(SubscriptionSeen).values(
            [
                {"subscription_id": subscription_id, "torrent_hash": torrent_hash}
                for torrent_hash in set(hashes)
            ]
        )
        await self.session.execute(stmt.on_conflict_do_nothing())

    async def touch_subscription(self, subscription_id: int) -> None:
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is not None:
            subscription.last_checked_at = datetime.datetime.now(datetime.UTC)
            await self.session.flush()
