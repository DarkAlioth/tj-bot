import asyncio
import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot

from tj_bot.config import AppConfig
from tj_bot.services import broadcaster
from tj_bot.services.formatting import format_size, padded
from tj_bot.services.hub_views import HubStatus
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


@dataclass(frozen=True)
class IndexerStatus:
    """One row of Jackett's indexer report."""

    id: str
    name: str
    ok: bool
    error: str | None
    results: int


def indexer_status(raw: dict[str, Any]) -> IndexerStatus:
    idx = str(raw.get("ID") or raw.get("Name") or "?")
    error = raw.get("Error")
    return IndexerStatus(
        id=idx,
        name=str(raw.get("Name") or idx),
        ok=not error,
        error=str(error) if error else None,
        results=int(raw.get("Results") or 0),
    )


@dataclass
class MonitorState:
    """The monitor's last pass, shared with the dashboard widget.

    ``indexers`` is the snapshot of the last successful full probe,
    ``checked_at`` its unix time (0 before the baseline pass). ``problems``
    is the current view, kept up to date by partial rechecks too.
    """

    problems: dict[str, Problem] = field(default_factory=dict)
    indexers: list[IndexerStatus] = field(default_factory=list)
    checked_at: int = 0
    # None until qBittorrent was probed at least once
    qbit_ok: bool | None = None
    probe: asyncio.Task[dict[str, Problem]] | None = field(default=None, repr=False)

    @property
    def known(self) -> dict[str, Problem] | None:
        """What ``check_once`` compares against; ``None`` until the baseline."""
        return self.problems if self.checked_at else None

    @property
    def probe_running(self) -> bool:
        return self.probe is not None

    def hub_status(self) -> HubStatus | None:
        """The hub's status line; None before the baseline pass."""
        if not self.checked_at:
            return None
        return HubStatus(
            jackett_ok="jackett" not in self.problems,
            qbit_ok=self.qbit_ok is not False,
            at=datetime.datetime.fromtimestamp(self.checked_at, datetime.UTC),
        )


async def collect_problems(
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    free_space_threshold_bytes: int,
    state: MonitorState | None = None,
) -> dict[str, Problem]:
    """Current problems keyed by a stable id (``indexer:<id>``/jackett/space).

    With ``state`` the indexer report is kept as the snapshot the dashboard
    shows; an unreachable Jackett leaves the previous snapshot in place.
    """
    problems: dict[str, Problem] = {}
    try:
        indexers = [indexer_status(raw) for raw in await jackett.indexers()]
    except JackettError:
        problems["jackett"] = JACKETT_PROBLEM
    else:
        if state is not None:
            state.indexers = indexers
        for indexer in indexers:
            if not indexer.ok:
                problems[f"indexer:{indexer.id}"] = indexer_problem(indexer.name)
    if qbit is not None:
        try:
            free = await qbit.free_space()
        except QbittorrentError:
            # the qbit host may be legitimately powered off — /health covers it
            logger.debug("Skipping free-space check: qBittorrent unreachable")
            if state is not None:
                state.qbit_ok = False
        else:
            if state is not None:
                state.qbit_ok = True
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
    state: MonitorState | None = None,
) -> dict[str, Problem]:
    """One full monitoring pass; alerts admins about state transitions only.

    ``known=None`` is the baseline pass: the current state is recorded
    silently, so a deploy restart does not re-announce long-standing problems.
    """
    # a full pass re-tests every indexer, superseding search observations
    jackett.take_observed_errors()
    threshold = config.settings.alert_free_space_gb * 1024**3
    problems = await collect_problems(jackett, qbit, threshold, state)
    if known is None:
        if problems:
            logger.info("Monitor baseline: %s existing problems", len(problems))
        return problems
    await announce(bot, config, known, problems)
    return problems


async def _full_pass(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    state: MonitorState,
) -> dict[str, Problem]:
    problems = await check_once(bot, jackett, qbit, config, state.known, state)
    state.problems = problems
    state.checked_at = int(time.time())
    return problems


async def probe_now(
    bot: Bot,
    jackett: JackettClient,
    qbit: QbittorrentClient | None,
    config: AppConfig,
    state: MonitorState,
) -> dict[str, Problem]:
    """A full pass on demand (up to ~30 s); concurrent callers share one probe."""
    if state.probe is not None:
        return await state.probe
    state.probe = asyncio.create_task(_full_pass(bot, jackett, qbit, config, state))
    try:
        return await state.probe
    finally:
        state.probe = None


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
    state: MonitorState | None = None,
) -> None:
    """Probe indexers and disk space; edge-triggered admin alerts.

    Full passes run every ``interval_seconds``. While something is broken —
    or a real search just observed an indexer failure — the loop switches to
    ``recheck_interval_seconds`` and re-tests only the affected indexers, so
    breakage is confirmed and recovery announced quickly without hammering
    healthy trackers. Sleeps before the first pass so the baseline runs
    against a warmed-up Jackett, not the half-started state after a deploy.

    ``state`` is the single source of truth for the known problems, so an
    on-demand ``probe_now`` between passes is not re-announced here.
    """
    if state is None:
        state = MonitorState()
    loop = asyncio.get_running_loop()
    next_full = loop.time() + interval_seconds
    while True:
        known = state.known
        pending = known is not None and (bool(known) or bool(jackett.observed_errors()))
        fast = recheck_interval_seconds > 0 and pending
        await asyncio.sleep(recheck_interval_seconds if fast else interval_seconds)
        known = state.known
        if (
            known is None
            or not fast
            # while Jackett itself is down the aggregate probe fails instantly,
            # so a full pass is the cheapest recovery check
            or "jackett" in known
            or loop.time() >= next_full
        ):
            await probe_now(bot, jackett, qbit, config, state)
            next_full = loop.time() + interval_seconds
        else:
            state.problems = await recheck_problems(bot, jackett, qbit, config, known)
