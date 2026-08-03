import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services import broadcaster
from tj_bot.services.formatting import format_size, padded
from tj_bot.services.jackett import JackettClient, JackettError
from tj_bot.services.qbittorrent import QbittorrentClient, QbittorrentError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Problem:
    """One monitored failure with its alert and recovery texts."""

    alert: str
    recovered: str


JACKETT_PROBLEM = Problem("Jackett недоступен", "Jackett снова доступен")


def indexer_problem(name: str) -> Problem:
    return Problem(
        alert=f"Индексер упал: {name}",
        recovered=f"Индексер снова в строю: {name}",
    )


def space_problem(free: int) -> Problem:
    return Problem(
        alert=f"Мало места на диске qBittorrent: {format_size(free)}",
        recovered="Место на диске снова в норме",
    )


async def collect_problems(
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    free_space_threshold_bytes: int,
) -> dict[str, Problem]:
    """Current problems keyed by a stable id (``indexer:<id>``/jackett/space)."""
    problems: dict[str, Problem] = {}
    try:
        for indexer in await jackett.indexers():
            if indexer.get("Error"):
                idx = str(indexer.get("ID") or indexer.get("Name") or "?")
                name = str(indexer.get("Name") or idx)
                problems[f"indexer:{idx}"] = indexer_problem(name)
    except JackettError:
        problems["jackett"] = JACKETT_PROBLEM
    if qbit is not None:
        try:
            free = await qbit.free_space()
        except QbittorrentError:
            # the qbit host may be legitimately powered off — /health covers it
            logger.debug("Skipping free-space check: qBittorrent unreachable")
        else:
            if 0 <= free < free_space_threshold_bytes:
                problems["space"] = space_problem(free)
    return problems


def build_alert_text(new: list[str], recovered: list[str]) -> str:
    lines = ["🚨 <b>Мониторинг</b>", "⠀"]
    lines.extend(f"⚠️ {text}" for text in new)
    lines.extend(f"✅ {text}" for text in recovered)
    return "\n".join(lines)


async def announce(
    bot: Bot,
    config: AppConfig,
    known: dict[str, Problem],
    problems: dict[str, Problem],
) -> None:
    """Edge-triggered broadcast of the state diff to admins."""
    new = [problem.alert for key, problem in problems.items() if key not in known]
    recovered = [
        problem.recovered for key, problem in known.items() if key not in problems
    ]
    if new or recovered:
        await broadcaster.broadcast(
            bot, config.admin_ids, padded(build_alert_text(new, recovered))
        )


async def check_once(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    known: dict[str, Problem] | None,
) -> dict[str, Problem]:
    """One full monitoring pass; alerts admins about state transitions only.

    ``known=None`` is the baseline pass: the current state is recorded
    silently, so a deploy restart does not re-announce long-standing problems.
    """
    # a full pass re-tests every indexer, superseding search observations
    jackett.take_observed_errors()
    threshold = config.settings.alert_free_space_gb * 1024**3
    problems = await collect_problems(jackett, qbit, threshold)
    if known is None:
        if problems:
            logger.info("Monitor baseline: %s existing problems", len(problems))
        return problems
    await announce(bot, config, known, problems)
    return problems


async def recheck_problems(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    known: dict[str, Problem],
) -> dict[str, Problem]:
    """Fast pass between full ones: re-test only failed or suspect indexers.

    Probing an indexer makes Jackett re-attempt it, so this doubles as the
    recovery action after transient failures (expired session, tracker
    hiccup) while healthy indexers are left alone to spare the trackers.
    Failures observed by real user searches are confirmed here before they
    are announced, so a one-off glitch does not page the admins.
    """
    observed = jackett.take_observed_errors()
    problems = dict(known)
    ids = {
        key.removeprefix("indexer:") for key in known if key.startswith("indexer:")
    } | set(observed)
    try:
        for idx in sorted(ids):
            error = await jackett.probe_indexer(idx)
            key = f"indexer:{idx}"
            if error:
                problems.setdefault(key, indexer_problem(observed.get(idx, idx)))
            else:
                problems.pop(key, None)
    except JackettError:
        problems["jackett"] = JACKETT_PROBLEM
    if "space" in known and qbit is not None:
        threshold = config.settings.alert_free_space_gb * 1024**3
        try:
            free = await qbit.free_space()
        except QbittorrentError:
            # mirror collect_problems: an unreachable qbit is not a problem
            problems.pop("space", None)
        else:
            if not 0 <= free < threshold:
                problems.pop("space", None)
    await announce(bot, config, known, problems)
    return problems


async def monitor_loop(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    interval_seconds: int,
    recheck_interval_seconds: int = 0,
) -> None:
    """Probe indexers and disk space; edge-triggered admin alerts.

    Full passes run every ``interval_seconds``. While something is broken —
    or a real search just observed an indexer failure — the loop switches to
    ``recheck_interval_seconds`` and re-tests only the affected indexers, so
    breakage is confirmed and recovery announced quickly without hammering
    healthy trackers. Sleeps before the first pass so the baseline runs
    against a warmed-up Jackett, not the half-started state after a deploy.
    """
    loop = asyncio.get_running_loop()
    known: dict[str, Problem] | None = None
    next_full = loop.time() + interval_seconds
    while True:
        pending = known is not None and (bool(known) or bool(jackett.observed_errors()))
        fast = recheck_interval_seconds > 0 and pending
        await asyncio.sleep(recheck_interval_seconds if fast else interval_seconds)
        if (
            known is None
            or not fast
            # while Jackett itself is down the aggregate probe fails instantly,
            # so a full pass is the cheapest recovery check
            or "jackett" in known
            or loop.time() >= next_full
        ):
            known = await check_once(bot, jackett, qbit, config, known)
            next_full = loop.time() + interval_seconds
        else:
            known = await recheck_problems(bot, jackett, qbit, config, known)
