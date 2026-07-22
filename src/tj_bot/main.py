import asyncio
import datetime
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from tj_bot.config import (
    AppConfig,
    JackettRuntime,
    Settings,
    load_settings,
    wait_for_jackett_api_key,
)
from tj_bot.db.engine import build_db_url, create_engine, create_session_pool
from tj_bot.db.maintenance import cleanup_loop
from tj_bot.db.migrate import run_migrations
from tj_bot.handlers import routers_list
from tj_bot.middlewares.config import ConfigMiddleware
from tj_bot.middlewares.database import DatabaseMiddleware
from tj_bot.services import broadcaster
from tj_bot.services.jackett import JackettClient

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d - %(message)s",
    )


def register_global_middlewares(
    dp: Dispatcher, middlewares: list[ConfigMiddleware | DatabaseMiddleware]
) -> None:
    for middleware in middlewares:
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)


async def main(settings: Settings) -> None:
    jackett_api_key = await wait_for_jackett_api_key(settings)
    config = AppConfig(
        settings=settings,
        jackett=JackettRuntime(url=settings.jackett_url, api_key=jackett_api_key),
    )

    engine = create_engine(settings)
    session_pool = create_session_pool(engine)
    jackett = JackettClient(
        base_url=settings.jackett_url,
        api_key=jackett_api_key,
        timeout_seconds=settings.jackett_timeout_seconds,
        download_max_bytes=settings.download_max_bytes,
    )

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp["jackett"] = jackett
    dp.include_routers(*routers_list)
    register_global_middlewares(
        dp, [ConfigMiddleware(config), DatabaseMiddleware(session_pool)]
    )

    cleanup_task = asyncio.create_task(
        cleanup_loop(
            session_pool,
            ttl=datetime.timedelta(days=settings.cache_ttl_days),
            interval_seconds=settings.cleanup_interval_seconds,
        )
    )
    try:
        await broadcaster.broadcast(bot, config.admin_ids, "Бот был запущен")
        logger.info("Starting polling")
        await dp.start_polling(bot)
    finally:
        cleanup_task.cancel()
        await jackett.close()
        await engine.dispose()


def run() -> None:
    setup_logging()
    settings = load_settings()
    run_migrations(build_db_url(settings))
    try:
        asyncio.run(main(settings))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")


if __name__ == "__main__":
    run()
