import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.exc import SQLAlchemyError

from tj_bot.db.engine import SessionPool
from tj_bot.db.repo import TorrentRepo
from tj_bot.services.jackett import JackettClient, JackettError

logger = logging.getLogger(__name__)

PER_SUBSCRIPTION_PAUSE_SECONDS = 5
MAX_TITLES_IN_NOTICE = 5


async def check_subscription(
    bot: Bot,
    repo: TorrentRepo,
    jackett: JackettClient,
    subscription_id: int,
    chat_id: int,
    query_text: str,
) -> int:
    """Check one subscription; notify about unseen results. Returns new count."""
    items = await jackett.search(query_text)
    await repo.touch_subscription(subscription_id)
    if not items:
        return 0
    seen = await repo.seen_hashes(subscription_id)
    fresh = [item for item in items if item.hash not in seen]
    await repo.add_seen_hashes(subscription_id, [item.hash for item in items])
    if not fresh:
        return 0
    await repo.upsert_torrents(fresh)
    titles = [f"• {item.title}" for item in fresh[:MAX_TITLES_IN_NOTICE]]
    more = len(fresh) - len(titles)
    if more > 0:
        titles.append(f"… и ещё {more}")
    text = "\n".join(
        [
            f"🔔 Новинки по запросу <b>{html.escape(query_text)}</b>:",
            *titles,
            "",
            f"Смотреть: <code>/s {html.escape(query_text)}</code>",
        ]
    )
    await bot.send_message(chat_id, text)
    return len(fresh)


async def subscriptions_loop(
    bot: Bot,
    session_pool: SessionPool,
    jackett: JackettClient,
    interval_seconds: int,
) -> None:
    """Periodically re-run saved searches and notify about new results."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_pool() as session:
                repo = TorrentRepo(session)
                subscriptions = await repo.all_subscriptions()
                for subscription in subscriptions:
                    try:
                        fresh = await check_subscription(
                            bot,
                            repo,
                            jackett,
                            subscription.id,
                            subscription.chat_id,
                            subscription.query_text,
                        )
                        await session.commit()
                        if fresh:
                            logger.info(
                                "Subscription %s: %s new results",
                                subscription.id,
                                fresh,
                            )
                    except JackettError:
                        logger.warning(
                            "Subscription %s check failed (jackett)",
                            subscription.id,
                        )
                    except TelegramAPIError:
                        logger.warning(
                            "Subscription %s notice undeliverable",
                            subscription.id,
                        )
                    await asyncio.sleep(PER_SUBSCRIPTION_PAUSE_SECONDS)
        except SQLAlchemyError:
            logger.exception("Subscriptions sweep failed")
