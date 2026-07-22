import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from tj_bot.config import (
    AppConfig,
    JackettRuntime,
    load_settings,
    wait_for_jackett_api_key,
)
from tj_bot.handlers import routers_list
from tj_bot.middlewares.config import ConfigMiddleware
from tj_bot.services import broadcaster

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s:%(lineno)d - %(message)s",
    )


def register_global_middlewares(dp: Dispatcher, config: AppConfig) -> None:
    middleware = ConfigMiddleware(config)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)


async def main() -> None:
    setup_logging()
    settings = load_settings()
    jackett_api_key = await wait_for_jackett_api_key(settings)
    config = AppConfig(
        settings=settings,
        jackett=JackettRuntime(url=settings.jackett_url, api_key=jackett_api_key),
    )

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(*routers_list)
    register_global_middlewares(dp, config)

    await broadcaster.broadcast(bot, config.admin_ids, "Бот был запущен")
    logger.info("Starting polling")
    await dp.start_polling(bot)


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")


if __name__ == "__main__":
    run()
