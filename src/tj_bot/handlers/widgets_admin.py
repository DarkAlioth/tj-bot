"""Admin tooling on widgets: ``dashboard``, ``users`` and ``broadcast``.

Protocols: ``docs/widgets/<name>/PROTOCOL.md``. The command handlers serve
admins on the widget UI; admins who switched to the classic UI fall through
to ``admin_info`` and ``users``, which stay unchanged.
"""

import asyncio
import datetime
import html
import logging
import shutil
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tj_bot.commands import set_user_commands
from tj_bot.config import AppConfig
from tj_bot.db.models import WidgetMessage
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.filters.widgets import WidgetKind, WidgetUser
from tj_bot.handlers.user import SEARCHING_TEXT
from tj_bot.handlers.widgets_search import run_widget_search
from tj_bot.services import broadcaster
from tj_bot.services.admin_views import (
    ACTIVITY_ROWS,
    STATS_WINDOW_DAYS,
    TOP_QUERIES,
    USER_GROUPS,
    USERS_PAGE_SIZE,
    BroadcastStatus,
    Health,
    QbitSummary,
    activity_answer,
    broadcast_caption,
    broadcast_done_caption,
    broadcast_state,
    dashboard_caption,
    dashboard_state,
    dashboard_tick,
    indexer_rows,
    user_row,
    users_caption,
    users_state,
    with_toast,
)
from tj_bot.services.enrichment import Enricher
from tj_bot.services.formatting import DOWNLOADING_STATES, padded
from tj_bot.services.jackett import JackettClient
from tj_bot.services.monitor import MonitorState, probe_now
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError
from tj_bot.widgets.api import AnswerWidgetQuery, WidgetQuery
from tj_bot.widgets.dispatch import Verb, WidgetRouter
from tj_bot.widgets.send import (
    answer_error,
    answer_json,
    edit_widget_state,
    send_widget,
)

logger = logging.getLogger(__name__)

widgets_admin_router = WidgetRouter(name="widgets_admin")
widgets_admin_router.message.filter(AdminOnly())
widgets_admin_router.widget_query.filter(AdminOnly())

type SessionPool = async_sessionmaker[AsyncSession]

FIRST_REV = 1
DASHBOARD_TABS = {"health": "h", "stats": "s", "indexers": "i"}
DB_SIZE_TTL_SECONDS = 60
QBIT_TTL_SECONDS = 5
BROADCAST_PACING_SECONDS = 0.05

ERR_BAD_REQUEST = "Неверный запрос"
ERR_STALE_TOP = "Список устарел, откройте панель заново"
ERR_USER_NOT_FOUND = "Пользователь не найден"
ERR_SUPER_BLOCK = "Супер-админа нельзя заблокировать"
ERR_SELF_BLOCK = "Нельзя заблокировать себя"
ERR_ADMIN_BLOCK = "Сначала снимите админа"
ERR_SUPER_ROLE = "Супер-админ задан в .env"
ERR_SELF_ROLE = "Нельзя изменить свою роль"
ERR_DRAFT_CLOSED = "Черновик уже закрыт"


class Cached[T]:
    """One value reloaded at most once per ``ttl`` seconds."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._entry: tuple[float, T] | None = None

    async def get(self, load: Callable[[], Awaitable[T]]) -> T:
        now = time.monotonic()
        if self._entry is None or now >= self._entry[0]:
            self._entry = (now + self._ttl, await load())
        return self._entry[1]


db_size_cache = Cached[int](DB_SIZE_TTL_SECONDS)
qbit_cache = Cached[QbitSummary | None](QBIT_TTL_SECONDS)

# strong references to the fire-and-forget tasks (chk probe, top-query
# search, broadcast delivery) so the loop does not drop them mid-way
background_tasks: set[asyncio.Task[None]] = set()


def spawn(coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    task = asyncio.create_task(coroutine)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def parse_user_args(args: list[str]) -> tuple[int, bool] | None:
    """``<id>:<1|0>`` of the block/admin verbs."""
    if len(args) != 2 or args[1] not in ("0", "1"):
        return None
    user_id = parse_int(args[0])
    return None if user_id is None else (user_id, args[1] == "1")


def message_key(query: WidgetQuery) -> tuple[int, int]:
    # the kind filter already proved both are present
    assert query.chat is not None and query.message_id is not None  # noqa: S101
    return query.chat.id, query.message_id


# --- dashboard -------------------------------------------------------------


async def load_qbit(qbit: QbittorrentClient | None) -> QbitSummary | None:
    if qbit is None:
        return None
    try:
        torrents = await qbit.list_torrents()
        transfer = await qbit.transfer_info()
        free = await qbit.free_space()
    except QbittorrentError:
        logger.debug("qBittorrent unreachable for the dashboard", exc_info=True)
        return None
    return QbitSummary(
        active=sum(1 for t in torrents if t.get("state") in DOWNLOADING_STATES),
        total=len(torrents),
        dl_speed=int(transfer.get("dl_info_speed", 0)),
        up_speed=int(transfer.get("up_info_speed", 0)),
        free=free,
    )


async def collect_health(
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    monitor: MonitorState,
    started_at: datetime.datetime,
) -> Health:
    uptime = datetime.datetime.now(datetime.UTC) - started_at
    disk = shutil.disk_usage("/")
    return Health(
        uptime=int(uptime.total_seconds()),
        disk=(disk.free, disk.total),
        db_size=await db_size_cache.get(repo.db_size),
        qbit=await qbit_cache.get(lambda: load_qbit(qbit)),
        checked_at=monitor.checked_at,
    )


async def build_dashboard(
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    monitor: MonitorState,
    started_at: datetime.datetime,
    *,
    tab: str,
    rev: int,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """State, caption and the stored context of a dashboard message."""
    health = await collect_health(repo, qbit, monitor, started_at)
    stats = await repo.get_stats(
        datetime.timedelta(days=STATS_WINDOW_DAYS), top_limit=TOP_QUERIES
    )
    per_day = await repo.search_counts_by_day(STATS_WINDOW_DAYS)
    state = dashboard_state(
        rev=rev,
        now=int(time.time()),
        tab=tab,
        health=health,
        stats=stats,
        per_day=per_day,
        indexers=monitor.indexers,
        problems=monitor.problems,
    )
    caption = dashboard_caption(health, stats, monitor.indexers, monitor.problems)
    # q:<i> resolves against the top list this message shows, not a newer one
    context = {"a": tab, "top": [row[0] for row in state["s"]["top"]], "i": state["i"]}
    return state, caption, context


@widgets_admin_router.message(Command("health", "stats", "indexers"), WidgetUser())
async def show_dashboard(
    message: Message,
    command: CommandObject,
    bot: Bot,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    monitor: MonitorState,
    started_at: datetime.datetime,
) -> None:
    state, caption, context = await build_dashboard(
        repo,
        qbit,
        monitor,
        started_at,
        tab=DASHBOARD_TABS[command.command],
        rev=FIRST_REV,
    )
    await send_widget(
        bot,
        message.chat.id,
        "dashboard",
        state,
        caption,
        repo=repo,
        kind="dashboard",
        context=context,
    )


@widgets_admin_router.widget_query(Verb("bg"), WidgetKind("dashboard"))
async def dashboard_bg(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    monitor: MonitorState,
    started_at: datetime.datetime,
) -> AnswerWidgetQuery:
    """Fresh ``h`` from cached sources; ``i`` only when the rows changed."""
    health = await collect_health(repo, qbit, monitor, started_at)
    rows = indexer_rows(monitor.indexers, monitor.problems)
    if rows == widget.context.get("i"):
        rows_changed = None
    else:
        rows_changed = rows
        chat_id, message_id = message_key(query)
        await repo.update_widget_message(
            chat_id, message_id, context={**widget.context, "i": rows}
        )
    return answer_json(
        query, dashboard_tick(widget.rev, int(time.time()), health, rows_changed)
    )


async def finish_check(
    bot: Bot,
    chat_id: int,
    message_id: int,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    monitor: MonitorState,
    started_at: datetime.datetime,
    session_pool: SessionPool,
) -> None:
    """Run the probe (up to ~30 s) and publish the result as a new revision."""
    await probe_now(bot, jackett, qbit, config, monitor)
    try:
        async with session_pool() as session:
            repo = TorrentRepo(session)
            widget = await repo.get_widget_message(chat_id, message_id)
            if widget is None:
                return
            state, _, context = await build_dashboard(
                repo,
                qbit,
                monitor,
                started_at,
                tab=str(widget.context.get("a", "h")),
                rev=widget.rev + 1,
            )
            await edit_widget_state(
                bot, chat_id, message_id, state, repo=repo, context=context
            )
            await session.commit()
    except (TelegramAPIError, SQLAlchemyError):
        logger.exception(
            "Dashboard %s/%s: check result not published", chat_id, message_id
        )


@widgets_admin_router.widget_query(Verb("chk"), WidgetKind("dashboard"))
async def dashboard_check(
    query: WidgetQuery,
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    monitor: MonitorState,
    started_at: datetime.datetime,
    session_pool: SessionPool,
) -> AnswerWidgetQuery:
    chat_id, message_id = message_key(query)
    spawn(
        finish_check(
            bot,
            chat_id,
            message_id,
            jackett,
            qbit,
            config,
            monitor,
            started_at,
            session_pool,
        )
    )
    return answer_json(query, {"ok": 1, "pending": 1})


async def run_top_query(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    session_pool: SessionPool,
) -> None:
    """Run a tapped top query as a widget search in a new message."""
    status = await bot.send_message(chat_id, SEARCHING_TEXT)
    async with session_pool() as session:
        await run_widget_search(
            status, user_id, text, TorrentRepo(session), jackett, config, enricher, bot
        )
        await session.commit()


@widgets_admin_router.widget_query(Verb("q"), WidgetKind("dashboard"))
async def dashboard_query(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    bot: Bot,
    jackett: JackettClient,
    config: AppConfig,
    enricher: Enricher,
    session_pool: SessionPool,
) -> AnswerWidgetQuery:
    index = parse_int(args[0]) if len(args) == 1 else None
    if index is None:
        return answer_error(query, ERR_BAD_REQUEST)
    top: list[str] = list(widget.context.get("top", []))
    if not 0 <= index < len(top):
        return answer_error(query, ERR_STALE_TOP)
    chat_id, _ = message_key(query)
    spawn(
        run_top_query(
            bot,
            chat_id,
            query.from_user.id,
            top[index],
            jackett,
            config,
            enricher,
            session_pool,
        )
    )
    return answer_json(query, {"ok": 1})


# --- users -----------------------------------------------------------------


async def build_users(
    repo: TorrentRepo, config: AppConfig, group: str, page: int, rev: int
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    counts = [
        await repo.count_users(name, config.super_admins)
        for name in USER_GROUPS.values()
    ]
    total = counts[list(USER_GROUPS).index(group)]
    pages = max(1, -(-total // USERS_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    users = await repo.list_users_page(
        USERS_PAGE_SIZE, page * USERS_PAGE_SIZE, USER_GROUPS[group], config.super_admins
    )
    rows = []
    for user in users:
        searches, downloads = await repo.user_activity_counts(user.user_id)
        rows.append(
            user_row(user, config.is_super_admin(user.user_id), searches, downloads)
        )
    state = users_state(
        rev=rev,
        now=int(time.time()),
        group=group,
        page=page,
        pages=pages,
        counts=counts,
        rows=rows,
    )
    return state, users_caption(counts, page, pages), {"g": group, "p": page}


@widgets_admin_router.message(Command("users"), WidgetUser())
async def show_users(
    message: Message, bot: Bot, repo: TorrentRepo, config: AppConfig
) -> None:
    state, caption, context = await build_users(repo, config, "a", 0, FIRST_REV)
    await send_widget(
        bot,
        message.chat.id,
        "users",
        state,
        caption,
        repo=repo,
        kind="users",
        context=context,
    )


async def respond_users(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
    group: str,
    page: int,
    toast: str | None = None,
) -> AnswerWidgetQuery:
    """Publish the page as the next revision and answer with the same state."""
    chat_id, message_id = message_key(query)
    state, _, context = await build_users(repo, config, group, page, widget.rev + 1)
    await edit_widget_state(bot, chat_id, message_id, state, repo=repo, context=context)
    return answer_json(query, with_toast(state, toast))


@widgets_admin_router.widget_query(Verb("t"), WidgetKind("users"))
async def users_tab(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> AnswerWidgetQuery:
    if len(args) != 1 or args[0] not in USER_GROUPS:
        return answer_error(query, ERR_BAD_REQUEST)
    return await respond_users(query, widget, repo, config, bot, args[0], 0)


@widgets_admin_router.widget_query(Verb("pg"), WidgetKind("users"))
async def users_page(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> AnswerWidgetQuery:
    page = parse_int(args[1]) if len(args) == 2 else None
    if page is None or args[0] not in USER_GROUPS:
        return answer_error(query, ERR_BAD_REQUEST)
    return await respond_users(query, widget, repo, config, bot, args[0], page)


def current_page(widget: WidgetMessage) -> tuple[str, int]:
    group = str(widget.context.get("g", "a"))
    page = widget.context.get("p", 0)
    return (group if group in USER_GROUPS else "a"), (
        page if isinstance(page, int) else 0
    )


@widgets_admin_router.widget_query(Verb("blk"), WidgetKind("users"))
async def users_block(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> AnswerWidgetQuery:
    parsed = parse_user_args(args)
    if parsed is None:
        return answer_error(query, ERR_BAD_REQUEST)
    user_id, blocked = parsed
    if config.is_super_admin(user_id):
        return answer_error(query, ERR_SUPER_BLOCK)
    if user_id == query.from_user.id:
        return answer_error(query, ERR_SELF_BLOCK)
    user = await repo.get_user(user_id)
    if user is None:
        return answer_error(query, ERR_USER_NOT_FOUND)
    if blocked and user.admin:
        return answer_error(query, ERR_ADMIN_BLOCK)
    await repo.set_blocked(user_id, blocked)
    group, page = current_page(widget)
    toast = "Заблокирован" if blocked else "Разблокирован"
    return await respond_users(query, widget, repo, config, bot, group, page, toast)


@widgets_admin_router.widget_query(Verb("adm"), WidgetKind("users"))
async def users_admin(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    config: AppConfig,
    bot: Bot,
) -> AnswerWidgetQuery:
    parsed = parse_user_args(args)
    if parsed is None:
        return answer_error(query, ERR_BAD_REQUEST)
    user_id, promote = parsed
    if config.is_super_admin(user_id):
        return answer_error(query, ERR_SUPER_ROLE)
    if user_id == query.from_user.id:
        return answer_error(query, ERR_SELF_ROLE)
    if await repo.get_user(user_id) is None:
        return answer_error(query, ERR_USER_NOT_FOUND)
    # promotion also unblocks (repo.set_admin), as in the classic card
    await repo.set_admin(user_id, promote)
    # keep the in-memory admin set live so access changes take effect at once
    if promote:
        config.dynamic_admins.add(user_id)
    else:
        config.dynamic_admins.discard(user_id)
    await set_user_commands(bot, user_id, promote)
    group, page = current_page(widget)
    toast = "Назначен админом" if promote else "Снят с админа"
    return await respond_users(query, widget, repo, config, bot, group, page, toast)


@widgets_admin_router.widget_query(Verb("act"), WidgetKind("users"))
async def users_activity(
    query: WidgetQuery, args: list[str], repo: TorrentRepo
) -> AnswerWidgetQuery:
    user_id = parse_int(args[0]) if len(args) == 1 else None
    if user_id is None:
        return answer_error(query, ERR_BAD_REQUEST)
    searches = await repo.get_user_searches(user_id, ACTIVITY_ROWS)
    downloads = await repo.get_user_downloads(user_id, ACTIVITY_ROWS)
    return answer_json(query, activity_answer(searches, downloads))


# --- broadcast -------------------------------------------------------------


@dataclass
class BroadcastProgress:
    """Delivery counters of one running broadcast, read by ``bg`` ticks."""

    rev: int
    recipients: int
    ok: int = 0
    fail: int = 0
    status: BroadcastStatus = "run"
    task: asyncio.Task[None] | None = None


broadcasts: dict[tuple[int, int], BroadcastProgress] = {}


def broadcast_context(
    status: BroadcastStatus, text: str, progress: BroadcastProgress
) -> dict[str, Any]:
    return {
        "st": status,
        "tx": text,
        "n": progress.recipients,
        "ok": progress.ok,
        "fail": progress.fail,
    }


@widgets_admin_router.message(Command("broadcast"), WidgetUser())
async def broadcast_draft(
    message: Message, command: CommandObject, bot: Bot, repo: TorrentRepo
) -> None:
    """The draft: the text as typed, confirmed or cancelled inside the widget."""
    if not command.args:
        await message.answer(
            padded("Использование: <code>/broadcast текст рассылки</code>"),
            parse_mode=ParseMode.HTML,
        )
        return
    text = command.args
    recipients = len(await repo.active_user_ids())
    state = broadcast_state(
        rev=FIRST_REV, status="draft", recipients=recipients, ok=0, fail=0, text=text
    )
    await send_widget(
        bot,
        message.chat.id,
        "broadcast",
        state,
        broadcast_caption(recipients),
        repo=repo,
        kind="broadcast",
        context={"st": "draft", "tx": text, "n": recipients},
    )


async def deliver_broadcast(
    bot: Bot,
    key: tuple[int, int],
    progress: BroadcastProgress,
    user_ids: list[int],
    text: str,
    session_pool: SessionPool,
) -> None:
    """Send to every user at the broadcaster's pace, then publish ``done``."""
    payload = html.escape(text)
    for user_id in user_ids:
        if await broadcaster.send_message(bot, user_id, payload):
            progress.ok += 1
        else:
            progress.fail += 1
        await asyncio.sleep(BROADCAST_PACING_SECONDS)
    progress.status = "done"
    progress.rev += 1
    logger.info(
        "Broadcast %s: %s delivered, %s failed", key, progress.ok, progress.fail
    )
    chat_id, message_id = key
    state = broadcast_state(
        rev=progress.rev,
        status="done",
        recipients=progress.recipients,
        ok=progress.ok,
        fail=progress.fail,
        text=text,
    )
    try:
        async with session_pool() as session:
            repo = TorrentRepo(session)
            await edit_widget_state(
                bot,
                chat_id,
                message_id,
                state,
                repo=repo,
                context=broadcast_context("done", text, progress),
            )
            await session.commit()
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=broadcast_done_caption(progress.ok, progress.recipients),
        )
    except (TelegramAPIError, SQLAlchemyError):
        logger.exception("Broadcast %s: report not published", key)


@widgets_admin_router.widget_query(Verb("go"), WidgetKind("broadcast"))
async def broadcast_go(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    bot: Bot,
    session_pool: SessionPool,
) -> AnswerWidgetQuery:
    context = widget.context
    if context.get("st") != "draft":
        return answer_error(query, ERR_DRAFT_CLOSED)
    key = message_key(query)
    text = str(context.get("tx", ""))
    user_ids = await repo.active_user_ids()
    progress = BroadcastProgress(rev=widget.rev + 1, recipients=len(user_ids))
    state = broadcast_state(
        rev=progress.rev,
        status="run",
        recipients=progress.recipients,
        ok=0,
        fail=0,
        text=text,
    )
    await edit_widget_state(
        bot, *key, state, repo=repo, context=broadcast_context("run", text, progress)
    )
    broadcasts[key] = progress
    progress.task = spawn(
        deliver_broadcast(bot, key, progress, user_ids, text, session_pool)
    )
    return answer_json(query, {"ok": 1, "st": "run"})


@widgets_admin_router.widget_query(Verb("bg"), WidgetKind("broadcast"))
async def broadcast_bg(query: WidgetQuery, widget: WidgetMessage) -> AnswerWidgetQuery:
    """Progress from memory; after a restart the stored context is the report."""
    progress = broadcasts.get(message_key(query))
    if progress is None:
        context = widget.context
        status = str(context.get("st", "draft"))
        answer = {
            "r": widget.rev,
            # a delivery interrupted by a restart never reports: close it
            "st": "done" if status == "run" else status,
            "n": context.get("n", 0),
            "ok": context.get("ok", 0),
            "fail": context.get("fail", 0),
        }
        return answer_json(query, answer)
    answer = {
        "st": progress.status,
        "n": progress.recipients,
        "ok": progress.ok,
        "fail": progress.fail,
    }
    if progress.status != "run":
        answer["r"] = progress.rev
    return answer_json(query, answer)


@widgets_admin_router.widget_query(Verb("x"), WidgetKind("broadcast"))
async def broadcast_cancel(
    query: WidgetQuery, widget: WidgetMessage, repo: TorrentRepo, bot: Bot
) -> AnswerWidgetQuery:
    context = widget.context
    if context.get("st") not in ("draft", "run"):
        return answer_error(query, ERR_DRAFT_CLOSED)
    key = message_key(query)
    text = str(context.get("tx", ""))
    progress = broadcasts.pop(key, None)
    if progress is None:
        progress = BroadcastProgress(
            rev=widget.rev, recipients=int(context.get("n", 0))
        )
    elif progress.task is not None:
        progress.task.cancel()
    progress.status = "cancel"
    progress.rev += 1
    state = broadcast_state(
        rev=progress.rev,
        status="cancel",
        recipients=progress.recipients,
        ok=progress.ok,
        fail=progress.fail,
        text=text,
    )
    await edit_widget_state(
        bot, *key, state, repo=repo, context=broadcast_context("cancel", text, progress)
    )
    return answer_json(
        query,
        {
            "r": progress.rev,
            "st": "cancel",
            "n": progress.recipients,
            "ok": progress.ok,
            "fail": progress.fail,
        },
    )
