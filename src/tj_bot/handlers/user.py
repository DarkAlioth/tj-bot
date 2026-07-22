import datetime
import hashlib
import html
import io
import re

import aiohttp
import psycopg2
from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dateutil import parser

from tj_bot.config import AppConfig, load_settings

user_router = Router()

# Transitional: module-level sync connection is replaced by the async
# database layer in the follow-up PR.
_settings = load_settings()

try:
    conn = psycopg2.connect(
        database=_settings.postgres_db,
        user=_settings.postgres_user,
        password=_settings.postgres_password,
        host=_settings.db_host,
        port=_settings.db_port,
    )
except psycopg2.OperationalError:
    print("I am unable to connect to the database")
    exit()

cur = conn.cursor()

try:
    cur.execute(
        "CREATE TABLE IF NOT EXISTS torrents ("
        "id serial PRIMARY KEY, "
        "hash TEXT, "
        "title TEXT, "
        "uploader TEXT, "
        "description TEXT, "
        "category TEXT, "
        "link TEXT, "
        "dl_link TEXT, "
        "seeders INT, "
        "peers INT, "
        "date DATE, "
        "size NUMERIC "
        ");"
    )

    cur.execute(
        "CREATE TABLE IF NOT EXISTS query ("
        "id serial PRIMARY KEY, "
        "hash TEXT, "
        "query TEXT, "
        "cnt INT "
        ");"
    )
except:
    print()
    exit()


class Dlt(CallbackData, prefix="dlt"):
    type: str
    hash: str


class Pgn(CallbackData, prefix="pgn"):
    type: str
    qh: str
    page: int
    srch: str


def text_srch_msg(
    current_page,
    all_pages,
    url,
    title,
    uploader,
    description,
    seeders,
    peers,
    size,
    category,
    date,
):
    text = [
        f"⠀\n⠀<b>{current_page}</b> из <b>{all_pages}</b>⠀⠀",
        f'\n<b>Название</b>: <a href="{url}">{title}</a>\n',
    ]

    if description and description.strip():
        text.append(f"<b>Описание</b>: <code>{description}</code>\n")

    if uploader:
        text.append(f"<b>Загрузил</b>: <code>{uploader}</code>")

    text.extend(
        [
            f"<b>Сиды</b> / <b>Пиры</b>: <code>{seeders}</code> / <code>{peers}</code>",
            f"<b>Размер</b>: <code>{round((size / 1024 / 1024 / 1024), 2)} GB</code>",
            f"<b>Категория</b>: <code>{category}</code>",
            f"<b>Дата публикации</b>: <code>{datetime.datetime.strptime(str(date), '%Y-%m-%d').strftime('%d.%m.%Y')}</code>\n⠀",
        ]
    )
    return text


@user_router.message(CommandStart())
async def admin_start(message: Message):
    text = ["⠀\n⠀Приветсвую!\n", "Для поиска введите:", "<code>/s Название</code>\n⠀"]
    await message.answer("\n".join(text), parse_mode=ParseMode.HTML)


@user_router.message(F.text, Command("s"))
async def srch_torrent(
    message: Message, config: AppConfig, command, pages=0, sql_hashs=""
):
    if command.args is None:
        text = ["⠀\nДля поиска используйте:", "<code>/s Название</code>\n⠀"]
        await message.answer("\n".join(text), parse_mode=ParseMode.HTML)
    else:
        srch_message = await message.answer("Поиск выполняется, ожидайте...")
        url = (
            config.jackett.url
            + "/api/v2.0/indexers/all/results?apikey="
            + config.jackett.api_key
            + '&Query="'
            + command.args
            + '"'
        )
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        results = (await resp.json())["Results"]
                        for torrent in results:
                            title = torrent["Title"] or "None"
                            title = html.escape(title)
                            description = torrent["Description"]
                            if not description:
                                description = None
                            uploader = None
                            if description:
                                match = re.search(r"Uploader:\s*(\S+)", description)
                                if match:
                                    uploader = match.group(1)
                                    description = re.sub(
                                        r"Uploader:\s*[\s\S]*?<br\s*/?>|[\s\S]*?<br\s*/?>",
                                        "",
                                        description,
                                    )
                                if not uploader:
                                    match = re.search(
                                        r"([^\s<]+)(?=\s*<br>)", description
                                    )
                                    if match:
                                        uploader = match.group(1)
                            if description:
                                description = html.escape(description)
                            else:
                                description = None
                            if uploader:
                                uploader = html.escape(uploader)
                            else:
                                uploader = None
                            category = torrent["CategoryDesc"] or "None"
                            date = torrent["PublishDate"] or "None"
                            trackerid = torrent["TrackerId"] or "None"
                            date = parser.isoparse(date)
                            details = torrent["Details"] or "None"
                            link = torrent["Link"] or "None"
                            seeders = torrent["Seeders"] or 0
                            peers = torrent["Peers"] or 0
                            size = torrent["Size"] or 0
                            salt_true_hash = title + trackerid + str(date)
                            true_hash = hashlib.md5(
                                salt_true_hash.encode("utf-8"), usedforsecurity=False
                            ).hexdigest()
                            if torrent["Link"] is not None:
                                if sql_hashs == "":
                                    sql_hashs = true_hash
                                else:
                                    sql_hashs = sql_hashs + ", " + true_hash
                                cur.execute(
                                    "SELECT hash FROM torrents WHERE hash = %s",
                                    (true_hash,),
                                )
                                rows = cur.fetchall()
                                if not rows:
                                    cur.execute(
                                        "INSERT INTO torrents ("
                                        "hash, "
                                        "title, "
                                        "uploader, "
                                        "description, "
                                        "category, "
                                        "link, "
                                        "dl_link, "
                                        "seeders, "
                                        "peers, "
                                        "date, "
                                        "size"
                                        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                        (
                                            true_hash,
                                            title,
                                            uploader,
                                            description,
                                            category,
                                            details,
                                            link,
                                            seeders,
                                            peers,
                                            date.strftime("%Y-%m-%d"),
                                            size,
                                        ),
                                    )
                                    conn.commit()
                        cur.execute(
                            "SELECT count(id) FROM (SELECT id FROM torrents WHERE hash IN %(sql_hashs)s) AS subquery;",
                            {"sql_hashs": tuple(sql_hashs.split(", "))},
                        )
                        rows = cur.fetchall()
                        if rows:
                            for row in rows:
                                counter = row[0]
                        qh = hashlib.md5(
                            sql_hashs.encode("utf-8"), usedforsecurity=False
                        ).hexdigest()
                        cur.execute("SELECT hash FROM query WHERE hash = %s", (qh,))
                        rows = cur.fetchall()
                        if not rows:
                            cur.execute(
                                "INSERT INTO query ("
                                "hash, "
                                "query,"
                                "cnt"
                                ") VALUES (%s, %s, %s)",
                                (qh, sql_hashs, counter),
                            )
                            conn.commit()
                        cur.execute(
                            "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                            {
                                "sql_hashs": tuple(sql_hashs.split(", ")),
                                "limit": 1,
                                "offset": pages,
                            },
                        )
                        rows = cur.fetchall()
                        if rows:
                            for row in rows:
                                text = text_srch_msg(
                                    pages + 1,
                                    counter,
                                    row[6],
                                    row[2],
                                    row[3],
                                    row[4],
                                    row[8],
                                    row[9],
                                    row[11],
                                    row[5],
                                    row[10],
                                )
                            if counter > 1:
                                kb = [
                                    [
                                        types.InlineKeyboardButton(
                                            text="💾 Скачать",
                                            callback_data=Dlt(
                                                type="download", hash=row[1]
                                            ).pack(),
                                        ),
                                        types.InlineKeyboardButton(
                                            text="🗂 Категории",
                                            callback_data=Pgn(
                                                type="go_search",
                                                qh=qh,
                                                page=0,
                                                srch="ALL",
                                            ).pack(),
                                        ),
                                    ],
                                    [
                                        types.InlineKeyboardButton(
                                            text="➡",
                                            callback_data=Pgn(
                                                type="go_next",
                                                qh=qh,
                                                page=0,
                                                srch="ALL",
                                            ).pack(),
                                        )
                                    ],
                                ]
                            else:
                                kb = [
                                    [
                                        types.InlineKeyboardButton(
                                            text="💾 Скачать",
                                            callback_data=Dlt(
                                                type="download", hash=row[1]
                                            ).pack(),
                                        )
                                    ]
                                ]
                            keyboard = types.InlineKeyboardMarkup(inline_keyboard=kb)
                            await srch_message.edit_text(
                                "\n".join(text),
                                parse_mode=ParseMode.HTML,
                                reply_markup=keyboard,
                                disable_web_page_preview=True,
                            )
                        else:
                            text = [
                                "К сожалению ничего не найдено 😔",
                            ]
                            await srch_message.edit_text(
                                "\n".join(text), parse_mode=ParseMode.HTML
                            )
            except aiohttp.ClientResponseError as e:
                text = ["<b>Ошибка!</b>", f"{e.status}", f"{e.message}"]
                await message.answer("\n".join(text))


@user_router.callback_query(Dlt.filter(F.type == "download"))
async def hash_callback(query: CallbackQuery, callback_data: Dlt):
    cur.execute(
        "SELECT dl_link, title FROM torrents WHERE hash = %s", (callback_data.hash,)
    )
    rows = cur.fetchall()
    for row in rows:
        title = row[1].replace("<", "").replace(">", "").replace("/", "|") + ".torrent"
        dl_link = row[0]
        async with aiohttp.ClientSession() as session:
            async with session.get(dl_link) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    file_buffer = io.BytesIO(content)
                    file_buffer.name = title
                    await query.message.answer_document(
                        BufferedInputFile(file=file_buffer.getvalue(), filename=title)
                    )
                else:
                    await query.message.answer("Не удалось скачать файл с трекера.")


@user_router.callback_query(Pgn.filter(F.type == "go_priv"))
async def go_priv(query: CallbackQuery, callback_data: Pgn):
    pages = callback_data.page - 1
    cur.execute("SELECT query, cnt FROM query WHERE hash = %s", (callback_data.qh,))
    rows = cur.fetchall()
    for row in rows:
        sql_hashs = row[0]
        counter = row[1]
        if callback_data.srch == "ALL":
            cur.execute(
                "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "limit": 1,
                    "offset": pages,
                },
            )
        else:
            cur.execute(
                "SELECT count(id) FROM (SELECT id FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s) AS subquery;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "category": callback_data.srch,
                },
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    counter = row[0]
            cur.execute(
                "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "category": callback_data.srch,
                    "limit": 1,
                    "offset": pages,
                },
            )
        rows = cur.fetchall()
        for row in rows:
            text = text_srch_msg(
                pages + 1,
                counter,
                row[6],
                row[2],
                row[3],
                row[4],
                row[8],
                row[9],
                row[11],
                row[5],
                row[10],
            )
            if pages - 1 < 0:
                kb = [
                    [
                        types.InlineKeyboardButton(
                            text="💾 Скачать",
                            callback_data=Dlt(type="download", hash=row[1]).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="🗂 Категории",
                            callback_data=Pgn(
                                type="go_search",
                                qh=callback_data.qh,
                                page=0,
                                srch="ALL",
                            ).pack(),
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="➡",
                            callback_data=Pgn(
                                type="go_next",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        )
                    ],
                ]
            else:
                kb = [
                    [
                        types.InlineKeyboardButton(
                            text="💾 Скачать",
                            callback_data=Dlt(type="download", hash=row[1]).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="🗂 Категории",
                            callback_data=Pgn(
                                type="go_search",
                                qh=callback_data.qh,
                                page=0,
                                srch="ALL",
                            ).pack(),
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="⬅",
                            callback_data=Pgn(
                                type="go_priv",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="➡",
                            callback_data=Pgn(
                                type="go_next",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        ),
                    ],
                ]
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=kb)
            await query.answer()
            await query.message.edit_text(
                "\n".join(text),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )


@user_router.callback_query(Pgn.filter(F.type == "go_next"))
async def go_next(query: CallbackQuery, callback_data: Pgn):
    pages = callback_data.page + 1
    cur.execute("SELECT query, cnt FROM query WHERE hash = %s", (callback_data.qh,))
    rows = cur.fetchall()
    for row in rows:
        sql_hashs = row[0]
        counter = row[1]
        if callback_data.srch == "ALL":
            cur.execute(
                "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "limit": 1,
                    "offset": pages,
                },
            )
        else:
            cur.execute(
                "SELECT count(id) FROM (SELECT id FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s) AS subquery;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "category": callback_data.srch,
                },
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    counter = row[0]
            cur.execute(
                "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                {
                    "sql_hashs": tuple(sql_hashs.split(", ")),
                    "category": callback_data.srch,
                    "limit": 1,
                    "offset": pages,
                },
            )
        rows = cur.fetchall()
        for row in rows:
            text = text_srch_msg(
                pages + 1,
                counter,
                row[6],
                row[2],
                row[3],
                row[4],
                row[8],
                row[9],
                row[11],
                row[5],
                row[10],
            )
            if pages + 2 > counter:
                kb = [
                    [
                        types.InlineKeyboardButton(
                            text="💾 Скачать",
                            callback_data=Dlt(type="download", hash=row[1]).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="🗂 Категории",
                            callback_data=Pgn(
                                type="go_search",
                                qh=callback_data.qh,
                                page=0,
                                srch="ALL",
                            ).pack(),
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="⬅",
                            callback_data=Pgn(
                                type="go_priv",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        )
                    ],
                ]
            else:
                kb = [
                    [
                        types.InlineKeyboardButton(
                            text="💾 Скачать",
                            callback_data=Dlt(type="download", hash=row[1]).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="🗂 Категории",
                            callback_data=Pgn(
                                type="go_search",
                                qh=callback_data.qh,
                                page=0,
                                srch="ALL",
                            ).pack(),
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="⬅",
                            callback_data=Pgn(
                                type="go_priv",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        ),
                        types.InlineKeyboardButton(
                            text="➡",
                            callback_data=Pgn(
                                type="go_next",
                                qh=callback_data.qh,
                                page=pages,
                                srch=callback_data.srch,
                            ).pack(),
                        ),
                    ],
                ]
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=kb)
            await query.answer()
            await query.message.edit_text(
                "\n".join(text),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )


@user_router.callback_query(Pgn.filter(F.type == "go_search"))
async def go_search(query: CallbackQuery, callback_data: Pgn):
    cur.execute("SELECT query, cnt FROM query WHERE hash = %s", (callback_data.qh,))
    rows = cur.fetchall()
    for row in rows:
        sql_hashs = row[0]
        counter = row[1]
        cur.execute(
            "SELECT DISTINCT ON (1) category FROM torrents WHERE hash IN %(sql_hashs)s;",
            {"sql_hashs": tuple(sql_hashs.split(", "))},
        )
        rows = cur.fetchall()
        if rows:
            builder = InlineKeyboardBuilder()
            builder.button(
                text=f"Все - {counter}",
                callback_data=Pgn(
                    type="go_priv", qh=callback_data.qh, page=1, srch="ALL"
                ).pack(),
            )
            for row in rows:
                category = row[0]
                cur.execute(
                    "SELECT count(id) FROM (SELECT id FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s) AS subquery;",
                    {"sql_hashs": tuple(sql_hashs.split(", ")), "category": category},
                )
                rows = cur.fetchall()
                if rows:
                    for row in rows:
                        srch_cnt = row[0]
                        builder.button(
                            text=f"{category} - {srch_cnt}",
                            callback_data=Pgn(
                                type="fs", qh=callback_data.qh, page=0, srch=category
                            ).pack(),
                        )
            builder.adjust(1, 2)
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=builder.export())
            text = ["⠀\n⠀<b>Выберите категорию</b>:\n⠀"]
            await query.answer()
            await query.message.edit_text(
                "\n".join(text),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )


@user_router.callback_query(Pgn.filter(F.type == "fs"))
async def go_searched(query: CallbackQuery, callback_data: Pgn):
    pages = callback_data.page
    sort = callback_data.srch
    cur.execute("SELECT query FROM query WHERE hash = %s", (callback_data.qh,))
    rows = cur.fetchall()
    for row in rows:
        sql_hashs = row[0]
        cur.execute(
            "SELECT count(id) FROM (SELECT id FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s) AS subquery;",
            {"sql_hashs": tuple(sql_hashs.split(", ")), "category": sort},
        )
        rows = cur.fetchall()
        if rows:
            for row in rows:
                counter = row[0]
                cur.execute(
                    "SELECT * FROM torrents WHERE hash IN %(sql_hashs)s AND category = %(category)s ORDER BY seeders DESC, peers DESC, id DESC LIMIT %(limit)s offset %(offset)s;",
                    {
                        "sql_hashs": tuple(sql_hashs.split(", ")),
                        "category": sort,
                        "limit": 1,
                        "offset": pages,
                    },
                )
                rows = cur.fetchall()
                for row in rows:
                    text = text_srch_msg(
                        pages + 1,
                        counter,
                        row[6],
                        row[2],
                        row[3],
                        row[4],
                        row[8],
                        row[9],
                        row[11],
                        row[5],
                        row[10],
                    )
                    if counter == 1:
                        kb = [
                            [
                                types.InlineKeyboardButton(
                                    text="💾 Скачать",
                                    callback_data=Dlt(
                                        type="download", hash=row[1]
                                    ).pack(),
                                ),
                                types.InlineKeyboardButton(
                                    text="🗂 Категории",
                                    callback_data=Pgn(
                                        type="go_search",
                                        qh=callback_data.qh,
                                        page=0,
                                        srch="ALL",
                                    ).pack(),
                                ),
                            ]
                        ]
                    else:
                        kb = [
                            [
                                types.InlineKeyboardButton(
                                    text="💾 Скачать",
                                    callback_data=Dlt(
                                        type="download", hash=row[1]
                                    ).pack(),
                                ),
                                types.InlineKeyboardButton(
                                    text="🗂 Категории",
                                    callback_data=Pgn(
                                        type="go_search",
                                        qh=callback_data.qh,
                                        page=0,
                                        srch="ALL",
                                    ).pack(),
                                ),
                            ],
                            [
                                types.InlineKeyboardButton(
                                    text="➡",
                                    callback_data=Pgn(
                                        type="go_next",
                                        qh=callback_data.qh,
                                        page=pages,
                                        srch=callback_data.srch,
                                    ).pack(),
                                )
                            ],
                        ]
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=kb)
                    await query.answer()
                    await query.message.edit_text(
                        "\n".join(text),
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
