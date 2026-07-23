from typing import cast
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

from tj_bot.commands import ADMIN_ONLY_COMMANDS, USER_COMMANDS, set_bot_commands
from tj_bot.config import AppConfig


def make_config(admin_ids: list[int]) -> AppConfig:
    config = MagicMock(spec=AppConfig)
    config.admin_ids = admin_ids
    return cast(AppConfig, config)


async def test_publishes_default_and_admin_scopes() -> None:
    bot = AsyncMock(spec=Bot)

    await set_bot_commands(bot, make_config([5, 6]))

    calls = bot.set_my_commands.await_args_list
    # 1 default scope + 2 admin scopes
    assert len(calls) == 3
    default_call = calls[0]
    assert isinstance(default_call.kwargs["scope"], BotCommandScopeDefault)
    assert default_call.args[0] == USER_COMMANDS

    admin_scopes = [c.kwargs["scope"] for c in calls[1:]]
    assert all(isinstance(s, BotCommandScopeChat) for s in admin_scopes)
    assert {s.chat_id for s in admin_scopes} == {5, 6}
    assert calls[1].args[0] == USER_COMMANDS + ADMIN_ONLY_COMMANDS


async def test_command_publish_failure_is_swallowed() -> None:
    bot = AsyncMock(spec=Bot)
    bot.set_my_commands.side_effect = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="fail"
    )

    # must not raise
    await set_bot_commands(bot, make_config([5]))
