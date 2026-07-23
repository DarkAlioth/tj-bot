import html
import logging

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import Favorite
from tj_bot.db.repo import TorrentRepo
from tj_bot.handlers.admin import KIND_FAVORITE, send_category_menu
from tj_bot.handlers.user import FAV_SAVE_LABEL, FAV_SAVED_LABEL, Dlt
from tj_bot.services.formatting import padded
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    safe_torrent_filename,
)
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)

favorites_router = Router()

FAV_PAGE_SIZE = 10
EMPTY_TEXT = padded("⭐ Избранное пусто — нажмите «☆ Сохранить» на карточке поиска.")
GONE_TEXT = "Записи больше нет в избранном"


class Fav(CallbackData, prefix="fav"):
    a: str
    id: int  # favorite id; for a == "ls" the list page number


def swap_fav_button(
    markup: types.InlineKeyboardMarkup, pressed: str, is_fav: bool
) -> types.InlineKeyboardMarkup:
    """The same keyboard with the pressed favorite button relabeled."""
    label = FAV_SAVED_LABEL if is_fav else FAV_SAVE_LABEL
    rows = [
        [
            button.model_copy(update={"text": label})
            if button.callback_data == pressed
            else button
            for button in row
        ]
        for row in markup.inline_keyboard
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@favorites_router.callback_query(Dlt.filter(F.type == "fav"))
async def toggle_favorite(
    query: CallbackQuery, callback_data: Dlt, repo: TorrentRepo
) -> None:
    torrent = await repo.get_torrent_by_hash(callback_data.hash)
    if torrent is None:
        await query.answer("Карточка устарела, повторите поиск", show_alert=True)
        return
    if await repo.add_favorite(query.from_user.id, torrent):
        is_fav = True
        await query.answer("⭐ Сохранено в избранное — /favorites")
    else:
        await repo.remove_favorite_by_hash(query.from_user.id, torrent.hash)
        is_fav = False
        await query.answer("Убрано из избранного")
    message = query.message
    if (
        isinstance(message, Message)
        and message.reply_markup is not None
        and query.data is not None
    ):
        try:
            await message.edit_reply_markup(
                reply_markup=swap_fav_button(message.reply_markup, query.data, is_fav)
            )
        except TelegramBadRequest:
            # double-tap: the keyboard already shows the right label
            logger.debug("Favorite button relabel skipped (message not modified)")


def favorite_line(index: int, favorite: Favorite) -> str:
    # unescape before truncating so a sliced entity cannot leak out
    title = html.unescape(favorite.title)
    if len(title) > 48:
        title = title[:47] + "…"
    size_gb = round(favorite.size / 1024 / 1024 / 1024, 1)
    return (
        f'{index}. <a href="{favorite.details_url}">{html.escape(title)}</a>'
        f" — {size_gb} GB"
    )


async def render_favorites(
    target: Message, repo: TorrentRepo, user_id: int, page: int, edit: bool
) -> None:
    total = await repo.count_favorites(user_id)
    offset = page * FAV_PAGE_SIZE
    favorites = await repo.list_favorites(user_id, FAV_PAGE_SIZE, offset)
    if not favorites:
        if edit:
            await target.edit_text(EMPTY_TEXT)
        else:
            await target.answer(EMPTY_TEXT)
        return
    lines = [f"⭐ <b>Избранное</b> — {total}\n"]
    lines.extend(
        favorite_line(offset + i + 1, favorite) for i, favorite in enumerate(favorites)
    )
    number_rows = [
        [
            types.InlineKeyboardButton(
                text=str(offset + start + i + 1),
                callback_data=Fav(a="op", id=favorite.id).pack(),
            )
            for i, favorite in enumerate(favorites[start : start + 5])
        ]
        for start in range(0, len(favorites), 5)
    ]
    nav = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                text="⬅", callback_data=Fav(a="ls", id=page - 1).pack()
            )
        )
    if offset + len(favorites) < total:
        nav.append(
            types.InlineKeyboardButton(
                text="➡", callback_data=Fav(a="ls", id=page + 1).pack()
            )
        )
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[*number_rows, nav] if nav else number_rows
    )
    text = padded("\n".join(lines))
    if edit:
        await target.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    else:
        await target.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


@favorites_router.message(Command("favorites"))
async def show_favorites(message: Message, repo: TorrentRepo) -> None:
    if message.from_user is None:
        return
    await render_favorites(message, repo, message.from_user.id, 0, edit=False)


@favorites_router.callback_query(Fav.filter(F.a == "ls"))
async def favorites_page(
    query: CallbackQuery, callback_data: Fav, repo: TorrentRepo
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    await query.answer()
    await render_favorites(
        message, repo, query.from_user.id, callback_data.id, edit=True
    )


@favorites_router.callback_query(Fav.filter(F.a == "op"))
async def open_favorite(
    query: CallbackQuery, callback_data: Fav, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    favorite = await repo.get_favorite(query.from_user.id, callback_data.id)
    if favorite is None or not isinstance(message, Message):
        await query.answer(GONE_TEXT, show_alert=True)
        return
    size_gb = round(favorite.size / 1024 / 1024 / 1024, 2)
    lines = [
        "⭐ <b>Избранное</b>\n",
        f'<b>Название</b>: <a href="{favorite.details_url}">{favorite.title}</a>\n',
        f"<b>Размер</b>: <code>{size_gb} GB</code>",
        f"<b>Категория</b>: <code>{favorite.category}</code>",
    ]
    if favorite.tracker:
        lines.append(f"<b>Трекер</b>: <code>{favorite.tracker}</code>")
    lines.append(
        f"<b>Дата публикации</b>: "
        f"<code>{favorite.published_at.strftime('%d.%m.%Y')}</code>"
    )
    lines.append(
        f"<b>Сохранено</b>: <code>{favorite.created_at.strftime('%d.%m.%Y')}</code>"
    )
    action_row = [
        types.InlineKeyboardButton(
            text="💾 Скачать", callback_data=Fav(a="dl", id=favorite.id).pack()
        )
    ]
    if config.qbit_enabled and config.is_admin(query.from_user.id):
        action_row.append(
            types.InlineKeyboardButton(
                text="⬇️ На сервер", callback_data=Fav(a="sv", id=favorite.id).pack()
            )
        )
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            action_row,
            [
                types.InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=Fav(a="rm", id=favorite.id).pack()
                ),
                types.InlineKeyboardButton(
                    text="↩️ Список", callback_data=Fav(a="ls", id=0).pack()
                ),
            ],
        ]
    )
    await query.answer()
    await message.edit_text(
        padded("\n".join(lines)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@favorites_router.callback_query(Fav.filter(F.a == "dl"))
async def download_favorite(
    query: CallbackQuery, callback_data: Fav, repo: TorrentRepo, jackett: JackettClient
) -> None:
    message = query.message
    favorite = await repo.get_favorite(query.from_user.id, callback_data.id)
    if favorite is None or not isinstance(message, Message):
        await query.answer(GONE_TEXT, show_alert=True)
        return
    try:
        content = await jackett.download(favorite.download_url)
    except DownloadTooLargeError:
        await message.answer(padded("Файл с трекера слишком большой."))
    except JackettError:
        logger.exception("Favorite download failed for %s", favorite.id)
        await message.answer(
            padded("Не удалось скачать — ссылка могла устареть, найдите заново.")
        )
    else:
        await message.answer_document(
            BufferedInputFile(
                file=content, filename=safe_torrent_filename(favorite.title)
            )
        )
        await repo.record_download(query.from_user.id, favorite.title, "chat")
    await query.answer()


@favorites_router.callback_query(Fav.filter(F.a == "sv"))
async def favorite_to_server(
    query: CallbackQuery,
    callback_data: Fav,
    repo: TorrentRepo,
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
    favorite = await repo.get_favorite(query.from_user.id, callback_data.id)
    if favorite is None:
        await query.answer(GONE_TEXT, show_alert=True)
        return
    try:
        await send_category_menu(
            message,
            qbit,
            config,
            kind=KIND_FAVORITE,
            item_hash=str(favorite.id),
            name=favorite.title,
            size=favorite.size,
        )
    except QbittorrentError:
        logger.exception("Failed to load qBittorrent categories")
        await query.answer("qBittorrent недоступен", show_alert=True)
        return
    await query.answer()


@favorites_router.callback_query(Fav.filter(F.a == "rm"))
async def remove_favorite(
    query: CallbackQuery, callback_data: Fav, repo: TorrentRepo
) -> None:
    message = query.message
    await repo.remove_favorite(query.from_user.id, callback_data.id)
    await query.answer("Удалено из избранного")
    if isinstance(message, Message):
        await render_favorites(message, repo, query.from_user.id, 0, edit=True)
