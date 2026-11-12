"""Tracker topic-page parsers sharing the TorrentMeta interface."""

from collections.abc import Callable

from tj_bot.services.enrich.fetch import host_of
from tj_bot.services.enrich.model import TorrentMeta
from tj_bot.services.enrich.parsers import (
    byrutor,
    megapeer,
    nnmclub,
    pctorrent,
    rutor,
    rutracker,
    tapochek,
)

Parser = Callable[[str, str], TorrentMeta]

PARSERS: dict[str, Parser] = {
    host: module.parse
    for module in (rutracker, nnmclub, tapochek, rutor, megapeer, pctorrent, byrutor)
    for host in module.HOSTS
}


def parser_for(url: str) -> Parser | None:
    """The parser for the details URL's host, or None for an unknown tracker."""
    return PARSERS.get(host_of(url))
