"""The search flow on widgets: the card, the results list and the filter sheet.

Users who did not opt into the classic UI (``BotUser.classic_ui``) get the
``search`` widget instead of the text card; the text handlers in
:mod:`tj_bot.handlers.user` keep serving classic users. Every widget query
is resolved through its ``widget_messages`` record, because a query carries
only the request string.
"""

import datetime
import functools
import logging
import re
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject
from aiogram.filters.command import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import SQLAlchemyError

from tj_bot.config import AppConfig
from tj_bot.db.engine import SessionPool
from tj_bot.db.filters import (
    ALL_CATEGORIES,
    DEFAULT_SORT,
    SORT_LABELS,
    ResultFilters,
)
from tj_bot.db.models import SearchQuery, Torrent, WidgetMessage
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.widgets import WidgetUser
from tj_bot.handlers.server_download import begin_torrent_send
from tj_bot.handlers.user import (
    EMPTY_HISTORY_TEXT,
    LIST_PAGE_SIZE,
    NOT_FOUND_TEXT,
    SEARCH_USAGE_TEXT,
    SEARCHING_TEXT,
    STALE_QUERY_TEXT,
    Hst,
    Upd,
    perform_search,
    resolve_category,
)
from tj_bot.services.cards import (
    SearchContext,
    cut,
    done_state,
    filters_caption,
    filters_done_text,
    filters_state,
    list_caption,
    list_state,
    more_answer,
    search_state,
)
from tj_bot.services.enrichment import CardMeta, Enricher
from tj_bot.services.formatting import padded
from tj_bot.services.jackett import (
    DownloadTooLargeError,
    JackettClient,
    JackettError,
    safe_torrent_filename,
)
from tj_bot.services.qbittorrent import QbittorrentClient
from tj_bot.widgets.api import WidgetQuery
from tj_bot.widgets.dispatch import Verb, WidgetRouter
from tj_bot.widgets.send import (
    answer_error,
    answer_json,
    edit_widget_state,
    send_widget,
)

logger = logging.getLogger(__name__)

widgets_search_router = WidgetRouter(name="widgets_search")

KIND_CARD = "search"
KIND_LIST = "search-list"
KIND_FILTERS = "search-filters"
# enrichment budgets: the first card may wait for the tracker page, a page
# turn must stay snappy (the widget's request times out at 15 s)
SEARCH_BUDGET = 4.0
NAV_BUDGET = 0.0
# «ещё» is a deliberate tap: a short wait for the page is fine
MORE_BUDGET = 3.0
PREFETCH_CARDS = 3
NOT_FOUND_FILTERED_TEXT = "Ничего не найдено с этими фильтрами"
NO_RIGHTS_TEXT = "Недостаточно прав"
QBIT_OFF_TEXT = "qBittorrent недоступен"
DOWNLOAD_TOO_LARGE_TEXT = "Файл с трекера слишком большой."
DOWNLOAD_FAILED_TEXT = "Не удалось скачать файл с трекера."
CLASSIC_ON_TEXT = "Классический режим включён"
CLASSIC_OFF_TEXT = "Классический режим выключен"
HEX8 = re.compile(r"[0-9a-f]{8}")
HASH_PREFIX = re.compile(r"[0-9a-f]{1,40}")


@dataclass(frozen=True, slots=True)
class Ctx:
    """The ``<qh>:<i>:<s>:<f>:<c>`` request context of the search widgets."""

    qh: str  # 8-character prefix
    index: int
    sort: str
    filter_code: str
    category: str


def parse_ctx(args: list[str]) -> Ctx | None:
    if len(args) < 5:
        return None
    qh, index, sort, code, category = args[:5]
    valid = (
        HEX8.fullmatch(qh) is not None
        and index.isdigit()
        and sort in SORT_LABELS
        and len(code) == 3
        and code.isdigit()
        and (category == ALL_CATEGORIES or HEX8.fullmatch(category) is not None)
    )
    if not valid:
        return None
    return Ctx(qh, int(index), sort, code, category)


@dataclass(frozen=True, slots=True)
class ResultSet:
    """A cached search narrowed by a widget's category and filters."""

    search: SearchQuery
    category: str | None
    token: str  # the category token as the widgets echo it
    filters: ResultFilters
    total: int


async def load_result_set(repo: TorrentRepo, qh: str, ctx: Ctx) -> ResultSet | None:
    search = await repo.get_search(qh)
    if search is None:
        return None
    category = await resolve_category(repo, qh, ctx.category)
    filters = ResultFilters.from_code(ctx.filter_code)
    if category is None and not filters.is_active:
        total = search.result_count
    else:
        total = await repo.count_results(qh, category, filters)
    token = ctx.category if category is not None else ALL_CATEGORIES
    return ResultSet(search, category, token, filters, total)


async def queried_widget(query: WidgetQuery, repo: TorrentRepo) -> WidgetMessage | None:
    """The record of the widget that sent ``query``."""
    if query.chat is None or query.message_id is None:
        return None
    return await repo.get_widget_message(query.chat.id, query.message_id)


async def own_widget(
    query: WidgetQuery, repo: TorrentRepo, ctx: Ctx | None, *kinds: str
) -> WidgetMessage | None:
    """The queried widget when it is of one of ``kinds`` and about ``ctx``'s search."""
    if ctx is None:
        return None
    row = await queried_widget(query, repo)
    if row is None or row.kind not in kinds:
        return None
    if str(row.context.get("qh", ""))[:8] != ctx.qh:
        return None
    return row


def context_search(context: dict[str, Any]) -> SearchContext:
    """The card's search context as recorded in ``widget_messages``."""
    return SearchContext(
        qh=str(context["qh"]),
        index=int(context["i"]),
        total=int(context["n"]),
        sort=str(context["s"]),
        filter_code=str(context["f"]),
        category=str(context["c"]),
        from_cache=bool(context.get("fc")),
        is_admin=bool(context.get("adm")),
    )


def card_context(
    ctx: SearchContext,
    torrent: Torrent,
    meta: CardMeta,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "qh": ctx.qh,
        "i": ctx.index,
        "n": ctx.total,
        "s": ctx.sort,
        "f": ctx.filter_code,
        "c": ctx.category,
        "h": torrent.hash,
        "fc": int(ctx.from_cache),
        "adm": int(ctx.is_admin),
        "en": int(meta.pending),
    }
    if previous and previous.get("list") is not None:
        context["list"] = previous["list"]
    return context


def refresh_keyboard(qh: str) -> InlineKeyboardMarkup:
    """The one button the widget leaves to the caption keyboard (NOTES §3)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄", callback_data=Upd(qh=qh).pack())]
        ]
    )


async def send_card(
    bot: Bot,
    repo: TorrentRepo,
    enricher: Enricher,
    chat_id: int,
    user_id: int,
    torrent: Torrent,
    ctx: SearchContext,
) -> None:
    meta = await enricher.card_meta(torrent, budget=SEARCH_BUDGET)
    is_fav = await repo.is_favorite(user_id, torrent.hash)
    state = search_state(torrent, ctx, meta, rev=1, is_fav=is_fav)
    await send_widget(
        bot,
        chat_id,
        KIND_CARD,
        state,
        repo=repo,
        kind=KIND_CARD,
        context=card_context(ctx, torrent, meta, None),
        reply_markup=refresh_keyboard(ctx.qh),
    )


async def update_card(
    bot: Bot,
    repo: TorrentRepo,
    enricher: Enricher,
    row: WidgetMessage,
    torrent: Torrent,
    ctx: SearchContext,
    *,
    user_id: int,
    query: WidgetQuery | None,
) -> None:
    """Turn a sent card to ``torrent``, answering ``query`` with the new state first."""
    meta = await enricher.card_meta(torrent, budget=NAV_BUDGET)
    is_fav = await repo.is_favorite(user_id, torrent.hash)
    state = search_state(torrent, ctx, meta, rev=row.rev + 1, is_fav=is_fav)
    if query is not None:
        await answer_json(query, state)
    await edit_widget_state(
        bot,
        row.chat_id,
        row.message_id,
        state,
        repo=repo,
        context=card_context(ctx, torrent, meta, row.context),
    )


async def run_widget_search(
    placeholder: Message,
    user_id: int,
    raw_query: str,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
    force_refresh: bool = False,
) -> None:
    """Search and send the first result as a widget card below ``placeholder``."""
    found = await perform_search(
        placeholder, user_id, raw_query, repo, jackett, config, force_refresh
    )
    if found is None:
        return
    qh, total, from_cache = found
    torrent = await repo.get_result_page(qh, None, 0, DEFAULT_SORT)
    if torrent is None:
        await placeholder.edit_text(NOT_FOUND_TEXT)
        return
    ctx = SearchContext(
        qh=qh,
        index=0,
        total=total,
        from_cache=from_cache,
        is_admin=config.qbit_enabled and config.is_admin(user_id),
    )
    await send_card(bot, repo, enricher, placeholder.chat.id, user_id, torrent, ctx)
    try:
        await placeholder.delete()
    except TelegramAPIError:
        logger.debug("Search placeholder already gone")
    enricher.prefetch(await repo.get_result_list(qh, PREFETCH_CARDS, 1))


@widgets_search_router.message(Command("classic"))
async def toggle_classic(message: Message, repo: TorrentRepo) -> None:
    if message.from_user is None:
        return
    enabled = not await repo.uses_classic_ui(message.from_user.id)
    await repo.set_classic_ui(message.from_user.id, enabled)
    await message.answer(padded(CLASSIC_ON_TEXT if enabled else CLASSIC_OFF_TEXT))


@widgets_search_router.message(F.text, Command("s"), WidgetUser())
async def search_widget(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
    command: CommandObject,
) -> None:
    if message.from_user is None:
        return
    if command.args is None:
        await message.answer(SEARCH_USAGE_TEXT)
        return
    placeholder = await message.answer(SEARCHING_TEXT)
    await run_widget_search(
        placeholder,
        message.from_user.id,
        command.args,
        repo,
        jackett,
        config,
        enricher,
        bot,
    )


@widgets_search_router.message(Command("last"), WidgetUser())
async def repeat_last_widget(
    message: Message,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    if message.from_user is None:
        return
    history = await repo.get_user_history(message.from_user.id, limit=1)
    if not history:
        await message.answer(EMPTY_HISTORY_TEXT)
        return
    placeholder = await message.answer(SEARCHING_TEXT)
    await run_widget_search(
        placeholder,
        message.from_user.id,
        history[0][1],
        repo,
        jackett,
        config,
        enricher,
        bot,
    )


@widgets_search_router.callback_query(Hst.filter(), WidgetUser())
async def replay_history_widget(
    query: CallbackQuery,
    callback_data: Hst,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    message = query.message
    query_text = await repo.get_query_text(callback_data.qid)
    if query_text is None or not isinstance(message, Message):
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    await query.answer()
    placeholder = await message.answer(SEARCHING_TEXT)
    await run_widget_search(
        placeholder,
        query.from_user.id,
        query_text,
        repo,
        jackett,
        config,
        enricher,
        bot,
    )


@widgets_search_router.callback_query(Upd.filter(), WidgetUser())
async def refresh_widget(
    query: CallbackQuery,
    callback_data: Upd,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    """🔄 under a widget card: a fresh search as a new card.

    A widget message cannot be edited into the searching text, so the old
    card stays and keeps working on its cached results.
    """
    message = query.message
    search = await repo.get_search(callback_data.qh)
    if search is None or not search.query_text or not isinstance(message, Message):
        await query.answer(STALE_QUERY_TEXT, show_alert=True)
        return
    await query.answer("Обновляю…")
    placeholder = await message.answer(SEARCHING_TEXT)
    await run_widget_search(
        placeholder,
        query.from_user.id,
        search.query_text,
        repo,
        jackett,
        config,
        enricher,
        bot,
        force_refresh=True,
    )


@widgets_search_router.widget_query(Verb("n", "p", "s"))
async def card_navigate(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    repo: TorrentRepo,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_CARD)
    if ctx is None or row is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), ctx)
    index = {"n": ctx.index + 1, "p": ctx.index - 1, "s": 0}[verb]
    if results is None or index < 0:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    torrent = await repo.get_result_page(
        results.search.hash, results.category, index, ctx.sort, results.filters
    )
    if torrent is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    user_id = query.from_user.id
    search_ctx = SearchContext(
        qh=results.search.hash,
        index=index,
        total=results.total,
        sort=ctx.sort,
        filter_code=results.filters.to_code(),
        category=results.token,
        from_cache=bool(row.context.get("fc")),
        is_admin=config.qbit_enabled and config.is_admin(user_id),
    )
    await update_card(
        bot, repo, enricher, row, torrent, search_ctx, user_id=user_id, query=query
    )


async def list_page_state(
    repo: TorrentRepo, results: ResultSet, ctx: Ctx, page: int, *, rev: int
) -> dict[str, Any]:
    offset = page * LIST_PAGE_SIZE
    torrents = await repo.get_result_list(
        results.search.hash,
        LIST_PAGE_SIZE,
        offset,
        results.category,
        ctx.sort,
        results.filters,
    )
    search_ctx = SearchContext(
        qh=results.search.hash,
        index=offset,
        total=results.total,
        sort=ctx.sort,
        filter_code=results.filters.to_code(),
        category=results.token,
    )
    return list_state(
        torrents, search_ctx, rev=rev, query_text=results.search.query_text
    )


@widgets_search_router.widget_query(Verb("ls"))
async def list_verb(
    query: WidgetQuery, args: list[str], repo: TorrentRepo, bot: Bot
) -> None:
    """«Список» on the card opens the list; the sort chip on the list re-sorts it."""
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_CARD, KIND_LIST)
    if ctx is None or row is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), ctx)
    if results is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    if row.kind == KIND_LIST:
        state = await list_page_state(repo, results, ctx, 0, rev=row.rev + 1)
        await answer_json(query, state)
        await edit_widget_state(bot, row.chat_id, row.message_id, state, repo=repo)
        return
    await answer_json(query, {"ok": 1})
    page = ctx.index // LIST_PAGE_SIZE
    state = await list_page_state(repo, results, ctx, page, rev=1)
    sent = await send_widget(
        bot,
        row.chat_id,
        KIND_LIST,
        state,
        list_caption(state, results.search.query_text),
        repo=repo,
        kind=KIND_LIST,
        context={"qh": results.search.hash, "card": row.message_id},
    )
    await repo.update_widget_message(
        row.chat_id, row.message_id, context={**row.context, "list": sent.message_id}
    )


@widgets_search_router.widget_query(Verb("fs"))
async def open_filters(
    query: WidgetQuery, args: list[str], repo: TorrentRepo, bot: Bot
) -> None:
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_CARD, KIND_LIST)
    if ctx is None or row is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), ctx)
    if results is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    # answered before the sheet goes out so the widget's pending state clears
    await answer_json(query, {"ok": 1})
    if row.kind == KIND_CARD:
        card_mid, list_mid = row.message_id, row.context.get("list")
    else:
        card_mid, list_mid = row.context["card"], row.message_id
    context: dict[str, Any] = {"qh": results.search.hash, "card": card_mid}
    if list_mid is not None:
        context["list"] = list_mid
    search_ctx = SearchContext(
        qh=results.search.hash,
        index=ctx.index,
        total=results.search.result_count,
        sort=ctx.sort,
        filter_code=results.filters.to_code(),
        category=results.token,
    )
    state = filters_state(
        search_ctx, rev=1, categories=await repo.get_categories(results.search.hash)
    )
    await send_widget(
        bot,
        row.chat_id,
        KIND_FILTERS,
        state,
        filters_caption(results.search.query_text, results.filters, results.category),
        repo=repo,
        kind=KIND_FILTERS,
        context=context,
    )


@widgets_search_router.widget_query(Verb("lp"))
async def list_page(
    query: WidgetQuery, args: list[str], repo: TorrentRepo, bot: Bot
) -> None:
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_LIST)
    if ctx is None or row is None or len(args) < 6 or not args[5].isdigit():
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), ctx)
    if results is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    state = await list_page_state(repo, results, ctx, int(args[5]), rev=row.rev + 1)
    await answer_json(query, state)
    await edit_widget_state(bot, row.chat_id, row.message_id, state, repo=repo)


async def close_widget(bot: Bot, repo: TorrentRepo, row: WidgetMessage) -> bool:
    """Delete a list or sheet message once it served its purpose."""
    try:
        await bot.delete_message(row.chat_id, row.message_id)
    except TelegramAPIError:
        logger.info("Widget %s/%s not deleted", row.chat_id, row.message_id)
        return False
    await repo.delete_widget_message(row.chat_id, row.message_id)
    return True


async def finish_widget(
    bot: Bot, repo: TorrentRepo, row: WidgetMessage, state: dict[str, Any]
) -> None:
    """Edit a list or sheet into its done state (errors logged: it may be gone)."""
    try:
        await edit_widget_state(
            bot,
            row.chat_id,
            row.message_id,
            state,
            repo=repo,
            context={**row.context, "done": 1},
        )
    except TelegramAPIError:
        logger.warning("Widget %s/%s not finished", row.chat_id, row.message_id)


async def show_card(
    bot: Bot,
    repo: TorrentRepo,
    enricher: Enricher,
    config: AppConfig,
    row: WidgetMessage,
    results: ResultSet,
    ctx: Ctx,
    index: int,
    *,
    user_id: int,
) -> None:
    """Send a fresh card for ``index`` of ``results`` below the list or sheet."""
    torrent = await repo.get_result_page(
        results.search.hash, results.category, index, ctx.sort, results.filters
    )
    if torrent is None:
        logger.info("Result %s of %s is gone", index, results.search.hash[:8])
        return
    card = await repo.get_widget_message(row.chat_id, int(row.context["card"]))
    search_ctx = SearchContext(
        qh=results.search.hash,
        index=index,
        total=results.total,
        sort=ctx.sort,
        filter_code=results.filters.to_code(),
        category=results.token,
        from_cache=bool(card.context.get("fc")) if card is not None else False,
        is_admin=config.qbit_enabled and config.is_admin(user_id),
    )
    try:
        await send_card(bot, repo, enricher, row.chat_id, user_id, torrent, search_ctx)
    except TelegramAPIError:
        logger.warning("Card for %s/%s not sent", row.chat_id, row.message_id)


@widgets_search_router.widget_query(Verb("cd"))
async def open_card(
    query: WidgetQuery,
    args: list[str],
    repo: TorrentRepo,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    """A row tap on the list: the card jumps to that index, the list is done."""
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_LIST)
    if ctx is None or row is None or len(args) < 6 or not args[5].isdigit():
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), ctx)
    if results is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    index = int(args[5])
    await answer_json(query, {"ok": 1})
    await show_card(
        bot,
        repo,
        enricher,
        config,
        row,
        results,
        ctx,
        index,
        user_id=query.from_user.id,
    )
    if not await close_widget(bot, repo, row):
        done = done_state(
            results.search.hash,
            row.rev + 1,
            f"Открыта карточка {index + 1}",
            q=cut(results.search.query_text or "", 24),
            sl=SORT_LABELS[ctx.sort],
        )
        await finish_widget(bot, repo, row, done)


@widgets_search_router.widget_query(Verb("x"))
async def close_sheet(
    query: WidgetQuery, args: list[str], repo: TorrentRepo, bot: Bot
) -> None:
    """«Закрыть» on the list or the sheet: the message disappears."""
    row = await queried_widget(query, repo)
    if row is None or row.kind not in (KIND_LIST, KIND_FILTERS):
        await answer_error(query, STALE_QUERY_TEXT)
        return
    await answer_json(query, {"ok": 1})
    if not await close_widget(bot, repo, row):
        await finish_widget(
            bot, repo, row, done_state(str(row.context["qh"]), row.rev + 1, "Закрыто")
        )


@widgets_search_router.widget_query(Verb("f"))
async def apply_filters(
    query: WidgetQuery,
    args: list[str],
    repo: TorrentRepo,
    config: AppConfig,
    enricher: Enricher,
    bot: Bot,
) -> None:
    """«Применить» on the sheet: card 0 of the filtered set, list and sheet done."""
    ctx = parse_ctx(args)
    row = await own_widget(query, repo, ctx, KIND_FILTERS)
    applied = parse_ctx([*args[:1], "0", *args[2:3], *args[5:7]]) if ctx else None
    if row is None or applied is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    results = await load_result_set(repo, str(row.context["qh"]), applied)
    if results is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    if results.total == 0:
        await answer_error(query, NOT_FOUND_FILTERED_TEXT)
        return
    await answer_json(query, {"ok": 1})
    await show_card(
        bot,
        repo,
        enricher,
        config,
        row,
        results,
        applied,
        0,
        user_id=query.from_user.id,
    )
    list_mid = row.context.get("list")
    if list_mid is not None:
        list_row = await repo.get_widget_message(row.chat_id, int(list_mid))
        if list_row is not None:
            await close_widget(bot, repo, list_row)
    if not await close_widget(bot, repo, row):
        text = filters_done_text(results.filters, results.category, results.total)
        await finish_widget(
            bot, repo, row, done_state(results.search.hash, row.rev + 1, text)
        )


async def card_torrent(
    query: WidgetQuery, repo: TorrentRepo, args: list[str]
) -> tuple[WidgetMessage, Torrent] | None:
    """The queried card and the torrent its ``<h>`` argument points at."""
    row = await queried_widget(query, repo)
    if row is None or row.kind != KIND_CARD or not args:
        return None
    if HASH_PREFIX.fullmatch(args[0]) is None:
        return None
    torrent = await repo.get_torrent_by_hash_prefix(str(row.context["qh"]), args[0])
    return None if torrent is None else (row, torrent)


@widgets_search_router.widget_query(Verb("d"))
async def download_torrent(
    query: WidgetQuery,
    args: list[str],
    repo: TorrentRepo,
    jackett: JackettClient,
    bot: Bot,
) -> None:
    """«Скачать .torrent»: answered first, the file follows (it may take a while)."""
    found = await card_torrent(query, repo, args)
    if found is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    row, torrent = found
    await answer_json(query, {"ok": 1})
    await repo.commit()
    try:
        content = await jackett.download(torrent.download_url)
    except DownloadTooLargeError:
        await bot.send_message(row.chat_id, padded(DOWNLOAD_TOO_LARGE_TEXT))
        return
    except JackettError:
        logger.exception("Download failed for %s", torrent.hash)
        await bot.send_message(row.chat_id, padded(DOWNLOAD_FAILED_TEXT))
        return
    await bot.send_document(
        row.chat_id,
        BufferedInputFile(content, filename=safe_torrent_filename(torrent.title)),
    )
    await repo.record_download(query.from_user.id, torrent.title, "chat")


@widgets_search_router.widget_query(Verb("fav"))
async def toggle_favorite_widget(
    query: WidgetQuery, args: list[str], repo: TorrentRepo
) -> None:
    found = await card_torrent(query, repo, args)
    if found is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    row, torrent = found
    user_id = query.from_user.id
    if await repo.add_favorite(user_id, torrent):
        is_fav = 1
    else:
        await repo.remove_favorite_by_hash(user_id, torrent.hash)
        is_fav = 0
    rev = row.rev + 1
    await answer_json(query, {"fav": is_fav, "r": rev})
    await repo.update_widget_message(row.chat_id, row.message_id, rev=rev)


@widgets_search_router.widget_query(Verb("srv"))
async def send_to_server(
    query: WidgetQuery,
    args: list[str],
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    bot: Bot,
) -> None:
    if not (config.qbit_enabled and config.is_admin(query.from_user.id)):
        await answer_error(query, NO_RIGHTS_TEXT)
        return
    if qbit is None or query.chat is None:
        await answer_error(query, QBIT_OFF_TEXT)
        return
    found = await card_torrent(query, repo, args)
    if found is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    row, torrent = found
    await answer_json(query, {"ok": 1})
    # begin_torrent_send replies into the chat through a Message of the card
    message = Message(
        message_id=row.message_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=query.chat,
    ).as_(bot)
    await begin_torrent_send(
        None,
        message,
        repo,
        jackett,
        qbit,
        config,
        bot,
        torrent.title,
        torrent.download_url,
    )


@widgets_search_router.widget_query(Verb("more"))
async def more_details(
    query: WidgetQuery, args: list[str], repo: TorrentRepo, enricher: Enricher
) -> None:
    found = await card_torrent(query, repo, args)
    if found is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    _, torrent = found
    meta = await enricher.card_meta(torrent, budget=MORE_BUDGET)
    await answer_json(query, more_answer(meta, torrent))


async def apply_enrichment(
    bot: Bot, repo: TorrentRepo, enricher: Enricher, torrent_hash: str, meta: CardMeta
) -> None:
    """Redraw the sent cards of ``torrent_hash`` that were waiting for ``meta``."""
    torrent: Torrent | None = None
    for row in await repo.widget_messages_of_kind(KIND_CARD):
        if row.context.get("h") != torrent_hash or not row.context.get("en"):
            continue
        if torrent is None:
            torrent = await repo.get_torrent_by_hash(torrent_hash)
            if torrent is None:
                return
        ctx = context_search(row.context)
        is_fav = await repo.is_favorite(row.chat_id, torrent_hash)
        state = search_state(torrent, ctx, meta, rev=row.rev + 1, is_fav=is_fav)
        try:
            await edit_widget_state(
                bot,
                row.chat_id,
                row.message_id,
                state,
                repo=repo,
                context=card_context(ctx, torrent, meta, row.context),
            )
        except TelegramAPIError:
            logger.warning(
                "Enrichment push to %s/%s failed",
                row.chat_id,
                row.message_id,
                exc_info=True,
            )


async def push_enrichment(
    bot: Bot,
    session_pool: SessionPool,
    enricher: Enricher,
    torrent_hash: str,
    meta: CardMeta,
) -> None:
    try:
        async with session_pool() as session:
            repo = TorrentRepo(session)
            await apply_enrichment(bot, repo, enricher, torrent_hash, meta)
            await session.commit()
    except SQLAlchemyError:
        logger.exception("Enrichment push for %s failed", torrent_hash)


def register_enrichment_push(
    bot: Bot, session_pool: SessionPool, enricher: Enricher
) -> None:
    """Have ready enrichment results pushed into the cards that wait for them."""
    enricher.on_ready(functools.partial(push_enrichment, bot, session_pool, enricher))
