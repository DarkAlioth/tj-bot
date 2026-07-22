import hashlib
import logging

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from tj_bot.db.models import Torrent
from tj_bot.db.repo import TorrentRepo
from tj_bot.services.jackett import DownloadTooLargeError, JackettClient, JackettError

logger = logging.getLogger(__name__)

user_router = Router()

ALL_CATEGORIES = "ALL"
NOT_FOUND_TEXT = "К сожалению ничего не найдено 😔"
SEARCH_UNAVAILABLE_TEXT = "Поиск временно недоступен, попробуйте позже 🛠"


class Dlt(CallbackData, prefix="dlt"):
    type: str
    hash: str


class Pgn(CallbackData, prefix="pgn"):
    type: str
    qh: str
    page: int
    srch: str


def category_token(category: str) -> str:
    """Short stable token that keeps callback_data under Telegram's 64 bytes."""
    return hashlib.blake2s(category.encode("utf-8"), digest_size=4).hexdigest()


async def resolve_category(repo: TorrentRepo, qh: str, token: str) -> str | None:
    """Map a callback token back to the category name; None means all."""
    if token == ALL_CATEGORIES:
        return None
    for name, _ in await repo.get_categories(qh):
        if category_token(name) == token:
            return name
    return None


def text_srch_msg(current_page: int, all_pages: int, torrent: Torrent) -> list[str]:
    text = [
        f"⠀\n⠀<b>{current_page}</b> из <b>{all_pages}</b>⠀⠀",
        f'\n<b>Название</b>: <a href="{torrent.details_url}">{torrent.title}</a>\n',
    ]
    if torrent.description and torrent.description.strip():
        text.append(f"<b>Описание</b>: <code>{torrent.description}</code>\n")
    if torrent.uploader:
        text.append(f"<b>Загрузил</b>: <code>{torrent.uploader}</code>")
    size_gb = round(torrent.size / 1024 / 1024 / 1024, 2)
    published = torrent.published_at.strftime("%d.%m.%Y")
    text.extend(
        [
            "<b>Сиды</b> / <b>Пиры</b>: "
            f"<code>{torrent.seeders}</code> / <code>{torrent.peers}</code>",
            f"<b>Размер</b>: <code>{size_gb} GB</code>",
            f"<b>Категория</b>: <code>{torrent.category}</code>",
            f"<b>Дата публикации</b>: <code>{published}</code>\n⠀",
        ]
    )
    return text


def result_keyboard(
    torrent_hash: str,
    qh: str,
    page: int,
    srch: str,
    has_prev: bool,
    has_next: bool,
) -> types.InlineKeyboardMarkup:
    top = [
        types.InlineKeyboardButton(
            text="💾 Скачать",
            callback_data=Dlt(type="download", hash=torrent_hash).pack(),
        ),
        types.InlineKeyboardButton(
            text="🗂 Категории",
            # carries the current position so the menu's back button can
            # return to exactly this card
            callback_data=Pgn(type="go_search", qh=qh, page=page, srch=srch).pack(),
        ),
    ]
    nav = []
    if has_prev:
        nav.append(
            types.InlineKeyboardButton(
                text="⬅",
                callback_data=Pgn(type="go_priv", qh=qh, page=page, srch=srch).pack(),
            )
        )
    if has_next:
        nav.append(
            types.InlineKeyboardButton(
                text="➡",
                callback_data=Pgn(type="go_next", qh=qh, page=page, srch=srch).pack(),
            )
        )
    kb = [top]
    if nav:
        kb.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=kb)


@user_router.message(CommandStart())
async def user_start(message: Message) -> None:
    text = ["⠀\n⠀Приветсвую!\n", "Для поиска введите:", "<code>/s Название</code>\n⠀"]
    await message.answer("\n".join(text), parse_mode=ParseMode.HTML)


@user_router.message(F.text, Command("s"))
async def srch_torrent(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    command: CommandObject,
) -> None:
    if command.args is None:
        text = ["⠀\nДля поиска используйте:", "<code>/s Название</code>\n⠀"]
        await message.answer("\n".join(text), parse_mode=ParseMode.HTML)
        return

    srch_message = await message.answer("Поиск выполняется, ожидайте...")
    try:
        items = await jackett.search(command.args)
    except JackettError:
        logger.exception("Search failed for query %r", command.args)
        await srch_message.edit_text(SEARCH_UNAVAILABLE_TEXT)
        return

    if not items:
        await srch_message.edit_text(NOT_FOUND_TEXT)
        return

    ids = await repo.upsert_torrents(items)
    unique_ids = list(dict.fromkeys(ids))
    counter = len(unique_ids)
    qh = hashlib.md5(
        ",".join(item.hash for item in items).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    await repo.create_search(qh, unique_ids)

    torrent = await repo.get_result_page(qh, None, 0)
    if torrent is None:
        await srch_message.edit_text(NOT_FOUND_TEXT)
        return
    keyboard = result_keyboard(
        torrent.hash, qh, 0, ALL_CATEGORIES, has_prev=False, has_next=counter > 1
    )
    await srch_message.edit_text(
        "\n".join(text_srch_msg(1, counter, torrent)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@user_router.callback_query(Dlt.filter(F.type == "download"))
async def hash_callback(
    query: CallbackQuery,
    callback_data: Dlt,
    repo: TorrentRepo,
    jackett: JackettClient,
) -> None:
    message = query.message
    torrent = await repo.get_torrent_by_hash(callback_data.hash)
    if torrent is None or not isinstance(message, Message):
        await query.answer()
        return
    filename = (
        torrent.title.replace("<", "").replace(">", "").replace("/", "|") + ".torrent"
    )
    try:
        content = await jackett.download(torrent.download_url)
    except DownloadTooLargeError:
        await message.answer("Файл с трекера слишком большой.")
    except JackettError:
        logger.exception("Download failed for %s", torrent.hash)
        await message.answer("Не удалось скачать файл с трекера.")
    else:
        await message.answer_document(
            BufferedInputFile(file=content, filename=filename)
        )
    await query.answer()


async def render_page(
    query: CallbackQuery, repo: TorrentRepo, qh: str, page: int, srch: str
) -> None:
    message = query.message
    search = await repo.get_search(qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    category = await resolve_category(repo, qh, srch)
    counter = (
        search.result_count
        if category is None
        else await repo.count_results(qh, category)
    )
    torrent = await repo.get_result_page(qh, category, page)
    if torrent is None:
        await query.answer()
        return
    keyboard = result_keyboard(
        torrent.hash,
        qh,
        page,
        srch,
        has_prev=page > 0,
        has_next=page + 1 < counter,
    )
    await query.answer()
    await message.edit_text(
        "\n".join(text_srch_msg(page + 1, counter, torrent)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@user_router.callback_query(Pgn.filter(F.type == "go_priv"))
async def go_priv(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo) -> None:
    await render_page(
        query, repo, callback_data.qh, callback_data.page - 1, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "go_next"))
async def go_next(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo) -> None:
    await render_page(
        query, repo, callback_data.qh, callback_data.page + 1, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "fs"))
async def go_searched(
    query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo
) -> None:
    await render_page(
        query, repo, callback_data.qh, callback_data.page, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "go_search"))
async def go_search(
    query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo
) -> None:
    message = query.message
    search = await repo.get_search(callback_data.qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    categories = await repo.get_categories(callback_data.qh)
    if not categories:
        await query.answer()
        return
    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Все - {search.result_count}",
                callback_data=Pgn(
                    type="go_priv", qh=callback_data.qh, page=1, srch=ALL_CATEGORIES
                ).pack(),
            )
        ]
    ]
    row: list[types.InlineKeyboardButton] = []
    for category, count in categories:
        row.append(
            types.InlineKeyboardButton(
                text=f"{category} - {count}",
                callback_data=Pgn(
                    type="fs",
                    qh=callback_data.qh,
                    page=0,
                    srch=category_token(category),
                ).pack(),
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            types.InlineKeyboardButton(
                text="◀️ Назад",
                # the menu callback kept the position of the card it was
                # opened from — return straight to it
                callback_data=Pgn(
                    type="fs",
                    qh=callback_data.qh,
                    page=callback_data.page,
                    srch=callback_data.srch,
                ).pack(),
            )
        ]
    )
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=rows)
    await query.answer()
    await message.edit_text(
        "⠀\n⠀<b>Выберите категорию</b>:\n⠀",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
