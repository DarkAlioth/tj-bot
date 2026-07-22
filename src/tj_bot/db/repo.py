import datetime
from dataclasses import dataclass
from typing import cast

from sqlalchemy import ColumnElement, CursorResult, Delete, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tj_bot.db.models import SearchQuery, SearchQueryTorrent, Torrent


@dataclass(frozen=True)
class TorrentData:
    hash: str
    title: str
    uploader: str | None
    description: str | None
    category: str
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

    async def create_search(self, query_hash: str, torrent_ids: list[int]) -> None:
        insert_query = (
            pg_insert(SearchQuery)
            .values(hash=query_hash, result_count=len(torrent_ids))
            .on_conflict_do_nothing(index_elements=[SearchQuery.hash])
            .returning(SearchQuery.id)
        )
        query_id = (await self.session.execute(insert_query)).scalar()
        if query_id is None or not torrent_ids:
            return
        links = pg_insert(SearchQueryTorrent).values(
            [{"query_id": query_id, "torrent_id": tid} for tid in torrent_ids]
        )
        await self.session.execute(links.on_conflict_do_nothing())

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

    async def get_result_page(
        self, query_hash: str, category: str | None, offset: int
    ) -> Torrent | None:
        stmt = (
            select(Torrent)
            .select_from(Torrent, SearchQueryTorrent, SearchQuery)
            .where(*self._results_filter(query_hash, category))
            .order_by(Torrent.seeders.desc(), Torrent.peers.desc(), Torrent.id.desc())
            .limit(1)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

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
