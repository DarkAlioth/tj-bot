import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import BotUser
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.users import (
    Us2,
    Usr,
    UsrL,
    build_user_list,
    legacy_user_action,
    user_action,
    users_page,
)


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
    config.super_admins = sorted(super_admins)
    config.dynamic_admins = set()
    return cast(AppConfig, config)


def make_query() -> AsyncMock:
    query = AsyncMock(spec=CallbackQuery)
    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    return query


def make_bot() -> AsyncMock:
    return AsyncMock()


def card(uid: int, action: str = "card") -> Us2:
    return Us2(a=action, uid=uid, g="a", p=0)


async def test_show_user_card_has_actions() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user()

    await user_action(query, card(100), repo, make_config(set()), make_bot())

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert "🚫 Заблокировать" in labels
    assert "⭐ В админы" in labels


async def test_card_keeps_list_position_in_callbacks() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user()

    await user_action(
        query, Us2(a="card", uid=100, g="b", p=2), repo, make_config(set()), make_bot()
    )

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [b.callback_data for r in kb.inline_keyboard for b in r]
    # actions carry the origin group/page; the back button returns to it
    assert Us2(a="block", uid=100, g="b", p=2).pack() in callbacks
    assert UsrL(g="b", p=2).pack() in callbacks
    assert all(len(data.encode()) <= 64 for data in callbacks)


async def test_super_admin_has_no_management_buttons() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(uid=5)

    await user_action(query, card(5), repo, make_config({5}), make_bot())

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for r in kb.inline_keyboard for b in r]
    assert labels == ["📋 История", "◀️ К списку"]


async def test_block_toggles_and_refreshes() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(blocked=True)

    await user_action(query, card(100, "block"), repo, make_config(set()), make_bot())

    repo.set_blocked.assert_awaited_once_with(100, True)


async def test_cannot_block_super_admin() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)

    await user_action(query, card(5, "block"), repo, make_config({5}), make_bot())

    repo.set_blocked.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


async def test_promote_updates_db_and_live_set() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user(admin=True)
    config = make_config(set())

    bot = make_bot()
    await user_action(query, card(100, "promote"), repo, config, bot)

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

    bot = make_bot()
    await user_action(query, card(100, "demote"), repo, config, bot)

    repo.set_admin.assert_awaited_once_with(100, False)
    assert 100 not in config.dynamic_admins
    from aiogram.types import BotCommandScopeChat

    from tj_bot.commands import USER_COMMANDS

    bot.set_my_commands.assert_awaited_once_with(
        USER_COMMANDS, scope=BotCommandScopeChat(chat_id=100)
    )


async def test_activity_view_lists_searches_and_downloads() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.user_activity_counts.return_value = (3, 2)
    when = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=datetime.UTC)
    repo.get_user_searches.return_value = [("ubuntu", when)]
    repo.get_user_downloads.return_value = [("Movie", "server", when)]

    await user_action(
        query, card(100, "activity"), repo, make_config(set()), make_bot()
    )

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


async def test_user_list_shows_tabs_and_user_buttons() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 2
    repo.list_users_page.return_value = [make_user(100), make_user(101)]

    text, kb = await build_user_list(repo, make_config(set()), "a", 0)

    assert "все: 2" in text
    tab_row = [b.text for b in kb.inline_keyboard[0]]
    assert tab_row == ["• 👥 Все", "👤 Обычные", "⭐ Админы", "🚫 Блок"]
    repo.list_users_page.assert_awaited_once_with(10, 0, "all", [])
    user_callbacks = [b.callback_data for b in (r[0] for r in kb.inline_keyboard[1:])]
    assert Us2(a="card", uid=100, g="a", p=0).pack() in user_callbacks
    # a two-user list fits one page: no nav row
    assert len(kb.inline_keyboard) == 3


async def test_user_list_pagination_nav() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 25
    repo.list_users_page.return_value = [make_user(100)]

    _, kb = await build_user_list(repo, make_config(set()), "a", 1)

    nav = [b.text for b in kb.inline_keyboard[-1]]
    assert nav == ["⬅", "2/3", "➡"]
    repo.list_users_page.assert_awaited_once_with(10, 10, "all", [])


async def test_user_list_clamps_page_overflow() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 25
    repo.list_users_page.return_value = [make_user(100)]

    await build_user_list(repo, make_config(set()), "a", 99)

    # 25 users -> 3 pages -> the last page offset is 20
    repo.list_users_page.assert_awaited_once_with(10, 20, "all", [])


async def test_user_list_blocked_group_queries_blocked() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 0
    repo.list_users_page.return_value = []

    text, kb = await build_user_list(repo, make_config({5}), "b", 0)

    assert "никого нет" in text
    repo.count_users.assert_awaited_once_with("blocked", [5])
    tab_row = [b.text for b in kb.inline_keyboard[0]]
    assert "• 🚫 Блок" in tab_row


async def test_users_page_renders_in_place() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 1
    repo.list_users_page.return_value = [make_user(100)]

    await users_page(query, UsrL(g="m", p=0), repo, make_config(set()))

    query.message.edit_text.assert_awaited_once()
    assert "админы: 1" in query.message.edit_text.await_args.args[0]


async def test_legacy_usr_buttons_still_work() -> None:
    query = make_query()
    repo = AsyncMock(spec=TorrentRepo)
    repo.get_user.return_value = make_user()
    repo.count_users.return_value = 1
    repo.list_users_page.return_value = [make_user(100)]
    config = make_config(set())
    bot = make_bot()

    await legacy_user_action(query, Usr(a="card", uid=100), repo, config, bot)
    assert query.message.edit_text.await_count == 1

    await legacy_user_action(query, Usr(a="list", uid=0), repo, config, bot)
    assert query.message.edit_text.await_count == 2
    assert "все: 1" in query.message.edit_text.await_args.args[0]


async def test_user_list_regular_group_maps_to_repo_filter() -> None:
    repo = AsyncMock(spec=TorrentRepo)
    repo.count_users.return_value = 1
    repo.list_users_page.return_value = [make_user(100)]

    text, kb = await build_user_list(repo, make_config({5}), "r", 0)

    assert "обычные: 1" in text
    repo.count_users.assert_awaited_once_with("regular", [5])
    tab_row = [b.text for b in kb.inline_keyboard[0]]
    assert "• 👤 Обычные" in tab_row
