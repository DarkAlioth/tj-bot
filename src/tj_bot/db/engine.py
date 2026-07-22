from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tj_bot.config import Settings


def build_db_url(settings: Settings) -> URL:
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.postgres_db,
    )


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(build_db_url(settings), pool_pre_ping=True)


def create_session_pool(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
