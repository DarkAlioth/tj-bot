import datetime
import hashlib
import html
import logging

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.filters import (
    DATE_LABELS,
    SEEDERS_LABELS,
    SIZE_LABELS,
    ResultFilters,
)
from tj_bot.db.models import Torrent
from tj_bot.db.repo import TorrentRepo
from tj_bot.services.formatting import padded
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    safe_torrent_filename,
)

# Dlt with type="server" and category_token are shared with the admin router;
# the favorite labels are swapped in place by the favorites router
__all__ = [
    "FAV_SAVED_LABEL",
    "FAV_SAVE_LABEL",
    "Dlt",
    "Pg2",
    "category_token",
    "result_keyboard",
    "user_router",
]

logger = logging.getLogger(__name__)

user_router = Router()

ALL_CATEGORIES = "ALL"
DEFAULT_SORT = "se"
NOT_FOUND_TEXT = padded("К сожалению ничего не найдено 😔")
SEARCH_UNAVAILABLE_TEXT = padded("Поиск временно недоступен, попробуйте позже 🛠")
SEARCHING_TEXT = padded("Поиск выполняется, ожидайте...")
STALE_QUERY_TEXT = "Запрос устарел, выполните новый поиск: /s"  # alert popup

DEFAULT_FILTER = "000"
LIST_PAGE_SIZE = 10
SORT_LABELS = {
    "se": "Сиды ↓",
    "sz": "Размер ↓",
    "za": "Размер ↑",
    "dt": "Дата ↓",
    "da": "Дата ↑",
}
SORT_NEXT = {"se": "sz", "sz": "za", "za": "dt", "dt": "da", "da": "se"}

# Favorite toggle labels; favorites.py swaps them in place on toggle.
FAV_SAVE_LABEL = "☆ Сохранить"
FAV_SAVED_LABEL = "⭐ Сохранено"

# Flt.o — which view the filter menu returns to on apply/reset
ORIGIN_CARD = "c"
ORIGIN_LIST = "l"


class Dlt(CallbackData, prefix="dlt"):
    type: str
    hash: str


class Pg2(CallbackData, prefix="pg2"):
    t: str
    qh: str
    p: int
    c: str
    s: str
    fl: str


class Flt(CallbackData, prefix="flt"):
    a: str
    qh: str
    c: str
    s: str
    fl: str
    o: str


class Upd(CallbackData, prefix="upd"):
    qh: str


class Hst(CallbackData, prefix="hst"):
    qid: int


def normalize_query(raw: str) -> str:
    return " ".join(raw.split()).lower()[:200]


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


def text_srch_msg(
    current_page: int, all_pages: int, torrent: Torrent, from_cache: bool = False
) -> list[str]:
    header = f"⠀\n⠀<b>{current_page}</b> из <b>{all_pages}</b>⠀⠀"
    if from_cache:
        header += "  ⚡"
    text = [
        header,
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
        ]
    )
    if torrent.tracker:
        text.append(f"<b>Трекер</b>: <code>{torrent.tracker}</code>")
    text.append(f"<b>Дата публикации</b>: <code>{published}</code>\n⠀")
    return text


def filter_button(
    qh: str, cat: str, sort: str, flt: str, origin: str
) -> types.InlineKeyboardButton:
    label = "🔎 Фильтр" + (
        " ✅" if flt != DEFAULT_FILTER or cat != ALL_CATEGORIES else ""
    )
    return types.InlineKeyboardButton(
        text=label,
        callback_data=Flt(a="open", qh=qh, c=cat, s=sort, fl=flt, o=origin).pack(),
    )


def sort_button(pg2_pack: str, sort: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=SORT_LABELS.get(sort, SORT_LABELS[DEFAULT_SORT]), callback_data=pg2_pack
    )


def result_keyboard(
    torrent_hash: str,
    qh: str,
    page: int,
    cat: str,
    sort: str,
    has_prev: bool,
    has_next: bool,
    show_server: bool = False,
    flt: str = DEFAULT_FILTER,
    is_fav: bool = False,
) -> types.InlineKeyboardMarkup:
    def pg2(t: str, p: int = page, c: str = cat, s: str = sort) -> str:
        return Pg2(t=t, qh=qh, p=p, c=c, s=s, fl=flt).pack()

    # row 1: list mode (opens the list page holding this card), sort, filters
    kb = [
        [
            types.InlineKeyboardButton(
                text="📋 Список", callback_data=pg2("ls", p=page // LIST_PAGE_SIZE)
            ),
            sort_button(pg2("fs", p=0, s=SORT_NEXT.get(sort, DEFAULT_SORT)), sort),
            filter_button(qh, cat, sort, flt, ORIGIN_CARD),
        ]
    ]
    # row 2: download, favorite toggle (+ send-to-server for admins)
    download_row = [
        types.InlineKeyboardButton(
            text="💾 Скачать",
            callback_data=Dlt(type="download", hash=torrent_hash).pack(),
        ),
        types.InlineKeyboardButton(
            text=FAV_SAVED_LABEL if is_fav else FAV_SAVE_LABEL,
            callback_data=Dlt(type="fav", hash=torrent_hash).pack(),
        ),
    ]
    if show_server:
        download_row.append(
            types.InlineKeyboardButton(
                text="⬇️ На сервер",
                callback_data=Dlt(type="server", hash=torrent_hash).pack(),
            )
        )
    kb.append(download_row)
    # row 3: navigation
    nav = []
    if has_prev:
        nav.append(types.InlineKeyboardButton(text="⬅", callback_data=pg2("pv")))
    nav.append(types.InlineKeyboardButton(text="🔄", callback_data=Upd(qh=qh).pack()))
    if has_next:
        nav.append(types.InlineKeyboardButton(text="➡", callback_data=pg2("nx")))
    kb.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=kb)


@user_router.message(CommandStart())
async def user_start(message: Message) -> None:
    text = [
        "Приветсвую!\n",
        "Для поиска введите:",
        "<code>/s Название</code>\n",
        "История ваших запросов: /history",
    ]
    await message.answer(padded("\n".join(text)), parse_mode=ParseMode.HTML)


async def render_result_card(
    target: Message,
    repo: TorrentRepo,
    config: AppConfig,
    qh: str,
    counter: int,
    user_id: int | None,
    from_cache: bool,
) -> None:
    torrent = await repo.get_result_page(qh, None, 0, DEFAULT_SORT)
    if torrent is None:
        await target.edit_text(NOT_FOUND_TEXT)
        return
    show_server = config.qbit_enabled and config.is_admin(user_id)
    is_fav = user_id is not None and await repo.is_favorite(user_id, torrent.hash)
    keyboard = result_keyboard(
        torrent.hash,
        qh,
        0,
        ALL_CATEGORIES,
        DEFAULT_SORT,
        has_prev=False,
        has_next=counter > 1,
        show_server=show_server,
        is_fav=is_fav,
    )
    await target.edit_text(
        "\n".join(text_srch_msg(1, counter, torrent, from_cache)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def run_search(
    target: Message,
    user_id: int | None,
    raw_query: str,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    force_refresh: bool = False,
) -> None:
    """Shared search flow for /s, the refresh button and history replays."""
    query_text = normalize_query(raw_query)
    if not force_refresh:
        cache_window = datetime.timedelta(seconds=config.settings.search_cache_seconds)
        recent = await repo.find_recent_search(query_text, cache_window)
        if recent is not None:
            if user_id is not None:
                await repo.record_search_event(user_id, recent.id)
            await render_result_card(
                target, repo, config, recent.hash, recent.result_count, user_id, True
            )
            return

    try:
        items = await jackett.search(raw_query)
    except JackettError:
        logger.exception("Search failed for query %r", raw_query)
        await target.edit_text(SEARCH_UNAVAILABLE_TEXT)
        return
    if not items:
        await target.edit_text(NOT_FOUND_TEXT)
        return

    ids = await repo.upsert_torrents(items)
    unique_ids = list(dict.fromkeys(ids))
    # include the query text so two different queries returning an identical
    # result set do not collide onto one cached search record
    fingerprint = query_text + "\x00" + ",".join(item.hash for item in items)
    qh = hashlib.md5(fingerprint.encode("utf-8"), usedforsecurity=False).hexdigest()
    query_id = await repo.create_search(qh, unique_ids, query_text)
    if user_id is not None:
        await repo.record_search_event(user_id, query_id)
    await render_result_card(target, repo, config, qh, len(unique_ids), user_id, False)


@user_router.message(F.text, Command("s"))
async def srch_torrent(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    command: CommandObject,
) -> None:
    if command.args is None:
        text = ["Для поиска используйте:", "<code>/s Название</code>"]
        await message.answer(padded("\n".join(text)), parse_mode=ParseMode.HTML)
        return
    srch_message = await message.answer(SEARCHING_TEXT)
    user_id = message.from_user.id if message.from_user else None
    await run_search(srch_message, user_id, command.args, repo, jackett, config)


@user_router.callback_query(Upd.filter())
async def refresh_search(
    query: CallbackQuery,
    callback_data: Upd,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    search = await repo.get_search(callback_data.qh)
    if search is None or not search.query_text:
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    await query.answer("Обновляю…")
    await message.edit_text(SEARCHING_TEXT)
    await run_search(
        message,
        query.from_user.id if query.from_user else None,
        search.query_text,
        repo,
        jackett,
        config,
        force_refresh=True,
    )


@user_router.message(Command("last"))
async def repeat_last_search(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    history = await repo.get_user_history(user_id, limit=1)
    if not history:
        await message.answer(padded("История поиска пуста. Начните с /s"))
        return
    srch_message = await message.answer(SEARCHING_TEXT)
    await run_search(srch_message, user_id, history[0][1], repo, jackett, config)


@user_router.message(Command("history"))
async def show_history(message: Message, repo: TorrentRepo) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    history = await repo.get_user_history(user_id)
    if not history:
        await message.answer(padded("История поиска пуста. Начните с /s"))
        return
    rows = [
        [
            types.InlineKeyboardButton(
                text=text if len(text) <= 40 else text[:39] + "…",
                callback_data=Hst(qid=query_id).pack(),
            )
        ]
        for query_id, text in history
    ]
    await message.answer(
        padded("🕘 <b>Недавние запросы</b> — нажмите, чтобы повторить:"),
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


@user_router.callback_query(Hst.filter())
async def replay_history(
    query: CallbackQuery,
    callback_data: Hst,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
) -> None:
    message = query.message
    query_text = await repo.get_query_text(callback_data.qid)
    if query_text is None or not isinstance(message, Message):
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    await query.answer()
    srch_message = await message.answer(SEARCHING_TEXT)
    await run_search(
        srch_message,
        query.from_user.id if query.from_user else None,
        query_text,
        repo,
        jackett,
        config,
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
    filename = safe_torrent_filename(torrent.title)
    try:
        content = await jackett.download(torrent.download_url)
    except DownloadTooLargeError:
        await message.answer(padded("Файл с трекера слишком большой."))
    except JackettError:
        logger.exception("Download failed for %s", torrent.hash)
        await message.answer(padded("Не удалось скачать файл с трекера."))
    else:
        await message.answer_document(
            BufferedInputFile(file=content, filename=filename)
        )
        if query.from_user is not None:
            await repo.record_download(query.from_user.id, torrent.title, "chat")
    await query.answer()


async def render_page(
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    qh: str,
    page: int,
    cat: str,
    sort: str,
    flt: str = DEFAULT_FILTER,
) -> None:
    message = query.message
    search = await repo.get_search(qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    category = await resolve_category(repo, qh, cat)
    filters = ResultFilters.from_code(flt)
    if category is None and not filters.is_active:
        counter = search.result_count
    else:
        counter = await repo.count_results(qh, category, filters)
    torrent = await repo.get_result_page(qh, category, page, sort, filters)
    if torrent is None:
        if page == 0:
            await query.answer("Ничего не найдено с этими фильтрами", show_alert=True)
        else:
            await query.answer()
        return
    user_id = query.from_user.id if query.from_user else None
    show_server = config.qbit_enabled and config.is_admin(user_id)
    is_fav = user_id is not None and await repo.is_favorite(user_id, torrent.hash)
    keyboard = result_keyboard(
        torrent.hash,
        qh,
        page,
        cat,
        sort,
        has_prev=page > 0,
        has_next=page + 1 < counter,
        show_server=show_server,
        flt=flt,
        is_fav=is_fav,
    )
    await query.answer()
    await message.edit_text(
        "\n".join(text_srch_msg(page + 1, counter, torrent)),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@user_router.callback_query(Pg2.filter(F.t == "pv"))
async def go_priv(
    query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo, config: AppConfig
) -> None:
    await render_page(
        query,
        repo,
        config,
        callback_data.qh,
        callback_data.p - 1,
        callback_data.c,
        callback_data.s,
        callback_data.fl,
    )


@user_router.callback_query(Pg2.filter(F.t == "nx"))
async def go_next(
    query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo, config: AppConfig
) -> None:
    await render_page(
        query,
        repo,
        config,
        callback_data.qh,
        callback_data.p + 1,
        callback_data.c,
        callback_data.s,
        callback_data.fl,
    )


@user_router.callback_query(Pg2.filter(F.t == "fs"))
async def go_searched(
    query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo, config: AppConfig
) -> None:
    await render_page(
        query,
        repo,
        config,
        callback_data.qh,
        callback_data.p,
        callback_data.c,
        callback_data.s,
        callback_data.fl,
    )


def list_line(index: int, torrent: Torrent) -> str:
    # unescape before truncating so a sliced entity ("&am…") cannot leak out
    title = html.unescape(torrent.title)
    if len(title) > 48:
        title = title[:47] + "…"
    size_gb = round(torrent.size / 1024 / 1024 / 1024, 1)
    published = torrent.published_at.strftime("%d.%m.%y")
    return (
        f'{index}. <a href="{torrent.details_url}">{html.escape(title)}</a>'
        f" — {size_gb} GB · 🌱{torrent.seeders} · {published}"
    )


async def render_list(
    query: CallbackQuery,
    repo: TorrentRepo,
    qh: str,
    page: int,
    cat: str,
    sort: str,
    flt: str,
) -> None:
    """Compact numbered list; number buttons jump to the matching card."""
    message = query.message
    search = await repo.get_search(qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    category = await resolve_category(repo, qh, cat)
    filters = ResultFilters.from_code(flt)
    if category is None and not filters.is_active:
        counter = search.result_count
    else:
        counter = await repo.count_results(qh, category, filters)
    offset = page * LIST_PAGE_SIZE
    torrents = await repo.get_result_list(
        qh, LIST_PAGE_SIZE, offset, category, sort, filters
    )
    if not torrents:
        await query.answer("Ничего не найдено с этими фильтрами", show_alert=True)
        return

    def pg2(t: str, p: int, s: str = sort) -> str:
        return Pg2(t=t, qh=qh, p=p, c=cat, s=s, fl=flt).pack()

    lines = [
        f"<b>Результаты {offset + 1}–{offset + len(torrents)}</b> из <b>{counter}</b>\n"
    ]
    lines.extend(
        list_line(offset + i + 1, torrent) for i, torrent in enumerate(torrents)
    )
    # row 1 mirrors the card: back to card view, sort, filters
    top_row = [
        types.InlineKeyboardButton(text="🃏 Карточка", callback_data=pg2("fs", offset)),
        sort_button(pg2("ls", 0, s=SORT_NEXT.get(sort, DEFAULT_SORT)), sort),
        filter_button(qh, cat, sort, flt, ORIGIN_LIST),
    ]
    number_rows = [
        [
            types.InlineKeyboardButton(
                text=str(offset + i + 1), callback_data=pg2("fs", offset + i)
            )
            for i in range(start, min(start + 5, len(torrents)))
        ]
        for start in range(0, len(torrents), 5)
    ]
    nav = []
    if page > 0:
        nav.append(
            types.InlineKeyboardButton(text="⬅", callback_data=pg2("ls", page - 1))
        )
    if offset + len(torrents) < counter:
        nav.append(
            types.InlineKeyboardButton(text="➡", callback_data=pg2("ls", page + 1))
        )
    rows = [top_row, *number_rows, nav] if nav else [top_row, *number_rows]
    await query.answer()
    await message.edit_text(
        padded("\n".join(lines)),
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        disable_web_page_preview=True,
    )


@user_router.callback_query(Pg2.filter(F.t == "ls"))
async def go_list(query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo) -> None:
    await render_list(
        query,
        repo,
        callback_data.qh,
        callback_data.p,
        callback_data.c,
        callback_data.s,
        callback_data.fl,
    )


@user_router.callback_query(Pg2.filter(F.t == "gs"))
async def go_search(
    query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo
) -> None:
    """Legacy: cards sent before the category filter still carry 🗂 buttons."""
    message = query.message
    search = await repo.get_search(callback_data.qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    categories = await repo.get_categories(callback_data.qh)
    if not categories:
        await query.answer()
        return

    def pg2(t: str, p: int, c: str) -> str:
        return Pg2(
            t=t, qh=callback_data.qh, p=p, c=c, s=callback_data.s, fl=callback_data.fl
        ).pack()

    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Все - {search.result_count}",
                callback_data=pg2("fs", 0, ALL_CATEGORIES),
            )
        ]
    ]
    row: list[types.InlineKeyboardButton] = []
    for category, count in categories:
        row.append(
            types.InlineKeyboardButton(
                text=f"{category} - {count}",
                callback_data=pg2("fs", 0, category_token(category)),
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
                # the menu callback kept the position of the card it was
                # opened from — return straight to it
                text="◀️ Назад",
                callback_data=pg2("fs", callback_data.p, callback_data.c),
            )
        ]
    )
    await query.answer()
    await message.edit_text(
        padded("<b>Выберите категорию</b>:"),
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        disable_web_page_preview=True,
    )


def build_filter_menu(
    qh: str, cat: str, sort: str, flt: str, origin: str, category_label: str
) -> tuple[str, types.InlineKeyboardMarkup]:
    filters = ResultFilters.from_code(flt)

    def cb(action: str, new_fl: str, c: str = cat) -> str:
        return Flt(a=action, qh=qh, c=c, s=sort, fl=new_fl, o=origin).pack()

    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Категория: {category_label}  ›",
                callback_data=cb("ct", flt),
            )
        ],
        [
            types.InlineKeyboardButton(
                text=f"Сиды: {SEEDERS_LABELS[filters.seeders]}  🔁",
                callback_data=cb("cs", filters.cycled("seeders").to_code()),
            )
        ],
        [
            types.InlineKeyboardButton(
                text=f"Размер: {SIZE_LABELS[filters.size]}  🔁",
                callback_data=cb("cz", filters.cycled("size").to_code()),
            )
        ],
        [
            types.InlineKeyboardButton(
                text=f"Дата: {DATE_LABELS[filters.date]}  🔁",
                callback_data=cb("cd", filters.cycled("date").to_code()),
            )
        ],
        [
            types.InlineKeyboardButton(
                # reset clears the category as well — it is one of the filters
                text="♻ Сброс",
                callback_data=cb("rs", DEFAULT_FILTER, c=ALL_CATEGORIES),
            ),
            types.InlineKeyboardButton(
                text="✅ Применить", callback_data=cb("ap", flt)
            ),
        ],
    ]
    parts = []
    if cat != ALL_CATEGORIES:
        parts.append(f"категория {category_label}")
    if filters.is_active:
        parts.append(filters.summary())
    active = ", ".join(parts) if parts else "не заданы"
    # escape: size labels contain "<"/">" which break Telegram HTML parsing
    text = padded(f"🔎 <b>Фильтры</b>\nАктивно: {html.escape(active)}")
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


async def render_category_picker(
    query: CallbackQuery, callback_data: Flt, repo: TorrentRepo
) -> None:
    """Category choice inside the filter menu; every option reopens the menu."""
    message = query.message
    search = await repo.get_search(callback_data.qh)
    if search is None or not isinstance(message, Message):
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    categories = await repo.get_categories(callback_data.qh)

    def flt_open(c: str) -> str:
        return Flt(
            a="open",
            qh=callback_data.qh,
            c=c,
            s=callback_data.s,
            fl=callback_data.fl,
            o=callback_data.o,
        ).pack()

    rows = [
        [
            types.InlineKeyboardButton(
                text=f"Все - {search.result_count}",
                callback_data=flt_open(ALL_CATEGORIES),
            )
        ]
    ]
    row: list[types.InlineKeyboardButton] = []
    for category, count in categories:
        row.append(
            types.InlineKeyboardButton(
                text=f"{category} - {count}",
                callback_data=flt_open(category_token(category)),
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
                text="◀️ Назад", callback_data=flt_open(callback_data.c)
            )
        ]
    )
    await query.answer()
    await message.edit_text(
        padded("<b>Категория результатов</b>:"),
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        disable_web_page_preview=True,
    )


@user_router.callback_query(Flt.filter())
async def filter_menu(
    query: CallbackQuery, callback_data: Flt, repo: TorrentRepo, config: AppConfig
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer()
        return
    if callback_data.a in ("ap", "rs"):
        # apply the working filter, or reset — return to the origin view
        applied = DEFAULT_FILTER if callback_data.a == "rs" else callback_data.fl
        cat = ALL_CATEGORIES if callback_data.a == "rs" else callback_data.c
        if callback_data.o == ORIGIN_LIST:
            await render_list(
                query, repo, callback_data.qh, 0, cat, callback_data.s, applied
            )
        else:
            await render_page(
                query, repo, config, callback_data.qh, 0, cat, callback_data.s, applied
            )
        return
    if callback_data.a == "ct":
        await render_category_picker(query, callback_data, repo)
        return
    category = await resolve_category(repo, callback_data.qh, callback_data.c)
    text, keyboard = build_filter_menu(
        callback_data.qh,
        callback_data.c if category is not None else ALL_CATEGORIES,
        callback_data.s,
        callback_data.fl,
        callback_data.o,
        category if category is not None else "Все",
    )
    await query.answer()
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
