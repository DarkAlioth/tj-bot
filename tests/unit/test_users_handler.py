import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import BotUser
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.users import Usr, show_user, toggle_admin, toggle_block


def make_user(uid: int = 100, blocked: bool = False, admin: bool = False) -> BotUser:
    return BotUser(
        user_id=uid,
        username="alice",
        full_name="Alice",
        blocked=blocked,
        admin=admin,
        first_seen=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
        last_seen=datetime.datetime(2026, 7, 22, tzinfo=datetime.UTC),
    )


def make_config(super_admins: set[int]) -> AppConfig:
    config = MagicMock()
    config.is_super_admin = lambda uid: uid in super_admins
    config.dynamic_admins = set()
    return cast(AppConfig, config)


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    return query


async def test_show_user_card_has_actions() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user()

    await show_user(query, Usr(a="card", uid=100), repo, make_config(set()))

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert "🚫 Заблокировать" in labels
    assert "⭐ В админы" in labels


async def test_super_admin_has_no_management_buttons() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(uid=5)

    await show_user(query, Usr(a="card", uid=5), repo, make_config({5}))

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert labels == ["📋 История", "◀️ К списку"]


async def test_block_toggles_and_refreshes() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(blocked=True)

    await toggle_block(query, Usr(a="block", uid=100), repo, make_config(set()))

    repo.set_blocked.assert_awaited_once_with(100, True)


async def test_cannot_block_super_admin() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)

    await toggle_block(query, Usr(a="block", uid=5), repo, make_config({5}))

    repo.set_blocked.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_promote_updates_db_and_live_set() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(admin=True)
    config = make_config(set())

    bot = AsyncMock()
    await toggle_admin(query, Usr(a="promote", uid=100), repo, config, bot)

    repo.set_admin.assert_awaited_once_with(100, True)
    assert 100 in config.dynamic_admins
    # the promoted user's command menu is refreshed at once
    from aiogram.types import BotCommandScopeChat

    from tj_bot.commands import ADMIN_ONLY_COMMANDS, USER_COMMANDS

    bot.set_my_commands.assert_awaited_once_with(
        USER_COMMANDS + ADMIN_ONLY_COMMANDS, scope=BotCommandScopeChat(chat_id=100)
    )


async def test_demote_removes_from_live_set() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(admin=False)
    config = make_config(set())
    config.dynamic_admins.add(100)

    bot = AsyncMock()
    await toggle_admin(query, Usr(a="demote", uid=100), repo, config, bot)

    repo.set_admin.assert_awaited_once_with(100, False)
    assert 100 not in config.dynamic_admins
    from aiogram.types import BotCommandScopeChat

    from tj_bot.commands import USER_COMMANDS

    bot.set_my_commands.assert_awaited_once_with(
        USER_COMMANDS, scope=BotCommandScopeChat(chat_id=100)
    )


async def test_activity_view_lists_searches_and_downloads() -> None:
    import datetime

    from tj_bot.handlers.users import show_activity

    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.user_activity_counts.return_value = (3, 2)
    when = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=datetime.UTC)
    repo.get_user_searches.return_value = [("ubuntu", when)]
    repo.get_user_downloads.return_value = [("Movie", "server", when)]

    await show_activity(query, Usr(a="activity", uid=100), repo)

    text = query.message.edit_text.await_args.args[0]
    assert "поисков: 3" in text and "скачиваний: 2" in text
    assert "ubuntu" in text
    assert "Movie" in text and "на сервер" in text


def test_user_label_flags_super_admin_with_crown() -> None:
    from tj_bot.handlers.users import user_label

    config = make_config({100})
    assert user_label(make_user(uid=100, admin=True), config).startswith("👑")
    assert user_label(make_user(uid=101, admin=True), config).startswith("⭐")
    assert user_label(make_user(uid=102), config).startswith("👤")
    assert user_label(make_user(uid=103, blocked=True), config).startswith("🚫")
