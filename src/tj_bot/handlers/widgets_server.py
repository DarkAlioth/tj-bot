"""qBittorrent on widgets for admins: file picker, live progress, console.

Users who opted into the classic UI (``BotUser.classic_ui``) keep the text
screens of ``server_download`` and ``qbit_console``; everyone else gets these
widgets. A widget query carries no state of its own, so each message's
context (torrent hash, page, tab…) lives in ``widget_messages``.

Protocols: ``docs/widgets/{picker,progress,console}/PROTOCOL.md``.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from tj_bot.db.models import WidgetMessage
from tj_bot.db.repo import TorrentRepo
from tj_bot.filters.admin import AdminOnly
from tj_bot.handlers.qbit_console import NOT_CONFIGURED_TEXT
from tj_bot.services import qbit_views as views
from tj_bot.services.formatting import padded
from tj_bot.services.qbittorrent import (
    QbittorrentClient,
    QbittorrentError,
    QueueingDisabledError,
)
from tj_bot.widgets.api import WidgetQuery
from tj_bot.widgets.dispatch import Verb, WidgetRouter
from tj_bot.widgets.send import (
    answer_error,
    answer_json,
    edit_widget_state,
    send_widget,
)

logger = logging.getLogger(__name__)

widgets_server_router = WidgetRouter(name="widgets_server")
widgets_server_router.message.filter(AdminOnly())
widgets_server_router.widget_query.filter(AdminOnly())

PICKER = "picker"
PROGRESS = "progress"
CONSOLE = "console"
PRIORITY_ACTIONS = {
    "top": "topPrio",
    "up": "increasePrio",
    "down": "decreasePrio",
    "bottom": "bottomPrio",
}
HASH_PREFIX = re.compile(r"[0-9a-f]{8}")


class WidgetUi(BaseFilter):
    """Users on the widget UI (the classic text flows serve the others)."""

    async def __call__(self, message: Message, repo: TorrentRepo) -> bool:
        user = message.from_user
        return user is not None and not await repo.uses_classic_ui(user.id)


class Kind(BaseFilter):
    """The query comes from one of our widgets; passes its ``widget`` record."""

    def __init__(self, *kinds: str) -> None:
        self.kinds = frozenset(kinds)

    async def __call__(
        self, query: WidgetQuery, repo: TorrentRepo
    ) -> bool | dict[str, Any]:
        if query.chat is None or query.message_id is None:
            return False
        widget = await repo.get_widget_message(query.chat.id, query.message_id)
        if widget is None or widget.kind not in self.kinds:
            return False
        return {"widget": widget}


def _now() -> int:
    return int(time.time())


def _arg_int(args: list[str]) -> int:
    """The first request argument as a non-negative int, -1 when absent or bad."""
    return int(args[0]) if args and args[0].isdigit() else -1


async def _edit_caption(bot: Bot, widget: WidgetMessage, caption: str) -> None:
    """Refresh the fallback caption; best effort (identical text is rejected)."""
    try:
        await bot.edit_message_caption(
            chat_id=widget.chat_id, message_id=widget.message_id, caption=caption
        )
    except TelegramAPIError:
        logger.debug(
            "Caption edit skipped for %s/%s", widget.chat_id, widget.message_id
        )


async def _find_torrent(
    qbit: QbittorrentClient, args: list[str]
) -> dict[str, Any] | None:
    """Resolve the 8-hex prefix a widget sends (untrusted) to a listed torrent."""
    prefix = args[0] if args else ""
    if not HASH_PREFIX.fullmatch(prefix):
        return None
    torrents = await qbit.list_torrents()
    return next(
        (t for t in torrents if str(t.get("hash") or "").startswith(prefix)), None
    )


# --- picker -------------------------------------------------------------------


async def picker_message(
    qbit: QbittorrentClient, torrent_hash: str
) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """State, caption and context of a fresh picker; None when the torrent is gone."""
    info = await qbit.torrent_info(torrent_hash)
    if info is None:
        return None
    view = views.picker_view(await qbit.torrent_files(torrent_hash))
    free_space = await qbit.free_space()
    name = str(info.get("name") or "?")
    state = views.picker_state(name, view, free_space, page=0, rev=1)
    context = {"h": torrent_hash, "n": name, "page": 0}
    return state, views.picker_caption(name, view), context


async def send_picker_widget(
    bot: Bot,
    chat_id: int,
    repo: TorrentRepo,
    qbit: QbittorrentClient,
    torrent_hash: str,
) -> bool:
    """Open the file picker widget; False when the torrent is not in qBittorrent."""
    prepared = await picker_message(qbit, torrent_hash)
    if prepared is None:
        return False
    state, caption, context = prepared
    await send_widget(
        bot, chat_id, PICKER, state, caption, repo=repo, kind=PICKER, context=context
    )
    return True


async def _picker_finish(
    bot: Bot, repo: TorrentRepo, widget: WidgetMessage, mode: str, caption: str
) -> None:
    """Switch the picker to a terminal mode (``go``, ``x``, ``gone``)."""
    state = views.picker_terminal(str(widget.context["n"]), mode, widget.rev + 1)
    await edit_widget_state(
        bot, widget.chat_id, widget.message_id, state, repo=repo, context=widget.context
    )
    await _edit_caption(bot, widget, caption)


async def _picker_gone(
    query: WidgetQuery, bot: Bot, repo: TorrentRepo, widget: WidgetMessage
) -> None:
    await answer_error(query, views.GONE_TEXT)
    await _picker_finish(bot, repo, widget, "gone", views.GONE_TEXT)


def _selection(
    view: views.PickerView, verb: str, args: list[str]
) -> tuple[list[views.PickerFile], int] | None:
    """Files and the priority a selection verb applies; None for a bad request."""
    if verb in ("all", "none"):
        return view.files, int(verb == "all")
    if verb == "tg":
        file = view.by_index(_arg_int(args))
        return ([file], int(not file.selected)) if file is not None else None
    if verb == "tf":
        folder = _arg_int(args)
        files = view.in_folder(folder) if folder >= 0 else []
    else:
        files = view.in_class(args[0]) if args and args[0] in views.CLASS_ORDER else []
    return (files, views.toggle_priority(files)) if files else None


@widgets_server_router.widget_query(Verb("tg", "tf", "qp", "all", "none"), Kind(PICKER))
async def picker_select(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    torrent_hash = str(widget.context["h"])
    try:
        info = await qbit.torrent_info(torrent_hash)
        if info is None:
            await _picker_gone(query, bot, repo, widget)
            return
        view = views.picker_view(await qbit.torrent_files(torrent_hash))
        selection = _selection(view, verb, args)
        if selection is None:
            await answer_error(query, views.BAD_REQUEST_TEXT)
            return
        files, priority = selection
        ids = "|".join(str(f.index) for f in files)
        await qbit.set_file_priority(torrent_hash, ids, priority)
        view = views.picker_view(await qbit.torrent_files(torrent_hash))
        free_space = await qbit.free_space()
    except QbittorrentError:
        logger.exception("Picker %s failed", verb)
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    # a toggled file is on the page the user sees; bulk verbs keep the stored page
    page = (
        view.page_of(files[0].index)
        if verb == "tg"
        else int(widget.context.get("page", 0))
    )
    rev = widget.rev + 1
    await answer_json(query, views.picker_fragment(view, page, rev))
    name = str(info.get("name") or "?")
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        views.picker_state(name, view, free_space, page, rev),
        repo=repo,
        context={**widget.context, "page": view.clamp(page)},
    )


@widgets_server_router.widget_query(Verb("pg"), Kind(PICKER))
async def picker_turn_page(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    torrent_hash = str(widget.context["h"])
    try:
        if await qbit.torrent_info(torrent_hash) is None:
            await _picker_gone(query, bot, repo, widget)
            return
        view = views.picker_view(await qbit.torrent_files(torrent_hash))
    except QbittorrentError:
        logger.exception("Picker page failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    page = view.clamp(_arg_int(args))
    await answer_json(query, views.picker_page(view, page, widget.rev))
    await repo.update_widget_message(
        widget.chat_id, widget.message_id, context={**widget.context, "page": page}
    )


@widgets_server_router.widget_query(Verb("go"), Kind(PICKER))
async def picker_start(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    torrent_hash = str(widget.context["h"])
    try:
        info = await qbit.torrent_info(torrent_hash)
        if info is None:
            await _picker_gone(query, bot, repo, widget)
            return
        selected, total = views.selected_counts(await qbit.torrent_files(torrent_hash))
        if not selected:
            await answer_error(query, views.NO_FILES_TEXT)
            return
        await qbit.start_torrents(torrent_hash)
        started = await qbit.torrent_info(torrent_hash) or info
    except QbittorrentError:
        logger.exception("Picker start failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    name = str(info.get("name") or "?")
    if str(info.get("state")) in views.PAUSED_STATES:
        # a torrent parked by the add flow; one reopened from the console while
        # running is not a new download
        await repo.record_download(query.from_user.id, name, "server")
    await answer_json(query, {"r": widget.rev + 1, "m": "go"})
    await _picker_finish(bot, repo, widget, "go", views.picker_started_caption(name))
    await send_progress_widget(bot, widget.chat_id, repo, started, selected, total)


@widgets_server_router.widget_query(Verb("x"), Kind(PICKER))
async def picker_cancel(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        # the torrent never started: nothing on disk worth keeping
        await qbit.delete_torrents(str(widget.context["h"]), delete_files=True)
    except QbittorrentError:
        logger.exception("Picker cancel failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    await answer_json(query, {"r": widget.rev + 1, "m": "x"})
    await _picker_finish(bot, repo, widget, "x", views.PICKER_CANCELLED_CAPTION)


# --- progress -----------------------------------------------------------------


async def send_progress_widget(
    bot: Bot,
    chat_id: int,
    repo: TorrentRepo,
    info: dict[str, Any],
    fc: int,
    ft: int,
) -> None:
    """Send the live progress widget for a just-started torrent."""
    state = views.progress_state(info, fc, ft, rev=1, now=_now())
    context = {
        "h": str(info.get("hash") or ""),
        "n": str(info.get("name") or "?"),
        "st": views.progress_phase(info),
        "fc": fc,
        "ft": ft,
    }
    await send_widget(
        bot,
        chat_id,
        PROGRESS,
        state,
        views.progress_caption(state),
        repo=repo,
        kind=PROGRESS,
        context=context,
    )


def _file_counts(widget: WidgetMessage) -> tuple[int, int]:
    return int(widget.context.get("fc") or 0), int(widget.context.get("ft") or 0)


async def _progress_sync(
    bot: Bot,
    repo: TorrentRepo,
    widget: WidgetMessage,
    info: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    """Persist a real state change (pause/resume, done, error) as new chat_data.

    Background ticks never edit the message: a re-created widget starts from
    the last persisted state and refreshes itself.
    """
    phase = views.progress_phase(info)
    if not force and phase == widget.context.get("st"):
        return
    fc, ft = _file_counts(widget)
    state = views.progress_state(info, fc, ft, widget.rev + 1, _now())
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        state,
        repo=repo,
        context={**widget.context, "st": phase},
    )
    await _edit_caption(bot, widget, views.progress_caption(state))


async def _progress_gone(bot: Bot, repo: TorrentRepo, widget: WidgetMessage) -> None:
    if widget.context.get("st") == "gone":
        return
    state = views.progress_gone(
        str(widget.context["n"]), str(widget.context["h"]), widget.rev + 1, _now()
    )
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        state,
        repo=repo,
        context={**widget.context, "st": "gone"},
    )
    await _edit_caption(bot, widget, views.progress_caption(state))


@widgets_server_router.widget_query(Verb("bg", "rf"), Kind(PROGRESS))
async def progress_refresh(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    """One ``torrents/info`` call per tick; chat_data changes only on transitions."""
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        info = await qbit.torrent_info(str(widget.context["h"]))
    except QbittorrentError as error:
        logger.warning("Progress poll failed: %s", error)
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    if info is None:
        await answer_json(query, {"st": "gone"})
        await _progress_gone(bot, repo, widget)
        return
    fc, ft = _file_counts(widget)
    await answer_json(query, views.progress_snapshot(info, fc, ft))
    await _progress_sync(bot, repo, widget, info)


@widgets_server_router.widget_query(Verb("pa", "re", "fo", "rc"), Kind(PROGRESS))
async def progress_control(
    query: WidgetQuery,
    verb: str,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    torrent_hash = str(widget.context["h"])
    try:
        info = await qbit.torrent_info(torrent_hash)
        if info is None:
            await answer_error(query, views.GONE_TEXT)
            await _progress_gone(bot, repo, widget)
            return
        match verb:
            case "pa":
                await qbit.stop_torrents(torrent_hash)
            case "re":
                await qbit.start_torrents(torrent_hash)
            case "fo":
                await qbit.set_force_start(torrent_hash, not info.get("force_start"))
            case _:
                await qbit.recheck_torrents(torrent_hash)
                await qbit.start_torrents(torrent_hash)
        info = await qbit.torrent_info(torrent_hash) or info
    except QbittorrentError:
        logger.exception("Progress %s failed", verb)
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    fc, ft = _file_counts(widget)
    snapshot = views.progress_snapshot(info, fc, ft)
    await answer_json(query, {"r": widget.rev + 1, **snapshot})
    await _progress_sync(bot, repo, widget, info, force=True)


@widgets_server_router.widget_query(Verb("del"), Kind(PROGRESS))
async def progress_delete(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        await qbit.delete_torrents(
            str(widget.context["h"]), delete_files=args[:1] == ["1"]
        )
    except QbittorrentError:
        logger.exception("Progress delete failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    await answer_json(query, {"r": widget.rev + 1, "st": "gone"})
    await _progress_gone(bot, repo, widget)


@widgets_server_router.widget_query(Verb("con"), Kind(PROGRESS))
async def progress_console(
    query: WidgetQuery,
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    await answer_json(query, {"ok": 1})
    await send_console_widget(bot, widget.chat_id, repo, qbit)


# --- console ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsoleSnapshot:
    state: dict[str, Any]
    torrents: list[dict[str, Any]]
    caption: str


async def _console_snapshot(
    qbit: QbittorrentClient, tab: str, page: int, rev: int
) -> ConsoleSnapshot:
    torrents = await qbit.list_torrents()
    transfer = await qbit.transfer_info()
    free_space = await qbit.free_space()
    alt = await qbit.alt_speed_enabled()
    state = views.console_state(
        torrents, transfer, free_space, alt, tab, page, rev, _now()
    )
    return ConsoleSnapshot(state, torrents, views.console_caption(torrents, transfer))


def _console_context(widget: WidgetMessage, state: dict[str, Any]) -> dict[str, Any]:
    """The tab/page the stored state shows (unchanged when it is the error view)."""
    return {
        "tab": state.get("tab", widget.context.get("tab", "all")),
        "page": state.get("pg", widget.context.get("page", 0)),
    }


async def send_console_widget(
    bot: Bot, chat_id: int, repo: TorrentRepo, qbit: QbittorrentClient
) -> None:
    try:
        snapshot = await _console_snapshot(qbit, "all", 0, rev=1)
        state, caption = snapshot.state, snapshot.caption
    except QbittorrentError:
        logger.exception("Console snapshot failed")
        state, caption = views.console_unavailable(1, _now()), views.QBIT_DOWN_TEXT
    await send_widget(
        bot,
        chat_id,
        CONSOLE,
        state,
        caption,
        repo=repo,
        kind=CONSOLE,
        context={"tab": "all", "page": 0},
    )


@widgets_server_router.message(Command("dl"), WidgetUi())
async def console_command(
    message: Message, repo: TorrentRepo, qbit: QbittorrentClient | None, bot: Bot
) -> None:
    if qbit is None:
        await message.answer(padded(NOT_CONFIGURED_TEXT))
        return
    await send_console_widget(bot, message.chat.id, repo, qbit)


@widgets_server_router.widget_query(Verb("bg", "rf", "t", "pg"), Kind(CONSOLE))
async def console_view(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    """Snapshot verbs: ``bg`` ticks answer only, the others also persist the view."""
    tab = str(widget.context.get("tab", "all"))
    page = int(widget.context.get("page", 0))
    if verb == "t":
        tab, page = (args[0] if args and args[0] in views.TABS else "all"), 0
    elif verb == "pg":
        page = max(0, _arg_int(args))
    rev = widget.rev if verb == "bg" else widget.rev + 1
    state = views.console_unavailable(rev, _now())
    if qbit is not None:
        try:
            state = (await _console_snapshot(qbit, tab, page, rev)).state
        except QbittorrentError as error:
            logger.warning("Console snapshot failed: %s", error)
    await answer_json(query, state)
    if verb == "bg":
        return
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        state,
        repo=repo,
        context={"tab": state.get("tab", tab), "page": state.get("pg", page)},
    )


@widgets_server_router.widget_query(Verb("x"), Kind(CONSOLE))
async def console_details(
    query: WidgetQuery, args: list[str], qbit: QbittorrentClient | None
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        torrent = await _find_torrent(qbit, args)
    except QbittorrentError:
        logger.exception("Console details failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    if torrent is None:
        await answer_error(query, views.NOT_FOUND_TEXT)
        return
    await answer_json(query, views.console_details(torrent))


@widgets_server_router.widget_query(Verb("pa", "re", "fo", "pr"), Kind(CONSOLE))
async def console_control(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        torrent = await _find_torrent(qbit, args)
        if torrent is None:
            await answer_error(query, views.NOT_FOUND_TEXT)
            return
        torrent_hash = str(torrent["hash"])
        match verb:
            case "pa":
                await qbit.stop_torrents(torrent_hash)
            case "re":
                await qbit.start_torrents(torrent_hash)
            case "fo":
                await qbit.set_force_start(torrent_hash, not torrent.get("force_start"))
            case _:
                action = PRIORITY_ACTIONS.get(args[1] if len(args) > 1 else "")
                if action is None:
                    await answer_error(query, views.BAD_REQUEST_TEXT)
                    return
                await qbit.change_priority(action, torrent_hash)
        snapshot = await _console_snapshot(
            qbit,
            str(widget.context.get("tab", "all")),
            int(widget.context.get("page", 0)),
            widget.rev + 1,
        )
    except QueueingDisabledError:
        await answer_error(query, views.QUEUEING_OFF_TEXT)
        return
    except QbittorrentError:
        logger.exception("Console %s failed", verb)
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    fresh = next((t for t in snapshot.torrents if t.get("hash") == torrent_hash), None)
    await answer_json(query, views.console_patch(snapshot.state, fresh))
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        snapshot.state,
        repo=repo,
        context=_console_context(widget, snapshot.state),
    )


@widgets_server_router.widget_query(Verb("del", "alt", "sa", "ra"), Kind(CONSOLE))
async def console_bulk(
    query: WidgetQuery,
    verb: str,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    """Verbs answered with a full snapshot of the stored tab/page."""
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        match verb:
            case "del":
                torrent = await _find_torrent(qbit, args)
                if torrent is None:
                    await answer_error(query, views.NOT_FOUND_TEXT)
                    return
                await qbit.delete_torrents(
                    str(torrent["hash"]), delete_files=args[1:2] == ["1"]
                )
            case "alt":
                await qbit.toggle_alt_speed()
            case "sa":
                await qbit.stop_torrents("all")
            case _:
                await qbit.start_torrents("all")
        snapshot = await _console_snapshot(
            qbit,
            str(widget.context.get("tab", "all")),
            int(widget.context.get("page", 0)),
            widget.rev + 1,
        )
    except QbittorrentError:
        logger.exception("Console %s failed", verb)
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    await answer_json(query, snapshot.state)
    await edit_widget_state(
        bot,
        widget.chat_id,
        widget.message_id,
        snapshot.state,
        repo=repo,
        context=_console_context(widget, snapshot.state),
    )


@widgets_server_router.widget_query(Verb("fi"), Kind(CONSOLE))
async def console_files(
    query: WidgetQuery,
    args: list[str],
    widget: WidgetMessage,
    repo: TorrentRepo,
    qbit: QbittorrentClient | None,
    bot: Bot,
) -> None:
    """Open the file picker for a listed torrent as a new message."""
    if qbit is None:
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    try:
        torrent = await _find_torrent(qbit, args)
        prepared = (
            await picker_message(qbit, str(torrent["hash"]))
            if torrent is not None
            else None
        )
    except QbittorrentError:
        logger.exception("Console picker failed")
        await answer_error(query, views.QBIT_DOWN_TEXT)
        return
    if prepared is None:
        await answer_error(query, views.NOT_FOUND_TEXT)
        return
    await answer_json(query, {"ok": 1})
    state, caption, context = prepared
    await send_widget(
        bot,
        widget.chat_id,
        PICKER,
        state,
        caption,
        repo=repo,
        kind=PICKER,
        context=context,
    )
