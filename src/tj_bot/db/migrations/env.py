import asyncio

from alembic import context
from sqlalchemy import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tj_bot.db.models import Base

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(url: URL) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    url = context.config.attributes["sqlalchemy_url"]
    asyncio.run(run_async_migrations(url))


run_migrations_online()
