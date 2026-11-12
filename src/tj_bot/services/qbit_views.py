"""State builders, answers and captions of the qBittorrent widgets.

Pure functions over qBittorrent API rows (``torrents/info``, ``torrents/files``,
``transfer/info``). The contracts are ``docs/widgets/<name>/PROTOCOL.md`` for
``picker``, ``progress`` and ``console``; every state is checked against the
1024-byte ``chat_data`` limit before it leaves this module.
"""

import datetime
import html
import math
import os.path
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tj_bot.services.formatting import STATE_VIEW
from tj_bot.widgets.state import StateTooLargeError, dumps_state

KIB = 1024
ETA_INFINITE = 8_640_000
NBSP = " "

QBIT_DOWN_TEXT = "qBittorrent недоступен"
GONE_TEXT = "Загрузка не найдена — возможно, удалена из qBittorrent"
NO_FILES_TEXT = "Выберите хотя бы один файл"
NOT_FOUND_TEXT = "Торрент не найден"
QUEUEING_OFF_TEXT = "Очередь отключена в qBittorrent"
BAD_REQUEST_TEXT = "Неверный запрос"

# --- Russian number formatting (the widgets format the same way) -------------

UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def size_ru(num_bytes: float) -> str:
    """«12,4 ГБ» with a no-break space before the unit."""
    value = float(num_bytes)
    unit = 0
    while value >= KIB and unit < len(UNITS) - 1:
        value /= KIB
        unit += 1
    if unit and value < 100:
        text = f"{value:.1f}".replace(".", ",").removesuffix(",0")
    else:
        text = str(round(value))
    return f"{text}{NBSP}{UNITS[unit]}"


def speed_ru(bytes_per_second: float) -> str:
    return f"{size_ru(bytes_per_second)}/с"


def eta_ru(seconds: int) -> str:
    if seconds < 0 or seconds >= ETA_INFINITE:
        return "—"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} м"
    return f"{max(minutes, 1)} м"


def plural(count: int, one: str, few: str, many: str) -> str:
    """The Russian word form for ``count``: 1 торрент, 2 торрента, 5 торрентов."""
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        return one
    if 2 <= tail <= 4 and not 12 <= hundred <= 14:
        return few
    return many


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- picker -------------------------------------------------------------------

PICKER_PAGE = 6
MAX_FOLDERS = 6
OTHER_FOLDERS = "Другие папки"
PICKER_NAME_LIMIT = 90
FILE_NAME_LIMIT = 28
FOLDER_NAME_LIMIT = 16
# tighter cuts when a page of long Cyrillic names overflows chat_data
COMPACT_FILE_NAME_LIMIT = 20
COMPACT_FOLDER_NAME_LIMIT = 12
MIN_COMMON_PREFIX = 6
FILE_CLASSES = {
    "v": frozenset({"mkv", "mp4", "avi", "m2ts", "ts", "mov", "wmv"}),
    "a": frozenset({"flac", "mp3", "aac", "ac3", "dts", "m4a", "wav", "ape", "mka"}),
    "s": frozenset({"srt", "ass", "sub", "idx", "sup"}),
    "r": frozenset({"rar", "zip", "7z", "iso"}),
}
CLASS_ORDER = "vasro"
PICKER_CANCELLED_CAPTION = "❌ Удалено из qBittorrent"


def file_class(name: str) -> str:
    """``v`` video, ``a`` audio, ``s`` subtitles, ``r`` archive, ``o`` other."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return next((cls for cls, exts in FILE_CLASSES.items() if ext in exts), "o")


def strip_common_prefix(names: list[str]) -> list[str]:
    """Drop a shared head («House.of.the.Dragon.»): the episode token stays visible."""
    if len(names) < 3:
        return names
    prefix = os.path.commonprefix(names)
    if len(prefix) < MIN_COMMON_PREFIX:
        return names
    head = prefix.rfind(".") + 1
    stripped = [name[head:] for name in names]
    return stripped if all(stripped) else names


@dataclass(frozen=True, slots=True)
class PickerFile:
    index: int  # qBittorrent file index (the ``filePrio`` id)
    name: str  # basename with the folder's common prefix stripped, uncut
    size_kb: int
    cls: str
    selected: bool
    folder: int  # index into ``PickerView.folders``, -1 at the root

    def row(self, name_limit: int) -> list[Any]:
        return [
            self.index,
            cut(self.name, name_limit),
            self.size_kb,
            self.cls,
            int(self.selected),
            self.folder,
        ]


@dataclass(frozen=True, slots=True)
class PickerView:
    """A torrent's files grouped for paging: root files first, then folder by folder."""

    files: list[PickerFile]
    folders: list[str]

    @property
    def selected_count(self) -> int:
        return sum(1 for f in self.files if f.selected)

    @property
    def total_kb(self) -> int:
        return sum(f.size_kb for f in self.files)

    @property
    def selected_kb(self) -> int:
        return sum(f.size_kb for f in self.files if f.selected)

    @property
    def pages(self) -> int:
        return max(1, math.ceil(len(self.files) / PICKER_PAGE))

    def clamp(self, page: int) -> int:
        return max(0, min(page, self.pages - 1))

    def page(self, page: int) -> list[PickerFile]:
        return self.files[page * PICKER_PAGE : (page + 1) * PICKER_PAGE]

    def page_of(self, index: int) -> int:
        """The page showing the file with qBittorrent index ``index`` (0 if unknown)."""
        position = next((i for i, f in enumerate(self.files) if f.index == index), 0)
        return position // PICKER_PAGE

    def by_index(self, index: int) -> PickerFile | None:
        return next((f for f in self.files if f.index == index), None)

    def in_folder(self, folder: int) -> list[PickerFile]:
        return [f for f in self.files if f.folder == folder]

    def in_class(self, cls: str) -> list[PickerFile]:
        return [f for f in self.files if f.cls == cls]


def picker_view(files: list[dict[str, Any]]) -> PickerView:
    """Group ``torrents/files`` rows: root stripped, ≤ 6 folders, prefixes cut."""
    paths = [str(f.get("name") or "?").split("/") for f in files]
    # the root folder shared by every file is the torrent name: no information
    if paths and all(len(p) > 1 for p in paths) and len({p[0] for p in paths}) == 1:
        paths = [p[1:] for p in paths]
    folder_names: list[str] = []
    groups: dict[str, list[int]] = {"": []}
    for position, path in enumerate(paths):
        folder = path[0] if len(path) > 1 else ""
        if folder and folder not in groups:
            folder_names.append(folder)
        groups.setdefault(folder, []).append(position)
    overflow = len(folder_names) > MAX_FOLDERS
    folders = folder_names
    if overflow:
        folders = [*folder_names[: MAX_FOLDERS - 1], OTHER_FOLDERS]
    folder_index = {
        name: min(i, MAX_FOLDERS - 1) if overflow else i
        for i, name in enumerate(folder_names)
    }
    picker_files: list[PickerFile] = []
    for folder, positions in groups.items():
        names = strip_common_prefix([paths[pos][-1] for pos in positions])
        for position, name in zip(positions, names, strict=True):
            raw = files[position]
            picker_files.append(
                PickerFile(
                    index=int(raw.get("index", position)),
                    name=name,
                    size_kb=int(raw.get("size") or 0) // KIB,
                    cls=file_class(paths[position][-1]),
                    selected=int(raw.get("priority") or 0) > 0,
                    folder=folder_index[folder] if folder else -1,
                )
            )
    return PickerView(files=picker_files, folders=folders)


def toggle_priority(files: list[PickerFile]) -> int:
    """All-or-nothing toggle: 0 (skip) when every file is selected, else 1."""
    return 0 if all(f.selected for f in files) else 1


def selected_counts(files: list[dict[str, Any]]) -> tuple[int, int]:
    """``(selected, total)`` file counts of raw ``torrents/files`` rows."""
    return sum(1 for f in files if int(f.get("priority") or 0) > 0), len(files)


def _classes(view: PickerView) -> list[list[Any]]:
    classes = []
    for cls in CLASS_ORDER:
        files = view.in_class(cls)
        if files:
            classes.append([cls, len(files), sum(1 for f in files if f.selected)])
    return classes


def _folders(view: PickerView, name_limit: int) -> list[list[Any]]:
    return [
        [cut(name, name_limit), len(files), sum(1 for f in files if f.selected)]
        for i, name in enumerate(view.folders)
        if (files := view.in_folder(i))
    ]


def _fit(build: Callable[[int, int], dict[str, Any]]) -> dict[str, Any]:
    """The payload with the standard name cuts, or the compact ones on overflow."""
    payload = build(FILE_NAME_LIMIT, FOLDER_NAME_LIMIT)
    try:
        dumps_state(payload)
    except StateTooLargeError:
        payload = build(COMPACT_FILE_NAME_LIMIT, COMPACT_FOLDER_NAME_LIMIT)
        dumps_state(payload)
    return payload


def picker_state(
    name: str, view: PickerView, free_space: int, page: int, rev: int
) -> dict[str, Any]:
    """Full picker ``chat_data`` (mode ``pick``) for ``page``."""
    page = view.clamp(page)

    def build(file_limit: int, folder_limit: int) -> dict[str, Any]:
        return {
            "v": 1,
            "r": rev,
            "m": "pick",
            "n": cut(name, PICKER_NAME_LIMIT),
            "c": len(view.files),
            "s": view.total_kb,
            "k": view.selected_count,
            "z": view.selected_kb,
            "fs": free_space // KIB if free_space >= 0 else -1,
            "p": page,
            "q": view.pages,
            "cl": _classes(view),
            "d": _folders(view, folder_limit),
            "f": [f.row(file_limit) for f in view.page(page)],
        }

    return _fit(build)


def picker_fragment(view: PickerView, page: int, rev: int) -> dict[str, Any]:
    """The answer to a selection verb: counts, chips and the current page."""
    page = view.clamp(page)

    def build(file_limit: int, folder_limit: int) -> dict[str, Any]:
        return {
            "r": rev,
            "p": page,
            "k": view.selected_count,
            "z": view.selected_kb,
            "cl": _classes(view),
            "d": _folders(view, folder_limit),
            "f": [f.row(file_limit) for f in view.page(page)],
        }

    return _fit(build)


def picker_page(view: PickerView, page: int, rev: int) -> dict[str, Any]:
    """The answer to ``pg:<n>``: the rows of page ``n``."""
    page = view.clamp(page)
    return _fit(
        lambda file_limit, _: {
            "r": rev,
            "p": page,
            "f": [f.row(file_limit) for f in view.page(page)],
        }
    )


def picker_terminal(name: str, mode: str, rev: int) -> dict[str, Any]:
    """The compact final view: ``go`` started, ``x`` cancelled, ``gone`` vanished."""
    state = {"v": 1, "r": rev, "m": mode, "n": cut(name, PICKER_NAME_LIMIT)}
    dumps_state(state)
    return state


def picker_caption(name: str, view: PickerView) -> str:
    return (
        f"📤 <b>{html.escape(name)}</b> — выберите файлы · "
        f"{view.selected_count} из {len(view.files)}, "
        f"{size_ru(view.selected_kb * KIB)} из {size_ru(view.total_kb * KIB)}"
    )


def picker_started_caption(name: str) -> str:
    return f"⬇️ Запущено: <b>{html.escape(name)}</b>"


# --- progress -----------------------------------------------------------------

PROGRESS_NAME_LIMIT = 120
PAUSED_STATES = frozenset({"stoppedDL", "pausedDL"})
STOPPED_UP_STATES = frozenset({"stoppedUP", "pausedUP"})
ERROR_STATES = frozenset({"error", "missingFiles"})
FORCED_STATES = frozenset({"forcedDL", "forcedUP"})
SEEDING_STATES = frozenset(
    {"uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP"}
)


def progress_snapshot(info: dict[str, Any], fc: int, ft: int) -> dict[str, Any]:
    """The live part of the progress state (an answer to ``bg``/``rf``)."""
    state = str(info.get("state") or "")
    eta = int(info.get("eta") or 0)
    return {
        "h": str(info.get("hash") or "")[:8],
        "n": cut(str(info.get("name") or "?"), PROGRESS_NAME_LIMIT),
        "st": state,
        "pr": round(float(info.get("progress") or 0), 3),
        "sz": int(info.get("size") or 0),
        "dn": int(info.get("downloaded") or 0),
        "ds": int(info.get("dlspeed") or 0),
        "us": int(info.get("upspeed") or 0),
        "eta": eta if 0 <= eta < ETA_INFINITE else -1,
        "se": int(info.get("num_seeds") or 0),
        "pe": int(info.get("num_leechs") or 0),
        "ra": round(float(info.get("ratio") or 0), 2),
        "fc": fc,
        "ft": ft,
        "ad": int(info.get("added_on") or 0),
        "fo": int(bool(info.get("force_start")) or state in FORCED_STATES),
    }


def progress_state(
    info: dict[str, Any], fc: int, ft: int, rev: int, now: int
) -> dict[str, Any]:
    state = {"v": 1, "r": rev, "t": now, **progress_snapshot(info, fc, ft)}
    dumps_state(state)
    return state


def progress_gone(name: str, torrent_hash: str, rev: int, now: int) -> dict[str, Any]:
    state = {
        "v": 1,
        "r": rev,
        "t": now,
        "h": torrent_hash[:8],
        "n": cut(name, PROGRESS_NAME_LIMIT),
        "st": "gone",
    }
    dumps_state(state)
    return state


def progress_phase(info: dict[str, Any]) -> str:
    """The coarse state the bot persists: chat_data is edited only when it changes."""
    state = str(info.get("state") or "")
    if state in ERROR_STATES:
        return "error"
    if state in PAUSED_STATES:
        return "paused"
    if state in STOPPED_UP_STATES:
        return "stopped"
    if float(info.get("progress") or 0) >= 1 or state in SEEDING_STATES:
        return "done"
    return "active"


def progress_caption(snapshot: dict[str, Any]) -> str:
    """Fallback caption for a progress state or snapshot (``n``, ``st`` required)."""
    name = html.escape(str(snapshot["n"]))
    state = str(snapshot["st"])
    if state == "gone":
        return f"🗑 Удалено из qBittorrent: <b>{name}</b>"
    progress = float(snapshot.get("pr") or 0)
    if progress >= 1:
        return f"✅ Загрузка завершена: <b>{name}</b>"
    if state in ERROR_STATES:
        return f"❌ <b>{name}</b> — {STATE_VIEW[state][1]}"
    percent = f"{progress * 100:.1f}".replace(".", ",")
    if state in PAUSED_STATES:
        return f"⏸ <b>{name}</b> — {percent} %, на паузе"
    return (
        f"⬇️ <b>{name}</b> — {percent} %, {speed_ru(int(snapshot.get('ds') or 0))}, "
        f"осталось {eta_ru(int(snapshot.get('eta', -1)))}"
    )


# --- console ------------------------------------------------------------------

CONSOLE_PAGE = 6
CONSOLE_NAME_LIMIT = 36
COMPACT_CONSOLE_NAME_LIMIT = 30
SAVE_PATH_LIMIT = 60
TABS = ("all", "dl", "up", "st", "er")
STATE_CODES = {
    "downloading": "d",
    "forcedDL": "df",
    "stalledDL": "ds",
    "queuedDL": "dq",
    "checkingDL": "dk",
    "metaDL": "dm",
    "allocating": "dk",
    "uploading": "u",
    "forcedUP": "uf",
    "stalledUP": "us",
    "queuedUP": "uq",
    "checkingUP": "uk",
    "moving": "uk",
    "stoppedDL": "p",
    "pausedDL": "p",
    "stoppedUP": "c",
    "pausedUP": "c",
    "error": "e",
    "missingFiles": "ex",
}
TAB_OF_KIND = {"d": "dl", "u": "up", "p": "st", "c": "st", "e": "er"}
CONNECTED = frozenset({"connected", "firewalled"})
CONSOLE_EMPTY_CAPTION = "qBittorrent · нет задач"


def state_code(state: str) -> str:
    """The widget's 1-2 char state code; unknown states draw as errors."""
    return STATE_CODES.get(state, "e")


def tab_of(code: str) -> str:
    return TAB_OF_KIND[code[0]]


def console_row(
    torrent: dict[str, Any], name_limit: int = CONSOLE_NAME_LIMIT
) -> list[Any]:
    """Positional row ``[h, n, s, p, v, e, ra]``; ``v`` is the speed that matters."""
    code = state_code(str(torrent.get("state") or ""))
    speed = torrent.get("dlspeed" if code[0] == "d" else "upspeed") or 0
    eta = int(torrent.get("eta") or 0)
    return [
        str(torrent.get("hash") or "")[:8],
        cut(str(torrent.get("name") or "?"), name_limit),
        code,
        round(float(torrent.get("progress") or 0), 3),
        int(speed),
        eta if 0 <= eta < ETA_INFINITE else -1,
        round(float(torrent.get("ratio") or 0), 2),
    ]


def console_counts(torrents: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(TABS, 0)
    counts["all"] = len(torrents)
    for torrent in torrents:
        counts[tab_of(state_code(str(torrent.get("state") or "")))] += 1
    return counts


def console_transfer(
    transfer: dict[str, Any], free_space: int, alt: bool
) -> dict[str, int]:
    """``tr``: speeds, session totals, disk (total unknown → bar hidden), alt, link."""
    return {
        "d": int(transfer.get("dl_info_speed") or 0),
        "u": int(transfer.get("up_info_speed") or 0),
        "dd": int(transfer.get("dl_info_data") or 0),
        "ud": int(transfer.get("up_info_data") or 0),
        "fs": max(free_space, 0),
        "ft": 0,
        "alt": int(alt),
        "cs": int(str(transfer.get("connection_status") or "") in CONNECTED),
    }


def console_state(
    torrents: list[dict[str, Any]],
    transfer: dict[str, Any],
    free_space: int,
    alt: bool,
    tab: str,
    page: int,
    rev: int,
    now: int,
) -> dict[str, Any]:
    """Full console ``chat_data`` (also the snapshot answer) for ``tab``/``page``."""
    tab = tab if tab in TABS else "all"
    shown = [
        t
        for t in torrents
        if tab == "all" or tab_of(state_code(str(t.get("state") or ""))) == tab
    ]
    pages = max(1, math.ceil(len(shown) / CONSOLE_PAGE))
    page = max(0, min(page, pages - 1))
    window = shown[page * CONSOLE_PAGE : (page + 1) * CONSOLE_PAGE]

    def build(name_limit: int) -> dict[str, Any]:
        return {
            "v": 1,
            "r": rev,
            "t": now,
            "tab": tab,
            "pg": page,
            "np": pages,
            "cnt": console_counts(torrents),
            "tr": console_transfer(transfer, free_space, alt),
            "rows": [console_row(t, name_limit) for t in window],
        }

    state = build(CONSOLE_NAME_LIMIT)
    try:
        dumps_state(state)
    except StateTooLargeError:
        state = build(COMPACT_CONSOLE_NAME_LIMIT)
        dumps_state(state)
    return state


def console_unavailable(rev: int, now: int) -> dict[str, Any]:
    return {"v": 1, "r": rev, "t": now, "err": QBIT_DOWN_TEXT}


def console_details(torrent: dict[str, Any]) -> dict[str, Any]:
    """The ``x:<h>`` answer: a preformatted details line and the save path."""
    state = str(torrent.get("state") or "")
    added = datetime.datetime.fromtimestamp(
        int(torrent.get("added_on") or 0), tz=datetime.UTC
    ).astimezone()
    line = " · ".join(
        [
            STATE_VIEW.get(state, ("", state))[1],
            size_ru(int(torrent.get("size") or 0)),
            f"↓ {size_ru(int(torrent.get('downloaded') or 0))}",
            f"↑ {size_ru(int(torrent.get('uploaded') or 0))}",
            f"×{float(torrent.get('ratio') or 0):.2f}".replace(".", ","),
            f"сиды {int(torrent.get('num_seeds') or 0)} "
            f"({int(torrent.get('num_complete') or 0)})",
            f"пиры {int(torrent.get('num_leechs') or 0)} "
            f"({int(torrent.get('num_incomplete') or 0)})",
            str(torrent.get("category") or "—"),
            added.strftime("%d.%m.%Y"),
        ]
    )
    return {
        "x": {
            "h": str(torrent.get("hash") or "")[:8],
            "l": line,
            "sp": cut(str(torrent.get("save_path") or ""), SAVE_PATH_LIMIT),
        }
    }


def console_patch(
    state: dict[str, Any], torrent: dict[str, Any] | None
) -> dict[str, Any]:
    """The answer to a per-torrent action: fresh counts, transfer, row and details."""
    patch = {
        "r": state["r"],
        "t": state["t"],
        "cnt": state["cnt"],
        "tr": state["tr"],
    }
    if torrent is not None:
        patch["row"] = console_row(torrent)
        patch |= console_details(torrent)
    dumps_state(patch)
    return patch


def console_caption(torrents: list[dict[str, Any]], transfer: dict[str, Any]) -> str:
    if not torrents:
        return CONSOLE_EMPTY_CAPTION
    total = len(torrents)
    downloading = console_counts(torrents)["dl"]
    return (
        f"qBittorrent · ↓ {speed_ru(int(transfer.get('dl_info_speed') or 0))} · "
        f"↑ {speed_ru(int(transfer.get('up_info_speed') or 0))} · "
        f"{total} {plural(total, 'торрент', 'торрента', 'торрентов')} "
        f"({downloading} "
        f"{plural(downloading, 'загружается', 'загружаются', 'загружаются')})"
    )
