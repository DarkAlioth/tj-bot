from unittest.mock import AsyncMock

from tj_bot.db.repo import TorrentRepo
from tj_bot.services.jackett import JackettClient
from tj_bot.services.subscriptions import check_subscription


def make_item_hashes(jackett: AsyncMock, hashes: list[str]) -> None:
    items = []
    for h in hashes:
        item = AsyncMock()
        item.hash = h
        item.title = f"Title {h}"
        items.append(item)
    jackett.search.return_value = items


async def test_only_unseen_results_trigger_notice() -> None:
    bot = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.seen_hashes.return_value = {"old"}
    jackett = AsyncMock(spec=JackettClient)
    make_item_hashes(jackett, ["old", "new1", "new2"])

    fresh = await check_subscription(bot, repo, jackett, 1, 777, "ubuntu")

    assert fresh == 2
    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.args[1]
    assert "Новинки" in text
    assert "Title new1" in text
    repo.upsert_torrents.assert_awaited_once()
    repo.add_seen_hashes.assert_awaited_once_with(1, ["old", "new1", "new2"])


async def test_no_news_no_notice() -> None:
    bot = AsyncMock()
    repo = AsyncMock(spec=TorrentRepo)
    repo.seen_hashes.return_value = {"a", "b"}
    jackett = AsyncMock(spec=JackettClient)
    make_item_hashes(jackett, ["a", "b"])

    fresh = await check_subscription(bot, repo, jackett, 1, 777, "ubuntu")

    assert fresh == 0
    bot.send_message.assert_not_awaited()
    repo.touch_subscription.assert_awaited_once_with(1)
