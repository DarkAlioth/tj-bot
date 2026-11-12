import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, and_, delete, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tj_bot.db.models import TorrentMetaRow

# how long a scrape result is trusted before the page is fetched again
STATUS_TTL: dict[str, datetime.timedelta] = {
    "ok": datetime.timedelta(days=30),
    "unsupported": datetime.timedelta(days=30),
    "not_found": datetime.timedelta(days=7),
    "error": datetime.timedelta(days=1),
    "challenge": datetime.timedelta(hours=1),
    "login": datetime.timedelta(hours=1),
}
DEFAULT_TTL = datetime.timedelta(days=1)


def is_expired(row: TorrentMetaRow, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.UTC)
    return row.fetched_at + STATUS_TTL.get(row.status, DEFAULT_TTL) < now


class MetaRepo:
    """Cache of scraped tracker pages (``torrent_meta``), keyed by torrent hash."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, torrent_hash: str) -> TorrentMetaRow | None:
        return await self.session.get(TorrentMetaRow, torrent_hash)

    async def upsert(
        self,
        *,
        torrent_hash: str,
        tracker: str,
        details_url: str,
        status: str,
        meta: dict[str, Any],
        error: str | None = None,
    ) -> TorrentMetaRow:
        """Replace the row for ``torrent_hash``."""
        values = {
            "hash": torrent_hash,
            "tracker": tracker,
            "details_url": details_url,
            "status": status,
            "fetched_at": func.now(),
            "meta": meta,
            "error": error,
        }
        stmt = (
            pg_insert(TorrentMetaRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[TorrentMetaRow.hash],
                set_={key: value for key, value in values.items() if key != "hash"},
            )
            .returning(TorrentMetaRow)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def purge_expired(self) -> int:
        """Delete rows older than their status TTL; returns the row count."""
        now = datetime.datetime.now(datetime.UTC)
        known = list(STATUS_TTL)
        conditions = [
            and_(TorrentMetaRow.status == status, TorrentMetaRow.fetched_at < now - ttl)
            for status, ttl in STATUS_TTL.items()
        ]
        conditions.append(
            and_(
                TorrentMetaRow.status.notin_(known),
                TorrentMetaRow.fetched_at < now - DEFAULT_TTL,
            )
        )
        result = await self.session.execute(
            delete(TorrentMetaRow).where(or_(*conditions))
        )
        # DELETE always yields a CursorResult, which carries rowcount
        return int(cast(CursorResult[int], result).rowcount or 0)
