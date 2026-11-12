"""Parse tracker release titles into title/year/quality chips. Never raises."""

import re
from dataclasses import dataclass, field

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
# Typographic punctuation / Latin-1 symbols that dominate cp1251→cp1252 mojibake.
_MOJIBAKE = re.compile(r"[‐-› -¿™ƒˆŠŽ]")
_YEAR_PAREN = re.compile(r"\((19\d{2}|20\d{2})(?=[,)\s])")
_YEAR_BRACKET = re.compile(r"\[\s*(19\d{2}|20\d{2})\b")
_YEAR_LOOSE = re.compile(r"(?<![\d.])(19\d{2}|20\d{2})(?![\d.])")
_LEADING_TAGS = re.compile(r"^(?:\[[^\]]{1,12}\]\s*)+")
_PAREN_GROUP = re.compile(r"\([^()]*\)")
_BRACKET_GROUP = re.compile(r"\[[^\[\]]*\]")
_SEASON_PATTERNS = (
    re.compile(r"\bS(\d{1,2})(?:E\d)?", re.I),
    re.compile(r"Сезон\s*:?\s*(\d{1,2})", re.I),
    re.compile(r"(\d{1,2})\s*сезон", re.I),
)
_EPISODES_PATTERNS = (
    re.compile(r"\bS\d{1,2}E(\d{1,3}(?:-\d{1,3})?)\s*(?:of|из)\s*(\d{1,3})", re.I),
    re.compile(r"Серии\s*:?\s*(\d{1,3}(?:-\d{1,3})?)\s*(?:из|of)\s*(\d{1,3})", re.I),
    re.compile(r"\bE(\d{1,3}(?:-\d{1,3})?)\s*(?:of|из)\s*(\d{1,3})", re.I),
)
_EPISODE_ONLY = re.compile(r"\bS\d{1,2}E(\d{1,3}(?:-\d{1,3})?)\b", re.I)
_RESOLUTION = re.compile(r"\b(\d{3,4})[pi]\b", re.I)
_FOUR_K = re.compile(r"\b4K\b", re.I)
_HDR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("HDR10+", re.compile(r"HDR10\+", re.I)),
    ("HDR10", re.compile(r"HDR10(?!\+)", re.I)),
    ("Dolby Vision", re.compile(r"Dolby\s*Vision|\bDoVi\b|\bDV\b")),
    ("HLG", re.compile(r"\bHLG\b")),
    ("HDR", re.compile(r"\bHDR\b(?!10)", re.I)),
)
_SOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("UHD BDRemux", re.compile(r"UHD\s*(?:BD|Blu-?Ray)\s*Remux", re.I)),
    ("BDRemux", re.compile(r"(?:BD|Blu-?Ray)\s*Remux", re.I)),
    ("UHD BDRip", re.compile(r"UHD\s*BDRip", re.I)),
    ("BDRip", re.compile(r"\bBDRip\b", re.I)),
    ("Blu-Ray", re.compile(r"\bBlu-?Ray\b", re.I)),
    ("WEB-DL", re.compile(r"\bWEB-?DL", re.I)),
    ("WEBRip", re.compile(r"\bWEB-?Rip\b", re.I)),
    ("HDTVRip", re.compile(r"\bHDTVRip\b", re.I)),
    ("HDTV", re.compile(r"\bHDTV\b", re.I)),
    ("DVDRip", re.compile(r"\bDVDRip\b", re.I)),
    ("DVD", re.compile(r"\bDVD[59]?\b", re.I)),
    ("HDRip", re.compile(r"\bHDRip\b", re.I)),
    ("CAMRip", re.compile(r"\bCAMRip\b|\bTS\b", re.I)),
)
_CODEC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("x265", re.compile(r"\bx265\b", re.I)),
    ("x264", re.compile(r"\bx264\b", re.I)),
    ("HEVC", re.compile(r"\bHEVC\b|\bH\.?265\b", re.I)),
    ("H.264", re.compile(r"\bH\.?264\b|\bAVC\b", re.I)),
    ("AV1", re.compile(r"\bAV1\b", re.I)),
)
_AUDIO_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Dub", re.compile(r"\bDub\b|\bДубляж|\bДублированн", re.I)),
    ("MVO", re.compile(r"\bMVO\b", re.I)),
    ("DVO", re.compile(r"\bDVO\b", re.I)),
    ("AVO", re.compile(r"\bAVO\b", re.I)),
    ("Original", re.compile(r"\bOriginal\b|Оригинал", re.I)),
    ("Sub", re.compile(r"\bSub\b|Субтитры", re.I)),
)
# Rutor/MegaPeer tail legend "| D, P, A": D dub, P pro MVO, P1/P2 pro AVO/DVO,
# L amateur, A author, O original.
_LEGEND = {
    "D": "Dub",
    "P": "MVO",
    "P1": "AVO",
    "P2": "DVO",
    "L": "MVO",
    "L1": "AVO",
    "L2": "DVO",
    "A": "AVO",
    "O": "Original",
}
_LEGEND_GROUP = re.compile(r"^\s*((?:[DPLAO][12]?\s*,\s*)*[DPLAO][12]?)\s*$")
_EXTRA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("3D", re.compile(r"\b3D\b")),
    ("IMAX", re.compile(r"\bIMAX\b", re.I)),
    ("Extended", re.compile(r"\bExtended\b|Расширенная", re.I)),
    ("Director's Cut", re.compile(r"Director'?s\s*Cut|Режисс[её]рская", re.I)),
    ("Remastered", re.compile(r"\bRemaster(?:ed)?\b", re.I)),
    ("RePack", re.compile(r"\bRePack\b", re.I)),
)
MAX_CHIPS = 5  # the search card shows at most five quality chips


@dataclass(slots=True)
class ParsedTitle:
    title_ru: str | None = None
    title_en: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: str | None = None
    resolution: str | None = None
    hdr: list[str] = field(default_factory=list)
    source: str | None = None
    codec: str | None = None
    audio_tags: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)


def is_mojibake(text: str) -> bool:
    letters = sum(1 for ch in text if ch.isalnum())
    junk = len(_MOJIBAKE.findall(text))
    return junk > 0 and junk >= letters


def _clean_part(part: str) -> str:
    part = _PAREN_GROUP.sub(" ", part)
    part = _BRACKET_GROUP.sub(" ", part)
    return re.sub(r"\s+", " ", part).strip(" -–—:|,")


def _is_meta_part(part: str) -> bool:
    return (
        any(p.search(part) for p in _SEASON_PATTERNS)
        or any(p.search(part) for p in _EPISODES_PATTERNS)
        or bool(re.fullmatch(r"\s*(Серии|Эпизоды)\b.*", part, re.I))
    )


def _split_head(head: str) -> tuple[str | None, str | None]:
    title_ru: str | None = None
    title_en: str | None = None
    head = _BRACKET_GROUP.sub(" ", _PAREN_GROUP.sub(" ", head))
    for raw in re.split(r"\s+/\s+", head):
        part = _clean_part(raw)
        if not part or _is_meta_part(part) or is_mojibake(part):
            continue
        if _CYRILLIC.search(part):
            title_ru = title_ru or part
        elif _LATIN.search(part):
            title_en = title_en or part
    return title_ru, title_en


def _find_year(text: str) -> tuple[int | None, int]:
    """Return (year, index where the technical tail starts)."""
    for pattern in (_YEAR_PAREN, _YEAR_BRACKET):
        match = pattern.search(text)
        if match:
            return int(match.group(1)), match.start()
    match = _YEAR_LOOSE.search(text)
    if match:
        return int(match.group(1)), match.start()
    return None, -1


def _first_match(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> str | None:
    for name, pattern in patterns:
        if pattern.search(text):
            return name
    return None


def _all_matches(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> list[str]:
    return [name for name, pattern in patterns if pattern.search(text)]


def _season(text: str) -> int | None:
    for pattern in _SEASON_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _episodes(text: str) -> str | None:
    for pattern in _EPISODES_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"{match.group(1)} of {match.group(2)}"
    match = _EPISODE_ONLY.search(text)
    return match.group(1) if match else None


def _audio_tags(text: str) -> list[str]:
    tags = _all_matches(text, _AUDIO_PATTERNS)
    for segment in text.split("|")[1:]:
        legend = _LEGEND_GROUP.match(segment)
        if not legend:
            continue
        for token in re.split(r"\s*,\s*", legend.group(1)):
            name = _LEGEND.get(token.upper())
            if name and name not in tags:
                tags.append(name)
    return tags


def _chips(parsed: ParsedTitle) -> list[str]:
    chips: list[str] = []
    if parsed.resolution:
        chips.append(parsed.resolution)
    chips.extend(parsed.hdr)
    if parsed.source:
        chips.append(parsed.source)
    if parsed.codec:
        chips.append(parsed.codec)
    if parsed.season is not None and parsed.episodes:
        chips.append(f"S{parsed.season} E{parsed.episodes.replace(' of ', '/')}")
    elif parsed.season is not None:
        chips.append(f"S{parsed.season}")
    elif parsed.episodes:
        chips.append(f"E{parsed.episodes.replace(' of ', '/')}")
    chips.extend(parsed.extras)
    return chips[:MAX_CHIPS]


def parse_title(title: str) -> ParsedTitle:
    text = _LEADING_TAGS.sub("", re.sub(r"\s+", " ", title).strip())
    parsed = ParsedTitle()
    if not text:
        return parsed
    parsed.year, tail_start = _find_year(text)
    head = text if tail_start < 0 else text[:tail_start]
    if tail_start < 0 and "[" in text:
        head = text[: text.index("[")]
    parsed.title_ru, parsed.title_en = _split_head(head)
    if parsed.title_ru is None and parsed.title_en is None:
        fallback = _clean_part(head)
        if fallback and not is_mojibake(fallback):
            parsed.title_en = fallback
    parsed.season = _season(text)
    parsed.episodes = _episodes(text)
    tail = text[len(head) :] if tail_start >= 0 else text
    resolution = _RESOLUTION.search(tail)
    if resolution:
        parsed.resolution = resolution.group(1) + resolution.group(0)[-1].lower()
    elif _FOUR_K.search(tail):
        parsed.resolution = "2160p"
    hdr = _all_matches(tail, _HDR_PATTERNS)
    if "HDR" in hdr and len(hdr) > 1 and any(h.startswith("HDR10") for h in hdr):
        hdr.remove("HDR")
    parsed.hdr = hdr
    parsed.source = _first_match(tail, _SOURCE_PATTERNS)
    parsed.codec = _first_match(tail, _CODEC_PATTERNS)
    parsed.audio_tags = _audio_tags(tail)
    parsed.extras = _all_matches(tail, _EXTRA_PATTERNS)
    parsed.chips = _chips(parsed)
    return parsed
