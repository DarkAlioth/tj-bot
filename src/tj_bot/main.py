import asyncio
import datetime
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from tj_bot.commands import set_bot_commands
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
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers import routers_list
from tj_bot.handlers.widgets_search import register_enrichment_push
from tj_bot.middlewares.access import AccessMiddleware
from tj_bot.middlewares.config import ConfigMiddleware
from tj_bot.middlewares.database import DatabaseMiddleware
from tj_bot.middlewares.throttling import ThrottlingMiddleware
from tj_bot.services import broadcaster
from tj_bot.services.enrich.service import build_enricher
from tj_bot.services.enrichment import NullEnricher
from tj_bot.services.favorites_watcher import favorites_watch_loop
from tj_bot.services.formatting import padded
from tj_bot.services.jackett import JackettClient
from tj_bot.services.monitor import MonitorState, monitor_loop
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError
from tj_bot.widgets.dispatch import (
    WidgetQueryMiddleware,
    widget_allowed_updates,
    widget_observer,
)

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
    access_middleware = AccessMiddleware()
    # config (admin ids) -> throttling (spam never opens a session) -> database
    # (session + repo) -> access (track user, drop blocked); on all observers
    # (inline queries pass throttling untouched — they only read the cache;
    # widget queries too — Telegram caps them at 5 per 5 s per user)
    for observer in (
        dp.message,
        dp.callback_query,
        dp.inline_query,
        widget_observer(dp),
    ):
        observer.outer_middleware(config_middleware)
        observer.outer_middleware(throttling_middleware)
        observer.outer_middleware(database_middleware)
        observer.outer_middleware(access_middleware)


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
    enricher = build_enricher(settings, session_pool)
    async with session_pool() as session:
        config.dynamic_admins.update(await TorrentRepo(session).admin_user_ids())

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    monitor = MonitorState()
    dp["jackett"] = jackett
    dp["qbit"] = qbit
    dp["enricher"] = enricher or NullEnricher()
    register_enrichment_push(bot, session_pool, dp["enricher"])
    dp["started_at"] = datetime.datetime.now(datetime.UTC)
    dp["monitor"] = monitor
    # background tasks spawned by widget handlers open their own sessions
    dp["session_pool"] = session_pool
    dp.include_routers(*routers_list)
    dp.update.outer_middleware(WidgetQueryMiddleware(dp))
    register_global_middlewares(dp, config, session_pool)
    await set_bot_commands(bot, config)

    background_tasks = [
        asyncio.create_task(
            cleanup_loop(
                session_pool,
                cache_ttl=datetime.timedelta(days=settings.cache_ttl_days),
                history_ttl=datetime.timedelta(days=settings.history_ttl_days),
                interval_seconds=settings.cleanup_interval_seconds,
            )
        ),
        asyncio.create_task(heartbeat_loop()),
    ]
    if settings.favorites_check_interval_seconds > 0:
        background_tasks.append(
            asyncio.create_task(
                favorites_watch_loop(
                    bot,
                    jackett,
                    session_pool,
                    config,
                    settings.favorites_check_interval_seconds,
                )
            )
        )
    if settings.alert_check_interval_seconds > 0:
        background_tasks.append(
            asyncio.create_task(
                monitor_loop(
                    bot,
                    jackett,
                    qbit,
                    config,
                    settings.alert_check_interval_seconds,
                    settings.alert_recheck_interval_seconds,
                    state=monitor,
                )
            )
        )
    if enricher is not None:
        await enricher.start()
    try:
        await broadcaster.broadcast(bot, config.admin_ids, padded("Бот был запущен"))
        logger.info("Starting polling")
        await dp.start_polling(bot, allowed_updates=widget_allowed_updates(dp))
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await jackett.close()
        if qbit is not None:
            await qbit.close()
        if enricher is not None:
            await enricher.close()
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
