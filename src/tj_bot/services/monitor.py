import asyncio
import logging

from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services import broadcaster
from tj_bot.services.formatting import format_size
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)


async def collect_problems(
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    free_space_threshold_bytes: int,
) -> dict[str, str]:
    """Current problems as stable key -> human description."""
    problems: dict[str, str] = {}
    try:
        for indexer in await jackett.indexers():
            if indexer.get("Error"):
                name = str(indexer.get("Name", "?"))
                problems[f"indexer:{name}"] = f"Индексер упал: {name}"
    except JackettError:
        problems["jackett"] = "Jackett недоступен"
    if qbit is not None:
        try:
            free = await qbit.free_space()
        except QbittorrentError:
            # the qbit host may be legitimately powered off — /health covers it
            logger.debug("Skipping free-space check: qBittorrent unreachable")
        else:
            if 0 <= free < free_space_threshold_bytes:
                problems["space"] = (
                    f"Мало места на диске qBittorrent: {format_size(free)}"
                )
    return problems


def recovery_text(key: str) -> str:
    if key.startswith("indexer:"):
        return f"Индексер снова в строю: {key.removeprefix('indexer:')}"
    if key == "jackett":
        return "Jackett снова доступен"
    return "Место на диске снова в норме"


def build_alert_text(new: list[str], recovered: list[str]) -> str:
    lines = ["🚨 <b>Мониторинг</b>"]
    lines.extend(f"⚠️ {text}" for text in new)
    lines.extend(f"✅ {text}" for text in recovered)
    return "\n".join(lines)


async def check_once(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    known: dict[str, str] | None,
) -> dict[str, str]:
    """One monitoring pass; alerts admins about state transitions only.

    ``known=None`` is the baseline pass: the current state is recorded
    silently, so a deploy restart does not re-announce long-standing problems.
    """
    threshold = config.settings.alert_free_space_gb * 1024**3
    problems = await collect_problems(jackett, qbit, threshold)
    if known is None:
        if problems:
            logger.info("Monitor baseline: %s existing problems", len(problems))
        return problems
    new = [text for key, text in problems.items() if key not in known]
    recovered = [recovery_text(key) for key in known if key not in problems]
    if new or recovered:
        await broadcaster.broadcast(
            bot, config.admin_ids, build_alert_text(new, recovered)
        )
    return problems


async def monitor_loop(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    interval_seconds: int,
) -> None:
    """Periodically probe indexers and disk space; edge-triggered admin alerts.

    Sleeps before the first pass so the baseline runs against a warmed-up
    Jackett, not the half-started state right after a deploy.
    """
    known: dict[str, str] | None = None
    while True:
        await asyncio.sleep(interval_seconds)
        known = await check_once(bot, jackett, qbit, config, known)
