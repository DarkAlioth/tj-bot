"""Pure builders of the search widgets' ``chat_data``, captions and answers.

The bot formats, the widget lays out (``docs/widgets/search/PROTOCOL.md``):
every human-readable string is produced here and the widgets only escape
and place it. Nothing in this module touches the network or the database.
"""

import datetime
import html
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tj_bot.db.filters import (
    ALL_CATEGORIES,
    DEFAULT_FILTER,
    DEFAULT_SORT,
    SEEDERS_LABELS,
    SIZE_LABELS,
    SORT_LABELS,
    ResultFilters,
    category_token,
)
from tj_bot.db.models import Torrent
from tj_bot.widgets.state import dumps_state
from tj_bot.services.enrichment import CardMeta
from tj_bot.widgets.state import MAX_STATE_BYTES

THIN_SPACE = " "
# search PROTOCOL §1: least valuable first; dropping the teaser keeps the
# full plot reachable through ``more``
DROP_ORDER = ("pl", "pp", "aa", "ml", "dr", "u", "te")
TEASER_CHARS = 80
MORE_PLOT_CHARS = 450
# answerWidgetQuery carries at most 1024 bytes; the plot wins over the cast
MORE_ANSWER_BYTES = 1000
MORE_PEOPLE_CHARS = 100
LIST_TITLE_CHARS = 36
LIST_CATEGORIES = 9  # plus «Все»: the sheet holds 10 chips
CATEGORY_LABEL_CHARS = 18
DONE_CHARS = 80

# Jackett tracker ids and display names, normalised to [a-z0-9]
TRACKER_NAMES = {
    "rutracker": "RuTracker",
    "rutor": "RuTor",
    "megapeer": "MegaPeer",
    "noname": "NoNaMe",
    "nnm": "NoNaMe",
    "tapochek": "Tapochek",
    "pctorrent": "PC-torrent",
    "byrutor": "Byrutor",
}
# Torznab category prefix -> (label, backdrop hue); «other» keeps the
# widget's default hue
CATEGORY_VIEWS = {
    "Movies": ("Фильмы", 262),
    "TV": ("Сериалы", 178),
    "PC/Games": ("Игры", 36),
    "Console": ("Игры", 36),
    "Audio": ("Музыка", 322),
    "Books": ("Книги", 82),
}
CATEGORY_SUFFIXES = {"HD": " HD", "UHD": " 4K"}
DATE_RANGE_LABELS = ("", "за неделю", "за месяц", "за год")
CYRILLIC = re.compile("[А-Яа-яЁё]")
TITLE_YEAR = re.compile(r"[(\[]((?:19|20)\d{2})[)\]]")
# BRIEF §4: one chip per group, in this order
CHIP_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(2160p|1080p|720p)(?!\w)",
        r"\b(Dolby Vision|HDR10\+?|HDR)(?!\w)",
        r"\b(BDRemux|BDRip|WEB-DL|WEBRip|HDTV)(?!\w)",
        r"\b(x265|HEVC|AV1|H\.264)(?!\w)",
        r"\b(S\d{1,2}E\d{1,2}(?:-\d{1,2})?)(?!\w)",
        r"\b(Dub|MVO|Sub)(?!\w)",
    )
)


@dataclass(frozen=True, slots=True)
class SearchContext:
    """Where a card, list page or filter sheet sits inside a cached search."""

    qh: str  # full query hash; the widgets see the first 8 characters
    index: int  # card index, or the first index of a list page
    total: int  # results after filters
    sort: str = DEFAULT_SORT
    filter_code: str = DEFAULT_FILTER
    category: str = ALL_CATEGORIES  # token or ALL
    from_cache: bool = False
    is_admin: bool = False

    @property
    def filters_active(self) -> bool:
        return self.filter_code != DEFAULT_FILTER or self.category != ALL_CATEGORIES

    def envelope(self) -> dict[str, Any]:
        """The sort/filter keys every search widget echoes in ``ctx``."""
        return {
            "qh": self.qh[:8],
            "s": self.sort,
            "sl": SORT_LABELS.get(self.sort, SORT_LABELS[DEFAULT_SORT]),
            "f": self.filter_code,
            "c": self.category,
        }


def plural(count: int, one: str, few: str, many: str) -> str:
    tail = abs(count) % 100
    if 11 <= tail <= 19:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def cut(text: str, limit: int) -> str:
    """Truncate to ``limit`` characters with «…», preferring a word boundary."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip(" ,.;:–-") + "…"


def fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", THIN_SPACE)


def fmt_size(num_bytes: int) -> str:
    """«45 ГБ», «8,2 ГБ», «912 МБ» — one decimal below 100, comma separated."""
    value = float(num_bytes)
    unit = "Б"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            break
        value /= 1024
    if unit == "Б":
        return f"{int(value)} Б"
    text = f"{value:.0f}" if value >= 100 else f"{value:.1f}".removesuffix(".0")
    return f"{text.replace('.', ',')} {unit}"


def fmt_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} мин"
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def relative_day(published: datetime.date, today: datetime.date) -> str | None:
    """«сегодня», «вчера», «9 дн. назад» within 30 days, else None."""
    days = (today - published).days
    if days < 0 or days > 30:
        return None
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def tracker_name(tracker: str) -> str:
    """Display name for a Jackett tracker id or name («rutracker» → RuTracker)."""
    raw = html.unescape(tracker)
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    for prefix, name in TRACKER_NAMES.items():
        if key.startswith(prefix):
            return name
    return cut(raw, 14)


def category_view(category: str) -> tuple[str, int | None]:
    """Russian label and backdrop hue of a Torznab category («Movies/HD»)."""
    raw = html.unescape(category)
    for prefix, (label, hue) in CATEGORY_VIEWS.items():
        if raw == prefix or raw.startswith(prefix + "/"):
            suffix = raw[len(prefix) + 1 :]
            return label + CATEGORY_SUFFIXES.get(suffix, ""), hue
    return raw, None


def category_label(category: str) -> str:
    return cut(category_view(category)[0], CATEGORY_LABEL_CHARS)


def split_title(title: str) -> tuple[str | None, str | None]:
    """Russian and English parts of «Дюна / Dune (2024) …»; Nones without « / »."""
    if " / " not in title:
        return None, None
    first, rest = title.split(" / ", 1)
    second = re.split(r" [(\[]", rest, maxsplit=1)[0].strip()
    first = first.strip()
    if not CYRILLIC.search(first) and CYRILLIC.search(second):
        first, second = second, first
    return first, second or None


def title_year(title: str) -> int | None:
    match = TITLE_YEAR.search(title)
    return int(match.group(1)) if match else None


def quality_chips(title: str) -> list[str]:
    chips = []
    for pattern in CHIP_PATTERNS:
        match = pattern.search(title)
        if match:
            chips.append(match.group(1))
    return chips


def state_size(state: dict[str, Any]) -> int:
    return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())


def pack(state: dict[str, Any]) -> dict[str, Any]:
    """Drop optional card keys in ``DROP_ORDER`` until the state fits."""
    for key in DROP_ORDER:
        if state_size(state) <= MAX_STATE_BYTES:
            break
        if state.pop(key, None) is not None and key == "pl":
            state["pm"] = 1
    return state


def title_fields(title: str, meta: CardMeta) -> dict[str, str]:
    """``tr``/``te`` from the enrichment or the title split, else ``ti``."""
    split_ru, split_en = split_title(title)
    russian = meta.title_ru or split_ru
    english = meta.title_en or split_en
    if not russian:
        return {"ti": cut(title, 120)}
    fields = {"tr": cut(russian, 60)}
    if english:
        fields["te"] = cut(english, 60)
    return fields


def meta_line(meta: CardMeta) -> str | None:
    parts = []
    if meta.genres:
        parts.append(", ".join(meta.genres[:3]))
    if meta.countries:
        parts.append(", ".join(meta.countries[:2]))
    if meta.duration_min:
        parts.append(fmt_duration(meta.duration_min))
    return cut(" · ".join(parts), 60) if parts else None


def people_line(meta: CardMeta, limit: int, cast_size: int) -> str | None:
    parts = []
    if meta.director:
        parts.append(f"Реж. {meta.director}")
    if meta.cast:
        parts.append(", ".join(meta.cast[:cast_size]))
    return cut(" · ".join(parts), limit) if parts else None


def audio_line(meta: CardMeta) -> str | None:
    parts = []
    if meta.translation:
        parts.append(", ".join(meta.translation))
    if meta.subtitles:
        parts.append("Субтитры: " + ", ".join(meta.subtitles))
    return cut(" · ".join(parts), 50) if parts else None


def search_state(
    torrent: Torrent,
    ctx: SearchContext,
    meta: CardMeta,
    *,
    rev: int,
    is_fav: bool,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """The ``search`` card's chat_data (search PROTOCOL §1), packed to fit."""
    today = today or datetime.date.today()
    title = html.unescape(torrent.title)
    label, hue = category_view(torrent.category)
    state: dict[str, Any] = {"v": 1, "r": rev, **ctx.envelope()}
    state.update(
        i=ctx.index,
        n=ctx.total,
        fa=int(ctx.filters_active),
        fc=int(ctx.from_cache),
        adm=int(ctx.is_admin),
        h=torrent.hash[:8],
    )
    state.update(title_fields(title, meta))
    year = meta.year or title_year(title)
    if year:
        state["y"] = year
    chips = list(meta.chips) or quality_chips(title)
    if chips:
        state["q"] = [cut(chip, 12) for chip in chips[:5]]
    if label:
        state["cn"] = cut(label, 14)
    state.update(
        tk=tracker_name(torrent.tracker),
        sd=torrent.seeders,
        pr=torrent.peers,
        sz=fmt_size(torrent.size),
        dt=torrent.published_at.strftime("%d.%m.%Y"),
    )
    relative = relative_day(torrent.published_at, today)
    if relative:
        state["dr"] = relative
    if torrent.uploader:
        state["up"] = cut(html.unescape(torrent.uploader), 16)
    lines = (
        ("ml", meta_line(meta)),
        ("pp", people_line(meta, 60, 2)),
        ("aa", audio_line(meta)),
    )
    for key, value in lines:
        if value:
            state[key] = value
    plot = card_plot(meta, torrent)
    if plot:
        state["pl"] = cut(plot, TEASER_CHARS)
        if len(plot) > TEASER_CHARS:
            state["pm"] = 1
    url = html.unescape(torrent.details_url)
    if len(url) <= 64:
        state["u"] = url
    if is_fav:
        state["fav"] = 1
    if meta.pending:
        state["en"] = 1
    return pack(state)


def card_plot(meta: CardMeta, torrent: Torrent) -> str | None:
    """The tracker page's plot, else the description Jackett gave (stored escaped)."""
    if meta.plot:
        return meta.plot
    description = html.unescape(torrent.description or "").strip()
    return description or None


def more_answer(meta: CardMeta, torrent: Torrent) -> dict[str, str]:
    """The ``more:<h>`` answer: the full plot and the wider cast."""
    answer = {}
    plot = card_plot(meta, torrent)
    if plot:
        answer["pl"] = cut(plot, MORE_PLOT_CHARS)
    people = people_line(meta, MORE_PEOPLE_CHARS, 4)
    if people:
        answer["pp"] = people
    if not answer:
        return {"err": "Описание недоступно"}
    if len(dumps_state(answer).encode("utf-8")) > MORE_ANSWER_BYTES:
        answer.pop("pp", None)
    return answer


def list_row(torrent: Torrent) -> list[str]:
    title = html.unescape(torrent.title)
    russian, _ = split_title(title)
    meta = "|".join(
        (
            fmt_size(torrent.size),
            torrent.published_at.strftime("%d.%m.%y"),
            fmt_int(torrent.seeders),
            tracker_name(torrent.tracker),
        )
    )
    return [cut(russian or title, LIST_TITLE_CHARS), meta]


def _cut_titles(rows: list[list[str]], limit: int) -> None:
    for row in rows:
        row[0] = cut(row[0], limit)


def _drop_trackers(rows: list[list[str]]) -> None:
    for row in rows:
        row[1] = "|".join(row[1].split("|")[:3])


def pack_list(state: dict[str, Any]) -> dict[str, Any]:
    """Shrink strings, never rows (search-list PROTOCOL §1)."""
    rows: list[list[str]] = state["rows"]
    steps: tuple[Callable[[], object], ...] = (
        lambda: state.pop("q", None),
        lambda: _cut_titles(rows, 28),
        lambda: _drop_trackers(rows),
        lambda: _cut_titles(rows, 20),
    )
    for step in steps:
        if state_size(state) <= MAX_STATE_BYTES:
            break
        step()
    return state


def list_state(
    torrents: Sequence[Torrent],
    ctx: SearchContext,
    *,
    rev: int,
    query_text: str | None,
) -> dict[str, Any]:
    """The ``search-list`` page's chat_data; ``ctx.index`` is the first row."""
    state: dict[str, Any] = {"v": 1, "r": rev, "qh": ctx.qh[:8]}
    if query_text:
        state["q"] = cut(query_text, 24)
    state.update(ctx.envelope())
    state.update(i0=ctx.index, n=ctx.total)
    if ctx.filters_active:
        state["fa"] = 1
    state["rows"] = [list_row(torrent) for torrent in torrents]
    return pack_list(state)


def list_caption(state: dict[str, Any], query_text: str | None) -> str:
    first = int(state["i0"]) + 1
    last = int(state["i0"]) + len(state["rows"])
    text = f"Результаты {first}–{last} из {state['n']}"
    if query_text:
        text += f" по запросу «{query_text}»"
    return html.escape(f"{text} · {state['sl']}")


def done_state(qh: str, rev: int, text: str, **context_line: str) -> dict[str, Any]:
    """A list or sheet reduced to one «✓ …» line (their PROTOCOLs §3)."""
    state: dict[str, Any] = {"v": 1, "r": rev, "qh": qh[:8], **context_line}
    state["done"] = cut(text, DONE_CHARS)
    return state


def filters_state(
    ctx: SearchContext, *, rev: int, categories: Sequence[tuple[str, int]]
) -> dict[str, Any]:
    """The ``search-filters`` sheet's chat_data; ``ctx.total`` is unfiltered."""
    state: dict[str, Any] = {
        "v": 1,
        "r": rev,
        "qh": ctx.qh[:8],
        "i": ctx.index,
        "s": ctx.sort,
        "f": ctx.filter_code,
        "c": ctx.category,
        "n": ctx.total,
    }
    if len(categories) > 1:
        largest = sorted(categories, key=lambda item: -item[1])[:LIST_CATEGORIES]
        state["cats"] = [
            ["ALL", "Все", ctx.total],
            *(
                [category_token(name), category_label(name), count]
                for name, count in largest
            ),
        ]
    return state


def filters_summary(filters: ResultFilters, category: str | None) -> str | None:
    """«сиды 10+ · 1–5 ГБ · за месяц · Фильмы HD», None when nothing is set."""
    parts = []
    if filters.seeders:
        parts.append(f"сиды {SEEDERS_LABELS[filters.seeders]}")
    if filters.size:
        parts.append(SIZE_LABELS[filters.size].replace("GB", "ГБ"))
    if filters.date:
        parts.append(DATE_RANGE_LABELS[filters.date])
    if category:
        parts.append(category_label(category))
    return " · ".join(parts) if parts else None


def filters_done_text(filters: ResultFilters, category: str | None, total: int) -> str:
    summary = filters_summary(filters, category) or "фильтры сброшены"
    results = plural(total, "результат", "результата", "результатов")
    return f"Применено: {summary} · {total} {results}"


def filters_caption(
    query_text: str | None, filters: ResultFilters, category: str | None
) -> str:
    summary = filters_summary(filters, category) or "не заданы"
    return html.escape(f"Фильтры поиска «{query_text or ''}» · Активно: {summary}")
