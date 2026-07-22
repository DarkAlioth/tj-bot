from tj_bot.config import Settings
from tj_bot.db.engine import build_db_url, create_engine, create_session_pool


def make_settings() -> Settings:
    # type ignore: kwargs validated by pydantic; signature hides settings sources
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        bot_token="42:TEST",
        postgres_user="u",
        postgres_password="p@ss w",
        postgres_db="d",
        db_host="db",
    )


def test_build_db_url_components() -> None:
    url = build_db_url(make_settings())

    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "db"
    assert url.port == 5432
    assert url.database == "d"
    assert url.password == "p@ss w"


async def test_create_engine_and_session_pool() -> None:
    engine = create_engine(make_settings())
    pool = create_session_pool(engine)

    assert pool.kw["expire_on_commit"] is False
    await engine.dispose()
