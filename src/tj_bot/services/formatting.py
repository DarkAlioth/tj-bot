from typing import Any

STATE_VIEW = {
    "downloading": ("⬇️", "Загружается"),
    "stalledDL": ("🐌", "Ожидает сидов"),
    "metaDL": ("🧲", "Получение метаданных"),
    "forcedDL": ("🚀", "Принудительная загрузка"),
    "queuedDL": ("⏳", "В очереди на загрузку"),
    "checkingDL": ("🔍", "Проверка"),
    "allocating": ("📦", "Выделение места"),
    "uploading": ("⬆️", "Раздаётся"),
    "stalledUP": ("✅", "Загружен, ждёт пиров"),
    "forcedUP": ("🚀", "Принудительная раздача"),
    "queuedUP": ("⏳", "В очереди на раздачу"),
    "checkingUP": ("🔍", "Проверка"),
    "stoppedDL": ("⏸", "На паузе"),
    "pausedDL": ("⏸", "На паузе"),
    "stoppedUP": ("☑️", "Завершён"),
    "pausedUP": ("☑️", "Завершён"),
    "error": ("❌", "Ошибка"),
    "missingFiles": ("⚠️", "Файлы не найдены"),
    "moving": ("📦", "Перемещение"),
}

DOWNLOADING_STATES = {
    "downloading",
    "stalledDL",
    "metaDL",
    "forcedDL",
    "queuedDL",
    "checkingDL",
    "allocating",
}


def state_view(state: str) -> tuple[str, str]:
    return STATE_VIEW.get(state, ("❔", state))


def format_size(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "0"
    return f"{format_size(bytes_per_second)}/s"


def format_eta(seconds: int) -> str:
    if seconds < 0 or seconds >= 8640000:
        return "∞"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{max(minutes, 1)}м"


def progress_bar(progress: float) -> str:
    filled = round(progress * 10)
    return "▓" * filled + "░" * (10 - filled) + f" {progress * 100:.1f}%"


def torrent_progress_text(torrent: dict[str, Any], name: str) -> str:
    emoji, state_name = state_view(torrent.get("state", ""))
    return "\n".join(
        [
            f"{emoji} <b>{name}</b>",
            progress_bar(torrent.get("progress", 0)),
            f"{state_name} · ETA {format_eta(torrent.get('eta', -1))}",
            f"⬇️ {format_speed(torrent.get('dlspeed', 0))}"
            f" · сиды {torrent.get('num_seeds', 0)}",
        ]
    )
