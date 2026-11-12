"""The ``hub`` widget: /start, /history and /favorites as one tabbed bubble.

Protocol: docs/widgets/hub/PROTOCOL.md. Users with ``classic_ui`` fall
through to the text handlers in ``user.py`` / ``favorites.py``.
"""

import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.filters.command import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from tj_bot.config import AppConfig
from tj_bot.db.models import WidgetMessage
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.widgets import WidgetUser
from tj_bot.handlers.favorites import GONE_TEXT
from tj_bot.handlers.server_download import QBIT_DOWN_TEXT, begin_torrent_send
from tj_bot.handlers.user import SEARCHING_TEXT, run_search
from tj_bot.services.formatting import padded
from tj_bot.services.hub_views import (
    FAVORITES_PAGE_SIZE,
    FILTER_ALL,
    FILTER_UPDATED,
    FILTERS,
    HISTORY_ROWS,
    TAB_FAVORITES,
    TAB_HISTORY,
    TAB_MAIN,
    TABS,
    HubStatus,
    HubStatusSource,
    clamp_page,
    details_link,
    hub_caption,
    hub_state,
)
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

HUB = "hub"
STALE_TEXT = "Виджет устарел — отправьте /start"
UNKNOWN_VERB_TEXT = "Неизвестная команда"
STALE_QUERY_TEXT = "Запрос устарел, выполните новый поиск"
NO_RIGHTS_TEXT = "Недостаточно прав"
SOON_TEXT = "Скоро"
TOO_LARGE_TEXT = "Файл с трекера слишком большой."
DOWNLOAD_FAILED_TEXT = "Не удалось скачать — ссылка могла устареть, найдите заново."
ADMIN_WIDGETS = {"con": "console", "dash": "dashboard", "usr": "users"}

# registered as dp["send_admin_widget"] by the admin/server widgets:
# (bot, chat_id, widget name, repo) -> sends that widget as a new message
SendAdminWidget = Callable[[Bot, int, str, TorrentRepo], Awaitable[None]]

hub_router = WidgetRouter(name="hub")
hub_router.message.filter(WidgetUser())


class HubQuery(BaseFilter):
    """Pass queries of hub widgets; ``hub`` is None for a forgotten message."""

    async def __call__(
        self, query: WidgetQuery, repo: TorrentRepo
    ) -> bool | dict[str, Any]:
        if query.chat is None or query.message_id is None:
            return False
        row = await repo.get_widget_message(query.chat.id, query.message_id)
        if row is not None and row.kind != HUB:
            return False
        return {"hub": row}


hub_router.widget_query.filter(HubQuery())


def hub_status(monitor: HubStatusSource | None) -> HubStatus | None:
    return None if monitor is None else monitor.hub_status()


async def build_state(
    repo: TorrentRepo,
    config: AppConfig,
    user_id: int,
    *,
    tab: str,
    rev: int,
    page: int = 0,
    favorites_filter: str = FILTER_ALL,
    status: HubStatus | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the tab's rows; returns the state and the context to remember."""
    total = await repo.count_favorites(user_id)
    updated = await repo.count_updated_favorites(user_id)
    history = []
    favorites = []
    if tab == TAB_FAVORITES:
        updated_only = favorites_filter == FILTER_UPDATED
        page = clamp_page(page, updated if updated_only else total)
        favorites = await repo.list_favorites(
            user_id,
            FAVORITES_PAGE_SIZE,
            page * FAVORITES_PAGE_SIZE,
            updated_only=updated_only,
        )
    else:
        page = 0
        favorites_filter = FILTER_ALL
        history = await repo.get_user_history_entries(user_id, HISTORY_ROWS[tab])
    state = hub_state(
        tab=tab,
        rev=rev,
        admin=config.is_admin(user_id),
        favorites_total=total,
        favorites_updated=updated,
        history=history,
        favorites=favorites,
        favorites_filter=favorites_filter,
        favorites_page=page,
        status=status,
    )
    return state, {"tab": tab, "page": page, "filter": favorites_filter}


async def open_hub(
    message: Message,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    tab: str,
    monitor: HubStatusSource | None,
) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    state, context = await build_state(
        repo, config, user_id, tab=tab, rev=1, status=hub_status(monitor)
    )
    await send_widget(
        bot,
        message.chat.id,
        HUB,
        state,
        hub_caption(config.is_admin(user_id)),
        repo=repo,
        kind=HUB,
        context=context,
    )


@hub_router.message(CommandStart())
async def hub_start(
    message: Message,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    monitor: HubStatusSource | None = None,
) -> None:
    await open_hub(message, repo, config, bot, TAB_MAIN, monitor)


@hub_router.message(Command("history"))
async def hub_history(
    message: Message,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    monitor: HubStatusSource | None = None,
) -> None:
    await open_hub(message, repo, config, bot, TAB_HISTORY, monitor)


@hub_router.message(Command("favorites"))
async def hub_favorites(
    message: Message,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    monitor: HubStatusSource | None = None,
) -> None:
    await open_hub(message, repo, config, bot, TAB_FAVORITES, monitor)


async def show_view(
    query: WidgetQuery,
    hub: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    *,
    tab: str,
    page: int = 0,
    favorites_filter: str = FILTER_ALL,
    status: HubStatus | None = None,
) -> None:
    """Answer with the view's fragment, then write it as the new chat_data."""
    state, context = await build_state(
        repo,
        config,
        query.from_user.id,
        tab=tab,
        rev=hub.rev + 1,
        page=page,
        favorites_filter=favorites_filter,
        status=status,
    )
    await answer_json(query, state)
    await edit_widget_state(
        bot, hub.chat_id, hub.message_id, state, repo=repo, context=context
    )


def context_view(hub: WidgetMessage) -> tuple[str, int, str]:
    """(tab, page, filter) remembered for the message, defaults for odd data."""
    context = hub.context
    tab = context.get("tab")
    page = context.get("page")
    favorites_filter = context.get("filter")
    return (
        tab if tab in TABS else TAB_MAIN,
        page if isinstance(page, int) and page >= 0 else 0,
        favorites_filter if favorites_filter in FILTERS else FILTER_ALL,
    )


def hub_message(query: WidgetQuery, bot: Bot) -> Message:
    """The hub message as an aiogram handle for flows that ``message.answer``."""
    assert query.chat is not None and query.message_id is not None  # noqa: S101  # HubQuery
    return Message(
        message_id=query.message_id,
        date=datetime.datetime.now(datetime.UTC),
        chat=query.chat,
    ).as_(bot)


@hub_router.widget_query(Verb("t", "ff", "fp"))
async def switch_view(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    monitor: HubStatusSource | None = None,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    _, page, favorites_filter = context_view(hub)
    arg = args[0] if args else ""
    if verb == "t" and arg in TABS:
        tab = arg
        if tab == TAB_FAVORITES:
            page, favorites_filter = 0, FILTER_ALL
    elif verb == "ff" and arg in FILTERS:
        tab, page, favorites_filter = TAB_FAVORITES, 0, arg
    elif verb == "fp" and arg.isdigit():
        tab, page = TAB_FAVORITES, int(arg)
    else:
        await answer_error(query, UNKNOWN_VERB_TEXT)
        return
    await show_view(
        query,
        hub,
        repo,
        config,
        bot,
        tab=tab,
        page=page,
        favorites_filter=favorites_filter,
        status=hub_status(monitor),
    )


@hub_router.widget_query(Verb("hc"))
async def clear_history(
    query: WidgetQuery,
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    await repo.clear_user_history(query.from_user.id)
    await show_view(query, hub, repo, config, bot, tab=TAB_HISTORY)


@hub_router.widget_query(Verb("h"))
async def replay_search(
    query: WidgetQuery,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    jackett: JackettClient,
    config: AppConfig,
    bot: Bot,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    query_text = None
    if args and args[0].isdigit():
        query_text = await repo.get_query_text(int(args[0]))
    if query_text is None:
        await answer_error(query, STALE_QUERY_TEXT)
        return
    await answer_json(query, {"ok": 1})
    sent = await bot.send_message(hub.chat_id, SEARCHING_TEXT)
    await run_search(sent, query.from_user.id, query_text, repo, jackett, config)


def favorite_id(args: list[str]) -> int | None:
    return int(args[0]) if args and args[0].isdigit() else None


@hub_router.widget_query(Verb("fo"))
async def open_favorite(
    query: WidgetQuery,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    fav_id = favorite_id(args)
    favorite = None
    if fav_id is not None:
        favorite = await repo.get_favorite(query.from_user.id, fav_id)
    if favorite is None:
        await answer_error(query, GONE_TEXT)
        return
    await repo.mark_favorites_seen(query.from_user.id, favorite.id)
    await answer_json(query, details_link(favorite.details_url))


@hub_router.widget_query(Verb("fd"))
async def download_favorite(
    query: WidgetQuery,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    jackett: JackettClient,
    bot: Bot,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    user_id = query.from_user.id
    fav_id = favorite_id(args)
    favorite = None
    if fav_id is not None:
        favorite = await repo.get_favorite(user_id, fav_id)
    if favorite is None:
        await answer_error(query, GONE_TEXT)
        return
    # answered before the tracker round-trip: the 15 s answer window is
    # shorter than a slow Jackett download
    await answer_json(query, {"ok": 1})
    await repo.mark_favorites_seen(user_id, favorite.id)
    await repo.commit()
    try:
        content = await jackett.download(favorite.download_url)
    except DownloadTooLargeError:
        await bot.send_message(hub.chat_id, padded(TOO_LARGE_TEXT))
    except JackettError:
        logger.exception("Favorite download failed for %s", favorite.id)
        await bot.send_message(hub.chat_id, padded(DOWNLOAD_FAILED_TEXT))
    else:
        await bot.send_document(
            hub.chat_id,
            BufferedInputFile(
                file=content, filename=safe_torrent_filename(favorite.title)
            ),
        )
        await repo.record_download(user_id, favorite.title, "chat")


@hub_router.widget_query(Verb("fs"))
async def favorite_to_server(
    query: WidgetQuery,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    bot: Bot,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    user_id = query.from_user.id
    if not config.is_admin(user_id):
        await answer_error(query, NO_RIGHTS_TEXT)
        return
    if qbit is None:
        await answer_error(query, QBIT_DOWN_TEXT)
        return
    fav_id = favorite_id(args)
    favorite = None
    if fav_id is not None:
        favorite = await repo.get_favorite(user_id, fav_id)
    if favorite is None:
        await answer_error(query, GONE_TEXT)
        return
    await answer_json(query, {"ok": 1})
    await repo.mark_favorites_seen(user_id, favorite.id)
    await begin_torrent_send(
        None,
        hub_message(query, bot),
        repo,
        jackett,
        qbit,
        config,
        favorite.title,
        favorite.download_url,
    )


@hub_router.widget_query(Verb("fr"))
async def remove_favorite(
    query: WidgetQuery,
    args: list[str],
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    fav_id = favorite_id(args)
    if fav_id is None:
        await answer_error(query, UNKNOWN_VERB_TEXT)
        return
    await repo.remove_favorite(query.from_user.id, fav_id)
    _, page, favorites_filter = context_view(hub)
    await show_view(
        query,
        hub,
        repo,
        config,
        bot,
        tab=TAB_FAVORITES,
        page=page,
        favorites_filter=favorites_filter,
    )


@hub_router.widget_query(Verb(*ADMIN_WIDGETS))
async def open_admin_widget(
    query: WidgetQuery,
    verb: str,
    hub: WidgetMessage | None,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    send_admin_widget: SendAdminWidget | None = None,
) -> None:
    if hub is None:
        await answer_error(query, STALE_TEXT)
        return
    if not config.is_admin(query.from_user.id):
        await answer_error(query, NO_RIGHTS_TEXT)
        return
    if send_admin_widget is None:
        await answer_error(query, SOON_TEXT)
        return
    await answer_json(query, {"ok": 1})
    await send_admin_widget(bot, hub.chat_id, ADMIN_WIDGETS[verb], repo)


@hub_router.widget_query()
async def unknown_verb(query: WidgetQuery, hub: WidgetMessage | None) -> None:
    await answer_error(query, UNKNOWN_VERB_TEXT if hub is not None else STALE_TEXT)
