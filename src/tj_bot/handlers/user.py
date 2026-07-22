import datetime
import hashlib
import logging

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import Torrent
from tj_bot.db.repo import TorrentRepo
from tj_bot.services.jackett import DownloadTooLargeError, JackettClient, JackettError

# Dlt with type="server" is handled by the admin router
__all__ = ["Dlt", "Pg2", "result_keyboard", "user_router"]

logger = logging.getLogger(__name__)

user_router = Router()

ALL_CATEGORIES = "ALL"
DEFAULT_SORT = "se"
NOT_FOUND_TEXT = "К сожалению ничего не найдено 😔"
SEARCH_UNAVAILABLE_TEXT = "Поиск временно недоступен, попробуйте позже 🛠"
SEARCHING_TEXT = "Поиск выполняется, ожидайте..."
STALE_QUERY_TEXT = "Запрос устарел, выполните новый поиск: /s"

SORT_LABELS = {"se": "↕ Сиды", "sz": "↕ Размер", "dt": "↕ Дата"}
SORT_NEXT = {"se": "sz", "sz": "dt", "dt": "se"}


class Dlt(CallbackData, prefix="dlt"):
    type: str
    hash: str


class Pg2(CallbackData, prefix="pg2"):
    t: str
    qh: str
    p: int
    c: str
    s: str


class Upd(CallbackData, prefix="upd"):
    qh: str


class Hst(CallbackData, prefix="hst"):
    qid: int


class Sub(CallbackData, prefix="sub"):
    a: str
    i: int
    qh: str


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


def result_keyboard(
    torrent_hash: str,
    qh: str,
    page: int,
    cat: str,
    sort: str,
    has_prev: bool,
    has_next: bool,
    show_server: bool = False,
) -> types.InlineKeyboardMarkup:
    def pg2(t: str, p: int = page, c: str = cat, s: str = sort) -> str:
        return Pg2(t=t, qh=qh, p=p, c=c, s=s).pack()

    kb = [
        [
            types.InlineKeyboardButton(
                text="💾 Скачать",
                callback_data=Dlt(type="download", hash=torrent_hash).pack(),
            ),
            types.InlineKeyboardButton(
                # carries the current position so the menu's back button can
                # return to exactly this card
                text="🗂 Категории",
                callback_data=pg2("gs"),
            ),
        ]
    ]
    tools = [
        types.InlineKeyboardButton(
            text=SORT_LABELS.get(sort, SORT_LABELS[DEFAULT_SORT]),
            callback_data=pg2("fs", p=0, s=SORT_NEXT.get(sort, DEFAULT_SORT)),
        ),
        types.InlineKeyboardButton(
            text="🔔",
            callback_data=Sub(a="add", i=0, qh=qh).pack(),
        ),
    ]
    if show_server:
        tools.append(
            types.InlineKeyboardButton(
                text="⬇️ На сервер",
                callback_data=Dlt(type="server", hash=torrent_hash).pack(),
            )
        )
    kb.append(tools)
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
        "⠀\n⠀Приветсвую!\n",
        "Для поиска введите:",
        "<code>/s Название</code>\n",
        "История ваших запросов: /history\n⠀",
    ]
    await message.answer("\n".join(text), parse_mode=ParseMode.HTML)


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
    keyboard = result_keyboard(
        torrent.hash,
        qh,
        0,
        ALL_CATEGORIES,
        DEFAULT_SORT,
        has_prev=False,
        has_next=counter > 1,
        show_server=show_server,
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
    qh = hashlib.md5(
        ",".join(item.hash for item in items).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
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
        text = ["⠀\nДля поиска используйте:", "<code>/s Название</code>\n⠀"]
        await message.answer("\n".join(text), parse_mode=ParseMode.HTML)
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


@user_router.message(Command("history"))
async def show_history(message: Message, repo: TorrentRepo) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    history = await repo.get_user_history(user_id)
    if not history:
        await message.answer("История поиска пуста. Начните с /s")
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
        "🕘 <b>Недавние запросы</b> — нажмите, чтобы повторить:",
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
    query: CallbackQuery,
    repo: TorrentRepo,
    config: AppConfig,
    qh: str,
    page: int,
    cat: str,
    sort: str,
) -> None:
    message = query.message
    search = await repo.get_search(qh)
    if search is None or not isinstance(message, Message):
        await query.answer()
        return
    category = await resolve_category(repo, qh, cat)
    counter = (
        search.result_count
        if category is None
        else await repo.count_results(qh, category)
    )
    torrent = await repo.get_result_page(qh, category, page, sort)
    if torrent is None:
        await query.answer()
        return
    show_server = config.qbit_enabled and config.is_admin(
        query.from_user.id if query.from_user else None
    )
    keyboard = result_keyboard(
        torrent.hash,
        qh,
        page,
        cat,
        sort,
        has_prev=page > 0,
        has_next=page + 1 < counter,
        show_server=show_server,
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
    )


@user_router.callback_query(Pg2.filter(F.t == "gs"))
async def go_search(
    query: CallbackQuery, callback_data: Pg2, repo: TorrentRepo
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

    def pg2(t: str, p: int, c: str) -> str:
        return Pg2(t=t, qh=callback_data.qh, p=p, c=c, s=callback_data.s).pack()

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
        "⠀\n⠀<b>Выберите категорию</b>:\n⠀",
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        disable_web_page_preview=True,
    )


def subs_view(
    subscriptions: list[object],
) -> tuple[str, types.InlineKeyboardMarkup | None]:
    if not subscriptions:
        return (
            "Подписок нет. Нажмите 🔔 на карточке поиска, чтобы следить "
            "за новинками по запросу.",
            None,
        )
    rows = [
        [
            types.InlineKeyboardButton(
                text=f"❌ {sub.query_text}",  # type: ignore[attr-defined]
                callback_data=Sub(
                    a="del",
                    i=sub.id,  # type: ignore[attr-defined]
                    qh="",
                ).pack(),
            )
        ]
        for sub in subscriptions
    ]
    text = (
        "🔔 <b>Подписки</b> — бот проверяет новинки по расписанию.\n"
        "Нажмите, чтобы отписаться:"
    )
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


@user_router.callback_query(Sub.filter(F.a == "add"))
async def subscribe(
    query: CallbackQuery,
    callback_data: Sub,
    repo: TorrentRepo,
    config: AppConfig,
) -> None:
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        await query.answer()
        return
    search = await repo.get_search(callback_data.qh)
    if search is None or not search.query_text:
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    if not config.is_admin(user.id):
        count = await repo.count_subscriptions(user.id)
        if count >= config.settings.subscriptions_per_user:
            await query.answer(
                f"Лимит подписок: {config.settings.subscriptions_per_user}. "
                "Удалите лишние: /subs",
                show_alert=True,
            )
            return
    subscription = await repo.create_subscription(
        user.id, message.chat.id, search.query_text
    )
    if subscription is None:
        await query.answer("Вы уже подписаны на этот запрос ✔")
        return
    # prefill so only future results count as news
    await repo.add_seen_hashes(
        subscription.id, await repo.get_result_hashes(callback_data.qh)
    )
    await query.answer("🔔 Подписка создана — сообщу о новинках")


@user_router.message(Command("subs"))
async def list_subs(message: Message, repo: TorrentRepo) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    text, keyboard = subs_view(list(await repo.list_subscriptions(user_id)))
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@user_router.callback_query(Sub.filter(F.a == "del"))
async def unsubscribe(
    query: CallbackQuery, callback_data: Sub, repo: TorrentRepo
) -> None:
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        await query.answer()
        return
    removed = await repo.delete_subscription(callback_data.i, user.id)
    await query.answer("Подписка удалена" if removed else "Уже удалена")
    text, keyboard = subs_view(list(await repo.list_subscriptions(user.id)))
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
