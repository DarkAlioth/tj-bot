import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from tj_bot.config import AppConfig

logger = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand(command="s", description="🔎 Поиск торрентов"),
    BotCommand(command="last", description="🔁 Повторить последний поиск"),
    BotCommand(command="history", description="🕘 История поиска"),
    BotCommand(command="favorites", description="⭐ Избранное"),
]
ADMIN_ONLY_COMMANDS = [
    BotCommand(command="dl", description="🖥 Консоль qBittorrent"),
    BotCommand(command="health", description="🩺 Состояние сервиса"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="indexers", description="🧲 Индексеры Jackett"),
    BotCommand(command="users", description="👥 Пользователи"),
    BotCommand(command="broadcast", description="📢 Рассылка"),
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


async def set_user_commands(bot: Bot, user_id: int, admin: bool) -> None:
    """Refresh one user's command menu after a role change."""
    commands = USER_COMMANDS + ADMIN_ONLY_COMMANDS if admin else USER_COMMANDS
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
    except TelegramAPIError:
        # the user may have never opened the bot chat — menu updates on /start
        logger.warning("Failed to update the command menu for %s", user_id)
