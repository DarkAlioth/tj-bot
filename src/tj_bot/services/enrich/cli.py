"""Manual check: ``python -m tj_bot.services.enrich.cli <url> [...]``.

Prints the parsed TorrentMeta as JSON with timings; ``--save`` keeps the fetched
HTML (for fixtures). Tapochek credentials come from TAPOCHEK_USERNAME /
TAPOCHEK_PASSWORD, the solver from FLARESOLVERR_URL (used on a challenge only).
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

from tj_bot.services.enrich import flaresolverr
from tj_bot.services.enrich.fetch import fetch_page, host_of
from tj_bot.services.enrich.model import ChallengeError, ScrapeError
from tj_bot.services.enrich.parsers import parser_for
from tj_bot.services.enrich.parsers.tapochek import HOSTS as TAPOCHEK_HOSTS
from tj_bot.services.enrich.tapochek import TapochekCredentials, fetch_tapochek
from tj_bot.services.enrich.title_parser import parse_title


def _credentials() -> TapochekCredentials | None:
    username = os.environ.get("TAPOCHEK_USERNAME")
    password = os.environ.get("TAPOCHEK_PASSWORD")
    return TapochekCredentials(username, password) if username and password else None


async def _fetch(
    session: aiohttp.ClientSession, url: str, proxy: str | None, solver: str | None
) -> tuple[str, str | None]:
    """Page HTML and the solver's user agent when FlareSolverr was needed."""
    if host_of(url) in TAPOCHEK_HOSTS:
        return await fetch_tapochek(session, url, _credentials(), proxy=proxy), None
    try:
        return await fetch_page(session, url, proxy=proxy), None
    except ChallengeError:
        if not solver:
            raise
        solution = await flaresolverr.solve(session, solver, url, proxy=proxy)
        return solution.html, solution.user_agent


async def run(args: argparse.Namespace) -> int:
    parser = parser_for(args.url)
    if parser is None:
        print(json.dumps({"error": f"no parser for {args.url}"}), file=sys.stderr)
        return 2
    result: dict[str, object] = {}
    async with aiohttp.ClientSession() as session:
        started = time.perf_counter()
        try:
            html_text, user_agent = await _fetch(
                session, args.url, args.proxy, args.solver
            )
            result["fetch_s"] = round(time.perf_counter() - started, 3)
            if args.save:
                await asyncio.to_thread(
                    Path(args.save).write_text, html_text, encoding="utf-8"
                )
            meta = parser(html_text, args.url)
        except ScrapeError as exc:
            print(
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr
            )
            return 1
        result["meta"] = dataclasses.asdict(meta)
        result["title"] = dataclasses.asdict(parse_title(meta.extra.get("title", "")))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape one tracker page into JSON")
    ap.add_argument("url")
    ap.add_argument("--proxy", default=os.environ.get("SCRAPE_PROXY_URL") or None)
    ap.add_argument("--solver", default=os.environ.get("FLARESOLVERR_URL") or None)
    ap.add_argument("--save", help="write the fetched HTML to this file")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
