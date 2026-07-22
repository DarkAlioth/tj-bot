import datetime
from dataclasses import dataclass

from sqlalchemy import ColumnElement

from tj_bot.db.models import Torrent

GB = 1024 * 1024 * 1024

# bucket 0 always means "any". Values are inclusive lower bounds / ranges.
SEEDERS_MIN = [0, 1, 10, 50]
SEEDERS_LABELS = ["любые", "1+", "10+", "50+"]
SIZE_RANGES: list[tuple[int, int | None]] = [
    (0, None),
    (0, GB),
    (GB, 5 * GB),
    (5 * GB, 20 * GB),
    (20 * GB, None),
]
SIZE_LABELS = ["любой", "< 1 GB", "1–5 GB", "5–20 GB", "> 20 GB"]
DATE_DAYS = [0, 7, 30, 365]
DATE_LABELS = ["любая", "неделя", "месяц", "год"]


@dataclass(frozen=True)
class ResultFilters:
    seeders: int = 0
    size: int = 0
    date: int = 0

    @classmethod
    def from_code(cls, code: str) -> "ResultFilters":
        if len(code) != 3 or not code.isdigit():
            return cls()
        seeders = min(int(code[0]), len(SEEDERS_MIN) - 1)
        size = min(int(code[1]), len(SIZE_RANGES) - 1)
        date = min(int(code[2]), len(DATE_DAYS) - 1)
        return cls(seeders=seeders, size=size, date=date)

    def to_code(self) -> str:
        return f"{self.seeders}{self.size}{self.date}"

    @property
    def is_active(self) -> bool:
        return self.seeders > 0 or self.size > 0 or self.date > 0

    def cycled(self, field: str) -> "ResultFilters":
        limits = {
            "seeders": len(SEEDERS_MIN),
            "size": len(SIZE_RANGES),
            "date": len(DATE_DAYS),
        }
        current = getattr(self, field)
        return type(self)(**{**self.__dict__, field: (current + 1) % limits[field]})

    def summary(self) -> str:
        parts = []
        if self.seeders:
            parts.append(f"сиды {SEEDERS_LABELS[self.seeders]}")
        if self.size:
            parts.append(f"размер {SIZE_LABELS[self.size]}")
        if self.date:
            parts.append(f"дата {DATE_LABELS[self.date]}")
        return ", ".join(parts) if parts else "не заданы"

    def conditions(self) -> list[ColumnElement[bool]]:
        conds: list[ColumnElement[bool]] = []
        if self.seeders:
            conds.append(Torrent.seeders >= SEEDERS_MIN[self.seeders])
        if self.size:
            low, high = SIZE_RANGES[self.size]
            if low:
                conds.append(Torrent.size >= low)
            if high is not None:
                conds.append(Torrent.size < high)
        if self.date:
            cutoff = datetime.date.today() - datetime.timedelta(
                days=DATE_DAYS[self.date]
            )
            conds.append(Torrent.published_at >= cutoff)
        return conds
