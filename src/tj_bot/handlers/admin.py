import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.handlers.server_download import begin_magnet_send, begin_torrent_send
from tj_bot.handlers.user import Dlt
from tj_bot.services.formatting import padded
from tj_bot.services.jackett import JackettClient
from tj_bot.services.qbittorrent import QbittorrentClient

logger = logging.getLogger(__name__)

admin_router = Router()


@admin_router.callback_query(Dlt.filter(F.type == "server"))
async def send_to_server(
    query: CallbackQuery,
    callback_data: Dlt,
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    message = query.message
    if not config.is_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    if qbit is None or not isinstance(message, Message):
        await query.answer()
        return
    torrent = await repo.get_torrent_by_hash(callback_data.hash)
    if torrent is None:
        await query.answer()
        return
    await begin_torrent_send(
        query,
        message,
        repo,
        jackett,
        qbit,
        config,
        torrent.title,
        torrent.download_url,
    )


@admin_router.message(AdminOnly(), F.text.startswith("magnet:"))
async def add_magnet(
    message: Message,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    config: AppConfig,
) -> None:
    if qbit is None:
        await message.answer(padded("qBittorrent не настроен."))
        return
    await begin_magnet_send(message, repo, qbit, config, (message.text or "").strip())
