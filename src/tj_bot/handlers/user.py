import hashlib
import html
import io
import re

import aiohttp
from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dateutil import parser as date_parser

from tj_bot.config import AppConfig
from tj_bot.db.models import Torrent
from tj_bot.db.repo import TorrentData, TorrentRepo

user_router = Router()

ALL_CATEGORIES = "ALL"


class Dlt(CallbackData, prefix="dlt"):
    type: str
    hash: str


class Pgn(CallbackData, prefix="pgn"):
    type: str
    qh: str
    page: int
    srch: str


def parse_jackett_result(torrent: dict) -> TorrentData | None:
    """Convert one Jackett result to TorrentData; None when not downloadable."""
    if torrent["Link"] is None:
        return None
    title = html.escape(torrent["Title"] or "None")
    description = torrent["Description"] or None
    uploader = None
    if description:
        match = re.search(r"Uploader:\s*(\S+)", description)
        if match:
            uploader = match.group(1)
            description = re.sub(
                r"Uploader:\s*[\s\S]*?<br\s*/?>|[\s\S]*?<br\s*/?>", "", description
            )
        if not uploader:
            match = re.search(r"([^\s<]+)(?=\s*<br>)", description)
            if match:
                uploader = match.group(1)
    description = html.escape(description) if description else None
    uploader = html.escape(uploader) if uploader else None
    category = torrent["CategoryDesc"] or "None"
    published_at = date_parser.isoparse(torrent["PublishDate"]).date()
    tracker_id = torrent["TrackerId"] or "None"
    raw_hash = f"{title}{tracker_id}{published_at}"
    torrent_hash = hashlib.md5(
        raw_hash.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    return TorrentData(
        hash=torrent_hash,
        title=title,
        uploader=uploader,
        description=description,
        category=category,
        details_url=torrent["Details"] or "None",
        download_url=torrent["Link"],
        seeders=torrent["Seeders"] or 0,
        peers=torrent["Peers"] or 0,
        published_at=published_at,
        size=int(torrent["Size"] or 0),
    )


def text_srch_msg(current_page: int, all_pages: int, torrent: Torrent) -> list[str]:
    text = [
        f"⠀\n⠀<b>{current_page}</b> из <b>{all_pages}</b>⠀⠀",
        f'\n<b>Название</b>: <a href="{torrent.details_url}">{torrent.title}</a>\n',
    ]
    if torrent.description and torrent.description.strip():
        text.append(f"<b>Описание</b>: <code>{torrent.description}</code>\n")
    if torrent.uploader:
        text.append(f"<b>Загрузил</b>: <code>{torrent.uploader}</code>")
    text.extend(
        [
            f"<b>Сиды</b> / <b>Пиры</b>: <code>{torrent.seeders}</code> / <code>{torrent.peers}</code>",
            f"<b>Размер</b>: <code>{round(torrent.size / 1024 / 1024 / 1024, 2)} GB</code>",
            f"<b>Категория</b>: <code>{torrent.category}</code>",
            f"<b>Дата публикации</b>: <code>{torrent.published_at.strftime('%d.%m.%Y')}</code>\n⠀",
        ]
    )
    return text


def result_keyboard(
    torrent_hash: str, qh: str, page: int, srch: str, has_prev: bool, has_next: bool
) -> types.InlineKeyboardMarkup:
    top = [
        types.InlineKeyboardButton(
            text="💾 Скачать",
            callback_data=Dlt(type="download", hash=torrent_hash).pack(),
        ),
        types.InlineKeyboardButton(
            text="🗂 Категории",
            callback_data=Pgn(
                type="go_search", qh=qh, page=0, srch=ALL_CATEGORIES
            ).pack(),
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
async def user_start(message: Message):
    text = ["⠀\n⠀Приветсвую!\n", "Для поиска введите:", "<code>/s Название</code>\n⠀"]
    await message.answer("\n".join(text), parse_mode=ParseMode.HTML)


@user_router.message(F.text, Command("s"))
async def srch_torrent(message: Message, config: AppConfig, repo: TorrentRepo, command):
    if command.args is None:
        text = ["⠀\nДля поиска используйте:", "<code>/s Название</code>\n⠀"]
        await message.answer("\n".join(text), parse_mode=ParseMode.HTML)
        return

    srch_message = await message.answer("Поиск выполняется, ожидайте...")
    url = (
        config.jackett.url
        + "/api/v2.0/indexers/all/results?apikey="
        + config.jackett.api_key
        + '&Query="'
        + command.args
        + '"'
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return
                results = (await resp.json())["Results"]
    except aiohttp.ClientResponseError as e:
        await message.answer(
            "\n".join(["<b>Ошибка!</b>", f"{e.status}", f"{e.message}"])
        )
        return

    items = [item for item in map(parse_jackett_result, results) if item]
    if not items:
        await srch_message.edit_text("К сожалению ничего не найдено 😔")
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
        await srch_message.edit_text("К сожалению ничего не найдено 😔")
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
async def hash_callback(query: CallbackQuery, callback_data: Dlt, repo: TorrentRepo):
    torrent = await repo.get_torrent_by_hash(callback_data.hash)
    if torrent is None:
        await query.answer()
        return
    filename = (
        torrent.title.replace("<", "").replace(">", "").replace("/", "|") + ".torrent"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(torrent.download_url) as resp:
            if resp.status == 200:
                content = await resp.read()
                file_buffer = io.BytesIO(content)
                file_buffer.name = filename
                await query.message.answer_document(
                    BufferedInputFile(file=file_buffer.getvalue(), filename=filename)
                )
            else:
                await query.message.answer("Не удалось скачать файл с трекера.")
    await query.answer()


async def render_page(
    query: CallbackQuery, repo: TorrentRepo, qh: str, page: int, srch: str
) -> None:
    search = await repo.get_search(qh)
    if search is None:
        await query.answer()
        return
    category = None if srch == ALL_CATEGORIES else srch
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
    await query.message.edit_text(
        "\n".join(text_srch_msg(page + 1, counter, torrent)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@user_router.callback_query(Pgn.filter(F.type == "go_priv"))
async def go_priv(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo):
    await render_page(
        query, repo, callback_data.qh, callback_data.page - 1, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "go_next"))
async def go_next(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo):
    await render_page(
        query, repo, callback_data.qh, callback_data.page + 1, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "fs"))
async def go_searched(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo):
    await render_page(
        query, repo, callback_data.qh, callback_data.page, callback_data.srch
    )


@user_router.callback_query(Pgn.filter(F.type == "go_search"))
async def go_search(query: CallbackQuery, callback_data: Pgn, repo: TorrentRepo):
    search = await repo.get_search(callback_data.qh)
    if search is None:
        await query.answer()
        return
    categories = await repo.get_categories(callback_data.qh)
    if not categories:
        await query.answer()
        return
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"Все - {search.result_count}",
        callback_data=Pgn(
            type="go_priv", qh=callback_data.qh, page=1, srch=ALL_CATEGORIES
        ).pack(),
    )
    for category, count in categories:
        builder.button(
            text=f"{category} - {count}",
            callback_data=Pgn(
                type="fs", qh=callback_data.qh, page=0, srch=category
            ).pack(),
        )
    builder.adjust(1, 2)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=builder.export())
    await query.answer()
    await query.message.edit_text(
        "⠀\n⠀<b>Выберите категорию</b>:\n⠀",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
