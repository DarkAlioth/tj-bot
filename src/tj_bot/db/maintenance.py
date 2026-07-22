import asyncio
import datetime
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.db.repo import TorrentRepo

logger = logging.getLogger(__name__)


async def cleanup_once(
    session_pool: async_sessionmaker[AsyncSession], ttl: datetime.timedelta
) -> int:
    async with session_pool() as session:
        deleted = await TorrentRepo(session).delete_stale(ttl)
        await session.commit()
    return deleted


async def cleanup_loop(
    session_pool: async_sessionmaker[AsyncSession],
    ttl: datetime.timedelta,
    interval_seconds: int,
) -> None:
    """Periodically purge cache entries older than the TTL."""
    while True:
        try:
            deleted = await cleanup_once(session_pool, ttl)
            if deleted:
                logger.info("Cache cleanup removed %s rows", deleted)
        except SQLAlchemyError:
            logger.exception("Cache cleanup failed")
        await asyncio.sleep(interval_seconds)
