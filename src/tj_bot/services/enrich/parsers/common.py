"""Helpers shared by the tracker parsers.

Two families of pages exist: forum posts (phpBB on RuTracker, NNM-Club,
Tapochek, and RuTor's free-form description cell), where a label's value may
continue on the following lines, handled by ``fields_of``/``apply_fields``;
and structured pages (MegaPeer, PC-torrent, Byrutor) with one ``Label: value``
per line, handled by ``labeled``/``apply_field``/``finalize_av``.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html
from lxml.html import HtmlElement

from tj_bot.services.enrich.model import TorrentMeta
from tj_bot.services.enrich.title_parser import parse_title

MAX_PLOT = 1000
MAX_CAST = 8
MAX_SCREENSHOTS = 6
MAX_LIST = 12
MAX_TRANSLATIONS = 6
BLOCK_TAGS = frozenset(
    {
        "br",
        "div",
        "p",
        "li",
        "ul",
        "ol",
        "hr",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
        "pre",
        "details",
        "summary",
    }
)
SKIP_TAGS = frozenset({"script", "style", "textarea", "template"})
IMG_MARK = "⁣"  # invisible separator standing in for an inline image (flags)

_WS = re.compile(r"\s+")
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_AGE = re.compile(r"\b(\d{1,2})\s*(?:\+|лет)")
_HAS_WORD = re.compile(r"\w")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
_DURATION_HMS = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_DURATION_WORDS = re.compile(r"(?:(\d+)\s*ч)?\s*(?:(\d+)\s*мин)?")
_IMDB = re.compile(
    r"imdb\.com/title/(tt\d+)|/imdb/pic/(\d+)\.gif|data-title=\"(tt\d+)\""
    r"|/(tt\d{5,})\.(?:png|gif)|imdb\.desol\.one/(tt\d{5,})"
)
_BADGE = re.compile(r"/tt\d{5,}\.\w{3,4}$")  # IMDb rating badges (desol.one, ...)
_KP = re.compile(
    r"kinopoisk\.ru/(?:film|series)/(\d+)"
    r"|(?:rating\.kinopoisk\.ru|kinopoisk\.ru/rating)/(\d+)\.gif"
)
_IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?.*)?$", re.I)
_LABEL = re.compile(r"^([^:]{2,45}?)\s*:\s*(.*)$")
_LABEL_LIKE = re.compile(r"^[A-Za-zА-Яа-яЁё][^!?]{1,29}$")
_SHORT_VALUE = 60  # unknown "Label: value" lines with a longer value are prose
_VIDEO_TECH = re.compile(
    r"\d{3,4}x\d{3,4}|AVC|HEVC|x26[45]|H\.?26[45]|MPEG|kbps|Кбит|fps|AV1|VP9|XviD|DivX",
    re.I,
)
_LANG_CODE = re.compile(r"[a-z]{3}")
_LABEL_LINE = re.compile(r"^\s*([^:]{1,40}?)\s*:\s*(.+)$")
_LABEL_NUMBER = re.compile(r"[\s№#(]*\d+[)]?\s*$|\(ы\)$")
_AUDIO_CODEC = re.compile(
    r"TrueHD(?:\s*\+?\s*Atmos)?|DTS-HD\s*MA|DTS-HD\s*HRA|DTS:X|DTS|E-?AC-?3(?:\s*JOC)?"
    r"|AC-?3|AAC|FLAC|LPCM|PCM|Opus|MP3|Atmos",
    re.I,
)
_AUDIO_CHANNELS = re.compile(r"\b([2578])\.[01]\b|\b([2-8])\s*ch\b", re.I)
_AUDIO_CANON = {
    "TRUEHDATMOS": "TrueHD Atmos",
    "TRUEHD": "TrueHD",
    "DTSHDMA": "DTS-HD MA",
    "DTSHDHRA": "DTS-HD HRA",
    "DTS:X": "DTS:X",
    "DTS": "DTS",
    "EAC3JOC": "E-AC3 JOC",
    "EAC3": "E-AC3",
    "AC3": "AC3",
    "AAC": "AAC",
    "FLAC": "FLAC",
    "LPCM": "LPCM",
    "PCM": "PCM",
    "OPUS": "Opus",
    "MP3": "MP3",
    "ATMOS": "Atmos",
}
_TRANSLATION_PREFIX = re.compile(r"^(?:№|#)\s*\d+\s*:\s*")
_TRANSLATION_CANON: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Профессиональный\s*\(многоголосый(?:\s*закадровый)?\)", re.I), "MVO"),
    (re.compile(r"Профессиональный\s*\(двухголосый(?:\s*закадровый)?\)", re.I), "DVO"),
    (re.compile(r"Профессиональный\s*\(одноголосый(?:\s*закадровый)?\)", re.I), "AVO"),
    (
        re.compile(r"Любительский\s*\(многоголосый(?:\s*закадровый)?\)", re.I),
        "MVO (люб.)",
    ),
    (
        re.compile(r"Любительский\s*\(одноголосый(?:\s*закадровый)?\)", re.I),
        "AVO (люб.)",
    ),
    (re.compile(r"Авторский\s*\(одноголосый(?:\s*закадровый)?\)", re.I), "AVO"),
    (re.compile(r"Дублированный\s*\(неофициальный\)", re.I), "Дубляж (неофиц.)"),
    (re.compile(r"Дублированный(?:\s*\(профессиональный\))?", re.I), "Дубляж"),
    (re.compile(r"\s*-\s*студия\s*", re.I), " | "),
)
_VIDEO_TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("HEVC", re.compile(r"HEVC|H\.?265|x265", re.I)),
    ("H.264", re.compile(r"\bAVC\b|H\.?264|x264", re.I)),
    ("AV1", re.compile(r"\bAV1\b", re.I)),
    ("HDR10+", re.compile(r"HDR10\+", re.I)),
    ("HDR10", re.compile(r"HDR10(?!\+)", re.I)),
    ("Dolby Vision", re.compile(r"Dolby\s*Vision|\bDV\b")),
    ("HDR", re.compile(r"\bHDR\b(?!10)", re.I)),
)
_SUBTITLE_LANGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("рус", re.compile(r"русск|\brus\b|на русском", re.I)),
    ("англ", re.compile(r"англ|\beng\b", re.I)),
    ("укр", re.compile(r"украин|\bukr\b", re.I)),
)

IMAGE_NOISE = (
    "/smiles/",
    "/smilies/",
    "/images/smile",
    "kinopoisk.ru/rating/",
    "rating.kinopoisk.ru",
    "/imdb/pic/",
    "sample_light",
    "/images/channel/",
    "cdnbunny.org",
    "static.rutracker",
    "/forum/images/",
    "/templates/",
    "spacer",
    "pixel.gif",
    "magnet.gif",
    "/i/magnet",
    "yandex.ru/i/",
    "ratinginformer.",
    "desol.one",
)
PREFERRED_IMAGE_HOSTS = (
    "fastpic.",
    "imgbox.",
    "imageban.",
    "postimg.",
    "ibb.co",
    "imgur.",
    "directupload",
    "hostingkartinok",
    "radikal.",
    "imgbb.",
)
SUBTITLE_SHORT = (
    "рус",
    "англ",
    "укр",
    "нем",
    "фр",
    "исп",
    "ит",
    "яп",
    "кор",
    "кит",
    "болг",
    "рум",
    "пол",
    "тур",
    "порт",
)
PLOT_LABELS = ("описание", "о фильме", "сюжет", "синопсис", "о сериале", "об игре")
MULTILINE_LABELS = (*PLOT_LABELS, "перевод")
KNOWN_LABELS = (
    *MULTILINE_LABELS,
    "год",
    "дата",
    "страна",
    "производство",
    "выпущено",
    "жанр",
    "режисс",
    "в ролях",
    "актер",
    "актёр",
    "продолжительность",
    "озвуч",
    "дубляж",
    "субтитры",
    "сабы",
    "качество",
    "видео",
    "аудио",
    "звук",
    "формат",
    "контейнер",
    "релиз",
    "разработчик",
    "издатель",
    "версия",
    "платформа",
    "язык",
    "возраст",
    "рейтинг",
    "название",
    "оригинальное",
    "тип",
    "сэмпл",
    "доп",
    "системные",
    "таблетка",
    "лекарство",
    "движок",
    "источник",
    "издание",
    "студия",
    "бюджет",
    "сборы",
    "премьера",
    "сценари",
    "продюсер",
    "композитор",
    "оператор",
    "роли",
)
_STOP_LINES = ("скриншоты", "все одноименные релизы", "просмотр доступен", "скриншот")
GAME_KEYS = {
    "разработчик": "developer",
    "издатель": "publisher",
    "издательство": "publisher",
    "версия": "version",
    "платформа": "platform",
}
LANG_KEYS = {"язык интерфейса": "ui_lang", "язык озвучки": "voice_lang", "язык": "lang"}


@dataclass(slots=True)
class Field:
    label: str  # lowercased, trimmed
    value: str


@dataclass(slots=True)
class ImageRef:
    url: str  # full-size URL where it could be derived
    aligned: bool  # right/left-floated image = the poster position in phpBB posts


# --- primitives -------------------------------------------------------------


def clean(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


def root_of(html_text: str) -> HtmlElement:
    return html.fromstring(html_text)


def lines_of(el: HtmlElement) -> list[str]:
    """Text of ``el`` split at <br> and block boundaries; empty lines dropped."""
    parts: list[str] = []

    def walk(node: HtmlElement) -> None:
        if not isinstance(node.tag, str) or node.tag in SKIP_TAGS:  # comment
            return
        if node.tag in ("img", "var"):
            parts.append(IMG_MARK)
        if node.tag in BLOCK_TAGS:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if node.tag in BLOCK_TAGS:
            parts.append("\n")

    walk(el)
    lines = (clean(raw).strip(IMG_MARK + " ") for raw in "".join(parts).split("\n"))
    return [line for line in lines if line]


def split_list(
    value: str, max_items: int = MAX_LIST, max_len: int = 60, separators: str = ",;"
) -> list[str]:
    """Split on the separators outside parentheses, trimming, deduping, capping."""
    items: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if ch in separators + IMG_MARK and depth <= 0:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    out: list[str] = []
    for item in items:
        text = clean(item).strip(" .-–|")
        if text and text not in out:
            out.append(text[:max_len])
    return out[:max_items]


def truncate(text: str, limit: int = MAX_PLOT) -> str:
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return cut[: cut.rfind(" ")].rstrip(" ,;:-") + "…"


def trim_plot(text: str) -> str | None:
    return truncate(text) or None


def absolute(base: str, src: str) -> str:
    return urljoin(base, src.strip())


def parse_year(value: str) -> int | None:
    found = _YEAR.search(value)
    return int(found.group(1)) if found else None


def parse_duration(value: str) -> int | None:
    found = _DURATION_HMS.search(value)
    if found:
        if found.group(3) is None:  # "mm:ss" is a track length, not a film
            return None
        return int(found.group(1)) * 60 + int(found.group(2))
    words = _DURATION_WORDS.search(value)
    if words and (words.group(1) or words.group(2)):
        return int(words.group(1) or 0) * 60 + int(words.group(2) or 0)
    return None


def find_ids(markup: str) -> tuple[str | None, int | None]:
    """IMDb (tt…) and Kinopoisk ids from links and rating badges in the post."""
    imdb = _IMDB.search(markup)
    kp = _KP.search(markup)
    imdb_id = None
    if imdb:
        imdb_id = (
            imdb.group(1)
            or imdb.group(3)
            or imdb.group(4)
            or imdb.group(5)
            or f"tt{imdb.group(2)}"
        )
    kp_id = int(kp.group(1) or kp.group(2)) if kp else None
    return imdb_id, kp_id


def dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


# --- images -----------------------------------------------------------------


def unwrap_image_proxy(url: str) -> str:
    """nnmclub wraps images as image.php?link=<original>; the proxy answers 403."""
    if "image.php?" in url:
        link = parse_qs(urlsplit(url).query).get("link")
        if link:
            return link[0]
    return url


def full_size(url: str) -> str:
    url = re.sub(
        r"(imageban\.ru)/thumbs/(\d{4})\.(\d{2})\.(\d{2})/", r"\1/out/\2/\3/\4/", url
    )
    return re.sub(r"(fastpic\.(?:ru|org))/thumb/(.+)\.jpeg$", r"\1/big/\2.jpg", url)


def is_noise(url: str) -> bool:
    low = url.lower()
    return any(marker in low for marker in IMAGE_NOISE) or bool(_BADGE.search(low))


def is_preferred_host(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(marker in host for marker in PREFERRED_IMAGE_HOSTS)


def image_refs(el: HtmlElement, base_url: str) -> list[ImageRef]:
    """Images of a post in document order: <img src> and phpBB's <var class=postImg>."""
    refs: list[ImageRef] = []
    for node in el.iter("img", "var"):
        raw = node.get("src") if node.tag == "img" else node.get("title")
        if not raw:
            continue
        url = urljoin(base_url, unwrap_image_proxy(raw.strip()))
        if is_noise(url) or not url.startswith("http"):
            continue
        parent = node.getparent()
        link = parent if parent is not None and parent.tag == "a" else None
        href = link.get("href") if link is not None else None
        if href and _IMAGE_EXT.search(href) and not is_noise(href):
            url = urljoin(base_url, unwrap_image_proxy(href))
        else:
            url = full_size(url)
        style = node.get("style") or ""
        if link is not None:
            style += link.get("style") or ""
        aligned = "img-right" in (node.get("class") or "") or "float" in style
        if all(ref.url != url for ref in refs):
            refs.append(ImageRef(url, aligned))
    return refs


def pick_images(refs: list[ImageRef]) -> tuple[list[str], list[str]]:
    """Poster candidates (best first) and screenshots from a post's images."""
    if not refs:
        return [], []
    poster = next((r for r in refs if r.aligned and is_preferred_host(r.url)), None)
    poster = poster or next((r for r in refs if r.aligned), None)
    poster = poster or next((r for r in refs if is_preferred_host(r.url)), None)
    poster = poster or refs[0]
    others = [r for r in refs if r.url != poster.url]
    posters = [poster.url] + [r.url for r in others if r.aligned][:1]
    shots = [r.url for r in others if not r.url.lower().endswith(".gif")]
    return posters, shots[:MAX_SCREENSHOTS]


# --- title ------------------------------------------------------------------


def meta_from_title(meta: TorrentMeta, title: str | None) -> None:
    """Fill title/year/season/episodes from the topic title where the post did not."""
    if not title:
        return
    title = clean(title)
    meta.extra.setdefault("title", title[:200])
    parsed = parse_title(title)
    meta.title_ru = meta.title_ru or parsed.title_ru
    meta.title_en = meta.title_en or parsed.title_en
    meta.year = meta.year or parsed.year
    meta.season = meta.season or parsed.season
    meta.episodes = meta.episodes or parsed.episodes


# --- forum posts: multi-line "Label: value" blocks ---------------------------


def fields_of(lines: list[str]) -> list[Field]:
    """``Label: value`` lines into fields; plot/translation values may span lines."""
    fields: list[Field] = []
    for line in lines:
        found = _LABEL.match(line)
        if found and _is_label(found.group(1), found.group(2)):
            fields.append(Field(found.group(1).strip().lower(), found.group(2).strip()))
        elif fields and (
            fields[-1].label.startswith(MULTILINE_LABELS) or not fields[-1].value
        ):
            if line.lower().startswith(_STOP_LINES):
                fields.append(Field("", ""))  # terminates the multi-line value
            elif _HAS_WORD.search(line):  # skip "| |" style fillers
                fields[-1].value = f"{fields[-1].value}\n{line}".strip()
    return [field for field in fields if field.label]


def _is_label(label: str, value: str) -> bool:
    if not _LABEL_LIKE.match(label):
        return False
    return label.lower().startswith(KNOWN_LABELS) or len(value) <= _SHORT_VALUE


def countries_of(value: str) -> list[str]:
    """Countries come first and are Cyrillic; Latin studios follow after '/' or ','."""
    out: list[str] = []
    for item in split_list(value.split(" / ")[0], 5):
        if _LATIN.search(item) and not _CYRILLIC.search(item):
            break
        out.append(item)
    return out


def short_subtitles(value: str) -> list[str]:
    out: list[str] = []
    for item in split_list(value, 8):
        word = re.sub(r"\(.*?\)", "", item).strip().lower()
        if not word:
            continue
        short = next((k for k in SUBTITLE_SHORT if word.startswith(k)), word[:20])
        if short not in out:
            out.append(short)
    return out[:6]


def split_translation(raw: str) -> list[str]:
    value = raw.replace(IMG_MARK, " ")
    items = [re.sub(r"^#\d+:\s*", "", line) for line in value.split("\n")]
    if len(items) == 1:
        text = items[0]
        items = text.split(" / ") if " / " in text else split_list(text, 6, 80)
    return [clean(i).strip(" -–|") for i in items if clean(i).strip(" -–|")]


def apply_fields(meta: TorrentMeta, fields: list[Field]) -> None:  # noqa: C901
    """Map labeled lines onto TorrentMeta; unknown-but-useful labels go to ``extra``."""
    audio: list[str] = []
    translation: list[str] = []
    quality: str | None = None
    video_tech: str | None = None
    for field in fields:
        label, raw = field.label, field.value
        value = clean(raw.replace(IMG_MARK, " "))
        if not value:
            continue
        if label in ("название", "русское название"):
            if _CYRILLIC.search(value):
                meta.title_ru = meta.title_ru or value[:200]
            else:
                meta.title_en = meta.title_en or value[:200]
        elif label in (
            "оригинальное название",
            "название на английском",
            "английское название",
        ):
            meta.title_en = meta.title_en or value[:200]
        elif re.fullmatch(r"год(?: выпуска| выхода)?|дата выхода", label):
            meta.year = meta.year or parse_year(value)
        elif label in ("дата выпуска", "дата релиза"):
            meta.extra.setdefault("release_date", value[:40])
            meta.year = meta.year or parse_year(value)
        elif label in ("страна", "производство", "выпущено"):
            meta.countries = meta.countries or countries_of(raw)
        elif label.startswith("жанр"):
            meta.genres = meta.genres or [g.lower() for g in split_list(raw, 6, 40)]
        elif label.startswith(("режисс", "постановщик")):
            meta.director = meta.director or value[:120]
        elif label in (
            "в ролях",
            "актеры",
            "актёры",
            "в главных ролях",
            "актеры и роли",
        ):
            meta.cast = meta.cast or [
                clean(re.sub(r"\s*\(.*?\)", "", name))
                for name in split_list(raw, MAX_CAST, 80)
            ]
        elif label in ("продолжительность", "длительность", "хронометраж"):
            meta.duration_min = meta.duration_min or parse_duration(value)
        elif label.startswith(PLOT_LABELS):
            meta.plot = meta.plot or trim_plot(value)
        elif label.startswith("перевод") or label in ("озвучка", "озвучание", "дубляж"):
            translation.extend(split_translation(raw))
        elif label in ("субтитры", "язык субтитров", "сабы"):
            meta.subtitles = meta.subtitles or short_subtitles(raw)
        elif label in ("качество", "качество видео", "тип релиза"):
            quality = quality or value[:60]
        elif label == "видео" and _VIDEO_TECH.search(value):
            video_tech = video_tech or value[:100]
        elif re.fullmatch(r"(аудио|звук)(?:\s*#?\s*\d+)?|[a-z]{3}", label):
            prefix = f"{label.upper()}: " if _LANG_CODE.fullmatch(label) else ""
            audio.append(prefix + value[:80])
        elif label in ("формат", "контейнер", "формат видео"):
            meta.extra.setdefault("format", value.split(" (")[0][:30])
        elif label in ("релиз от", "релиз-группа"):
            meta.extra.setdefault("release", value[:40])
        elif label in GAME_KEYS:
            meta.extra.setdefault(GAME_KEYS[label], value[:60])
        elif label in LANG_KEYS:
            meta.extra.setdefault(LANG_KEYS[label], value[:80])
        elif label.startswith("возраст"):
            age = _AGE.search(value)
            meta.extra.setdefault("age", f"{age.group(1)}+" if age else value[:20])
        elif label.startswith("рейтинг mpaa"):
            meta.extra.setdefault("age", value.split(" (")[0][:20])
    if translation:
        meta.translation = meta.translation or dedupe(translation)[:MAX_TRANSLATIONS]
    if quality or video_tech:
        meta.video = (
            meta.video or " | ".join(v for v in (quality, video_tech) if v)[:160]
        )
    if audio:
        meta.audio = meta.audio or " / ".join(dedupe(audio))[:300]


# --- structured pages: one "Label: value" per line ---------------------------


def labeled(lines: list[str]) -> list[tuple[str, str]]:
    """``Label: value`` pairs; label lowercased, trailing number / '№ 1' removed."""
    pairs: list[tuple[str, str]] = []
    for line in lines:
        match = _LABEL_LINE.match(line)
        if match:
            label = _LABEL_NUMBER.sub("", match.group(1).lower())
            pairs.append((label.strip(" .:").replace("ё", "е"), match.group(2).strip()))
    return pairs


def normalize_translation(value: str) -> str:
    """'№ 5: Профессиональный (многоголосый закадровый) | X' → 'MVO | X'."""
    value = _TRANSLATION_PREFIX.sub("", value)
    for pattern, replacement in _TRANSLATION_CANON:
        value = pattern.sub(replacement, value)
    return truncate(clean(value.replace('"', "")), 60)


def add_translation(meta: TorrentMeta, value: str) -> None:
    item = normalize_translation(value)
    if (
        item
        and item not in meta.translation
        and len(meta.translation) < MAX_TRANSLATIONS
    ):
        meta.translation.append(item)


def summarize_audio(lines: list[str]) -> str | None:
    """'Русский, E-AC3, 6 ch, 384 Кбит/с' … → 'E-AC3 5.1 / AC3 5.1 / TrueHD 7.1'."""
    entries: list[str] = []
    for line in lines:
        codec = _AUDIO_CODEC.search(line)
        if not codec:
            continue
        key = re.sub(r"[\s\-+]", "", codec.group(0).upper())
        name = _AUDIO_CANON.get(key, codec.group(0).upper())
        channels = _AUDIO_CHANNELS.search(line)
        if channels:
            if channels.group(1):
                name += " " + channels.group(0)
            else:
                name += " " + {"2": "2.0", "6": "5.1", "8": "7.1"}.get(
                    channels.group(2), channels.group(2) + "ch"
                )
        if name not in entries:
            entries.append(name)
    if not entries:
        return None
    summary = " / ".join(entries[:4])
    return summary + f" +{len(entries) - 4}" if len(entries) > 4 else summary


def compose_video(quality: str | None, details: list[str]) -> str | None:
    """'Blu-Ray Remux 2160p' + 'MPEG-H HEVC HDR, Dolby Vision' → '… HEVC HDR DV'."""
    parts = [clean(quality)] if quality else []
    blob = " ".join(details)
    for name, pattern in _VIDEO_TOKENS:
        if pattern.search(blob) and not any(name.lower() in p.lower() for p in parts):
            if name == "HDR" and any("HDR10" in p for p in parts):
                continue
            parts.append(name)
    return " ".join(parts) or None


def subtitle_langs(value: str) -> list[str]:
    found = [name for name, pattern in _SUBTITLE_LANGS if pattern.search(value)]
    return found or [truncate(value, 60)]


def _countries_and_studio(value: str) -> tuple[list[str], str | None]:
    """'США, Канада, Legendary Entertainment' → (['США', 'Канада'], 'Legendary …')."""
    countries: list[str] = []
    studios: list[str] = []
    for item in split_list(value, separators=",;/|"):
        (studios if _LATIN.search(item) else countries).append(item)
    return countries, ", ".join(studios) or None


def apply_field(meta: TorrentMeta, label: str, value: str) -> bool:  # noqa: C901
    """Apply a known ``label: value`` pair to meta; False when the label is unknown."""
    if label == "название":
        if _CYRILLIC.search(value):
            meta.title_ru = meta.title_ru or clean(value)
        else:
            meta.title_en = meta.title_en or clean(value)
    elif label == "оригинальное название":
        meta.title_en = meta.title_en or clean(value)
    elif label in ("год выхода", "год выпуска", "год", "дата выхода", "дата выпуска"):
        meta.year = meta.year or parse_year(value)
        if label.startswith("дата"):
            meta.extra.setdefault("release_date", clean(value))
    elif label == "жанр":
        meta.genres = meta.genres or [
            g.lower() for g in split_list(value, separators=",;/|")
        ]
    elif label == "страна":
        meta.countries = meta.countries or split_list(value, separators=",;/|")
    elif label == "выпущено":
        countries, studio = _countries_and_studio(value)
        meta.countries = meta.countries or countries
        if studio:
            meta.extra.setdefault("studio", studio)
    elif label in ("режиссер", "режиссеры"):
        meta.director = meta.director or clean(value)
    elif label in ("в ролях", "актеры", "в главных ролях"):
        meta.cast = meta.cast or split_list(value, MAX_CAST, separators=",;/|")
    elif label in ("о фильме", "описание", "сюжет", "о сериале", "об игре"):
        meta.plot = meta.plot or truncate(value)
    elif label == "продолжительность":
        meta.duration_min = meta.duration_min or parse_duration(value)
    elif label in ("перевод", "переводы", "озвучка", "озвучивание"):
        add_translation(meta, value)
    elif label == "субтитры":
        meta.subtitles = meta.subtitles or subtitle_langs(value)
    elif label in ("качество", "тип релиза", "качество видео"):
        meta.extra.setdefault("quality", clean(value))
    elif label == "видео":
        meta.extra.setdefault("video_info", truncate(value, 120))
    elif label in ("кодек", "видео кодек"):
        meta.extra.setdefault("codec", clean(value))
    elif label in ("звук", "аудио"):
        joined = meta.extra.get("audio_lines", "") + "\n" + value
        meta.extra["audio_lines"] = joined.strip()
    elif label == "контейнер":
        meta.extra.setdefault("container", clean(value))
    elif label == "студия":
        meta.extra.setdefault("studio", clean(value))
    elif label == "разработчик":
        meta.extra.setdefault("developer", clean(value))
    elif label in ("издательство", "издатель"):
        meta.extra.setdefault("publisher", clean(value))
    elif label in ("версия игры", "версия"):
        meta.extra.setdefault("version", clean(value))
    elif label == "платформа":
        meta.extra.setdefault("platform", clean(value))
    elif label in ("язык интерфейса", "интерфейс", "язык"):
        meta.extra.setdefault("language", truncate(value, 80))
    elif label == "язык озвучки":
        meta.extra.setdefault("voice", truncate(value, 60))
    elif label == "тип издания":
        meta.extra.setdefault("edition", clean(value))
    elif label in ("лекарство", "таблетка"):
        meta.extra.setdefault("crack", clean(value))
    elif label in ("операционная система", "ос"):
        meta.extra.setdefault("os", truncate(value, 60))
    elif label == "процессор":
        meta.extra.setdefault("cpu", truncate(value, 60))
    elif label in ("память", "оперативная память"):
        meta.extra.setdefault("ram", truncate(value, 30))
    elif label == "видеокарта":
        meta.extra.setdefault("gpu", truncate(value, 60))
    elif label in (
        "место на диске",
        "свободное место на диске",
        "дисковое пространство",
    ):
        meta.extra.setdefault("disk", truncate(value, 30))
    else:
        return False
    return True


def finalize_av(meta: TorrentMeta, hint: str = "") -> None:
    """Build meta.video / meta.audio from the collected quality/codec/audio lines."""
    quality = meta.extra.pop("quality", None)
    details = [meta.extra.get("video_info", ""), meta.extra.get("codec", ""), hint]
    meta.video = compose_video(quality, details)
    audio_lines = meta.extra.pop("audio_lines", "")
    if audio_lines:
        meta.audio = summarize_audio(audio_lines.split("\n"))
