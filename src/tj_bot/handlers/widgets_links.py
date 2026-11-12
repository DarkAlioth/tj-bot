"""Cross-widget hooks: the hub's admin shortcuts open the admin widgets."""

import datetime

from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.qbit_console import NOT_CONFIGURED_TEXT
from tj_bot.handlers.widgets_admin import FIRST_REV, build_dashboard, build_users
from tj_bot.handlers.widgets_hub import SendAdminWidget
from tj_bot.handlers.widgets_server import send_console_widget
from tj_bot.services.formatting import padded
from tj_bot.services.monitor import MonitorState
from tj_bot.services.qbittorrent import QbittorrentClient
from tj_bot.widgets.send import send_widget


def make_send_admin_widget(
    config: AppConfig,
    qbit: QbittorrentClient | None,
    monitor: MonitorState,
    started_at: datetime.datetime,
) -> SendAdminWidget:
    """``dp["send_admin_widget"]``: sends the console, dashboard or users widget."""

    async def send(bot: Bot, chat_id: int, name: str, repo: TorrentRepo) -> None:
        if name == "console":
            if qbit is None:
                await bot.send_message(chat_id, padded(NOT_CONFIGURED_TEXT))
                return
            await send_console_widget(bot, chat_id, repo, qbit)
            return
        if name == "dashboard":
            state, caption, context = await build_dashboard(
                repo, qbit, monitor, started_at, tab="h", rev=FIRST_REV
            )
        else:
            state, caption, context = await build_users(repo, config, "a", 0, FIRST_REV)
        await send_widget(
            bot, chat_id, name, state, caption, repo=repo, kind=name, context=context
        )

    return send
