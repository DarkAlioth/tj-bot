"""Result model shared by the tracker-page parsers."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class TorrentMeta:
    """What one tracker topic page says about a release; every field is optional."""

    # rutracker | nnmclub | tapochek | rutor | megapeer | pctorrent | byrutor
    tracker: str
    source_url: str
    title_ru: str | None = None
    title_en: str | None = None
    year: int | None = None
    poster_urls: list[str] = field(default_factory=list)  # best first, absolute
    screenshots: list[str] = field(default_factory=list)  # <= 6
    genres: list[str] = field(default_factory=list)  # lowercase
    countries: list[str] = field(default_factory=list)
    director: str | None = None
    cast: list[str] = field(default_factory=list)  # <= 8
    duration_min: int | None = None
    plot: str | None = None  # plain text, <= 1000 chars
    translation: list[str] = field(default_factory=list)  # ["Дубляж", "MVO | LostFilm"]
    subtitles: list[str] = field(default_factory=list)  # ["рус", "англ"]
    video: str | None = None  # "BDRemux 2160p HEVC HDR10"
    audio: str | None = None  # "DTS-HD MA 7.1 / AC3 5.1"
    imdb_id: str | None = None  # "tt1160419"
    kp_id: int | None = None
    season: int | None = None
    episodes: str | None = None  # "1-6 of 6"
    extra: dict[str, str] = field(default_factory=dict)  # short keys, short values


class ScrapeError(Exception):
    """The page could not be fetched or parsed."""


class ChallengeError(ScrapeError):
    """An anti-bot (Cloudflare) challenge came back instead of the topic."""


class NotFoundError(ScrapeError):
    """HTTP 404/410: the topic is gone."""


class LoginRequiredError(ScrapeError):
    """The tracker hides the topic behind a login (Tapochek video sections)."""
