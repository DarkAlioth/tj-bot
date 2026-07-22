import asyncio
import datetime
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from tj_bot.config import (
    AppConfig,
    JackettRuntime,
    Settings,
    load_settings,
    wait_for_jackett_api_key,
)
from tj_bot.db.engine import (
    SessionPool,
    build_db_url,
    create_engine,
    create_session_pool,
)
from tj_bot.db.maintenance import cleanup_loop
from tj_bot.db.migrate import run_migrations
from tj_bot.handlers import routers_list
from tj_bot.middlewares.config import ConfigMiddleware
from tj_bot.middlewares.database import DatabaseMiddleware
from tj_bot.middlewares.throttling import ThrottlingMiddleware
from tj_bot.services import broadcaster
from tj_bot.services.jackett import JackettClient
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError
from tj_bot.services.subscriptions import subscriptions_loop

logger = logging.getLogger(__name__)

# Ephemeral liveness marker inside the container, inspected by HEALTHCHECK.
HEARTBEAT_PATH = Path(  # noqa: S108  # nosec B108
    os.environ.get("HEARTBEAT_PATH", "/tmp/tj-bot-heartbeat")  # noqa: S108  # nosec B108
)
HEARTBEAT_INTERVAL_SECONDS = 30


async def heartbeat_loop() -> None:
    """Touch the liveness file the container HEALTHCHECK inspects."""
    while True:
        try:
            await asyncio.to_thread(
                HEARTBEAT_PATH.write_text, str(time.time()), encoding="utf-8"
            )
        except OSError:
            logger.exception("Failed to write heartbeat")
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d - %(message)s",
    )


def register_global_middlewares(
    dp: Dispatcher, config: AppConfig, session_pool: SessionPool
) -> None:
    config_middleware = ConfigMiddleware(config)
    throttling_middleware = ThrottlingMiddleware()
    database_middleware = DatabaseMiddleware(session_pool)
    # throttling sits after config (needs admin ids) and before the database so
    # rate-limited spam never opens a session; applied to messages and callbacks
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(config_middleware)
        observer.outer_middleware(throttling_middleware)
        observer.outer_middleware(database_middleware)


USER_COMMANDS = [
    BotCommand(command="s", description="🔎 Поиск торрентов"),
    BotCommand(command="history", description="🕘 История поиска"),
    BotCommand(command="subs", description="🔔 Мои подписки"),
]
ADMIN_ONLY_COMMANDS = [
    BotCommand(command="dl", description="🖥 Консоль qBittorrent"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="indexers", description="🧲 Индексеры Jackett"),
]


async def set_bot_commands(bot: Bot, config: AppConfig) -> None:
    """Publish the command menu: shared commands for all, extras for admins."""
    try:
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
        for admin_id in config.admin_ids:
            await bot.set_my_commands(
                USER_COMMANDS + ADMIN_ONLY_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
    except TelegramAPIError:
        logger.exception("Failed to publish bot commands")


async def setup_qbittorrent(settings: Settings) -> QbittorrentClient | None:
    """Build and log in the qBittorrent client, or None if unconfigured."""
    if not (
        settings.qbit_enabled
        and settings.qbit_url
        and settings.qbit_username
        and settings.qbit_password
    ):
        logger.info("qBittorrent integration disabled (not configured)")
        return None
    client = QbittorrentClient(
        base_url=settings.qbit_url,
        username=settings.qbit_username,
        password=settings.qbit_password,
    )
    try:
        await client.login()
    except QbittorrentError:
        logger.exception("qBittorrent login failed; integration disabled")
        await client.close()
        return None
    logger.info("qBittorrent integration enabled")
    return client


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
    qbit = await setup_qbittorrent(settings)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp["jackett"] = jackett
    dp["qbit"] = qbit
    dp["started_at"] = datetime.datetime.now(datetime.UTC)
    dp.include_routers(*routers_list)
    register_global_middlewares(dp, config, session_pool)
    await set_bot_commands(bot, config)

    cleanup_task = asyncio.create_task(
        cleanup_loop(
            session_pool,
            ttl=datetime.timedelta(days=settings.cache_ttl_days),
            interval_seconds=settings.cleanup_interval_seconds,
        )
    )
    subscriptions_task = asyncio.create_task(
        subscriptions_loop(
            bot, session_pool, jackett, settings.subscriptions_check_seconds
        )
    )
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        await broadcaster.broadcast(bot, config.admin_ids, "Бот был запущен")
        logger.info("Starting polling")
        await dp.start_polling(bot)
    finally:
        for task in (cleanup_task, subscriptions_task, heartbeat_task):
            task.cancel()
        await asyncio.gather(
            cleanup_task, subscriptions_task, heartbeat_task, return_exceptions=True
        )
        await jackett.close()
        if qbit is not None:
            await qbit.close()
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
