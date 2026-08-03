import dataclasses
import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentData, TorrentRepo
from tj_bot.services.favorites_watcher import (
    check_favorites_once,
    search_query_for,
)
from tj_bot.services.jackett import JackettClient, JackettError

GB = 1024**3


def make_favorite(
    fav_id: int = 7,
    user_id: int = 111,
    size: int = GB,
    title: str = "Series S01 [01/10]",
) -> MagicMock:
    favorite = MagicMock()
    favorite.id = fav_id
    favorite.user_id = user_id
    favorite.title = title
    favorite.tracker = "rutracker"
    favorite.details_url = "https://tracker.example/topic/1"
    favorite.size = size
    favorite.published_at = datetime.date(2026, 7, 1)
    return favorite


def make_fresh(size: int = 2 * GB, title: str = "Series S01 [02/10]") -> TorrentData:
    return TorrentData(
        hash="newhash",
        title=title,
        uploader=None,
        description=None,
        category="TV",
        tracker="rutracker",
        details_url="https://tracker.example/topic/1",
        download_url="http://jackett:9117/dl/2",
        seeders=10,
        peers=2,
        published_at=datetime.date(2026, 8, 1),
        size=size,
    )


def make_config(admin_ids: set[int]) -> AppConfig:
    config = MagicMock()
    config.is_admin = lambda uid: uid in admin_ids
    return cast(AppConfig, config)


def make_env(
    monkeypatch: pytest.MonkeyPatch, favorites: list[MagicMock]
) -> tuple[AsyncMock, MagicMock]:
    """A fake TorrentRepo class + session pool for the watcher module."""
    repo = AsyncMock(spec=TorrentRepo)
    repo.list_all_favorites.return_value = favorites
    monkeypatch.setattr(
        "tj_bot.services.favorites_watcher.TorrentRepo", MagicMock(return_value=repo)
    )
    monkeypatch.setattr("tj_bot.services.favorites_watcher.REQUEST_GAP_SECONDS", 0)
    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock(return_value=context)
    return repo, pool


def test_search_query_strips_bracketed_counters() -> None:
    assert (
        search_query_for("Кухня [08/16] [2016, WEB-DL] &amp; bonus") == "Кухня & bonus"
    )


async def test_changed_topic_updates_snapshot_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    favorite = make_favorite()
    repo, pool = make_env(monkeypatch, [favorite])
    bot = AsyncMock(spec=Bot)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search_indexer.return_value = [make_fresh()]

    updated = await check_favorites_once(
        cast(Bot, bot), jackett, pool, make_config(set())
    )

    assert updated == 1
    repo.update_favorite_snapshot.assert_awaited_once()
    jackett.search_indexer.assert_awaited_once_with("rutracker", "Series S01")
    text = bot.send_message.await_args.args[1]
    assert "Обновление в избранном" in text
    assert "1.0 GB" in text and "2.0 GB" in text
    keyboard = bot.send_message.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["💾 Скачать"]  # regular user: no server button


async def test_admin_notification_has_server_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    favorite = make_favorite(user_id=999)
    _, pool = make_env(monkeypatch, [favorite])
    bot = AsyncMock(spec=Bot)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search_indexer.return_value = [make_fresh()]

    await check_favorites_once(cast(Bot, bot), jackett, pool, make_config({999}))

    keyboard = bot.send_message.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["💾 Скачать", "⬇️ На сервер"]


async def test_unchanged_topic_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    favorite = make_favorite(size=GB, title="Same")
    repo, pool = make_env(monkeypatch, [favorite])
    bot = AsyncMock(spec=Bot)
    jackett = AsyncMock(spec=JackettClient)
    fresh = dataclasses.replace(
        make_fresh(size=GB, title="Same"), published_at=favorite.published_at
    )
    jackett.search_indexer.return_value = [fresh]

    updated = await check_favorites_once(
        cast(Bot, bot), jackett, pool, make_config(set())
    )

    assert updated == 0
    repo.update_favorite_snapshot.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_missing_topic_and_search_errors_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gone = make_favorite(fav_id=1)
    broken = make_favorite(fav_id=2)
    broken.details_url = "https://tracker.example/topic/2"
    repo, pool = make_env(monkeypatch, [gone, broken])
    bot = AsyncMock(spec=Bot)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search_indexer.side_effect = [[], JackettError("down")]

    updated = await check_favorites_once(
        cast(Bot, bot), jackett, pool, make_config(set())
    )

    assert updated == 0
    bot.send_message.assert_not_awaited()


async def test_shared_topic_notifies_every_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_favorite(fav_id=1, user_id=111)
    second = make_favorite(fav_id=2, user_id=222)
    repo, pool = make_env(monkeypatch, [first, second])
    bot = AsyncMock(spec=Bot)
    jackett = AsyncMock(spec=JackettClient)
    jackett.search_indexer.return_value = [make_fresh()]

    updated = await check_favorites_once(
        cast(Bot, bot), jackett, pool, make_config(set())
    )

    assert updated == 1
    jackett.search_indexer.assert_awaited_once()  # one search per topic
    assert repo.update_favorite_snapshot.await_count == 2
    notified = {call.args[0] for call in bot.send_message.await_args_list}
    assert notified == {111, 222}
