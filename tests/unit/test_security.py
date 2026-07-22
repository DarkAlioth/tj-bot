import pytest

from tj_bot.services.jackett import (
    JackettClient,
    UntrustedDownloadError,
    parse_result,
    safe_torrent_filename,
)


def make_client(base_url: str = "http://jackett:9117") -> JackettClient:
    return JackettClient(base_url=base_url, api_key="k")


@pytest.mark.parametrize(
    "url",
    [
        "http://jackett:9117/dl/rutracker/?apikey=x",
        "http://jackett:9117/anything",
    ],
)
def test_trusted_urls_allowed(url: str) -> None:
    assert make_client()._is_trusted_url(url) is True  # noqa: SLF001


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8181/api/v2/torrents/info",  # qBittorrent in LAN
        "http://db:5432/",  # internal service
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://jackett:9118/dl",  # wrong port
        "https://jackett:9117/dl",  # wrong scheme
        "file:///etc/passwd",  # non-http scheme
        "http://evil.example.com/x",  # external host
    ],
)
def test_untrusted_urls_rejected(url: str) -> None:
    assert make_client()._is_trusted_url(url) is False  # noqa: SLF001


async def test_download_blocks_untrusted_before_any_request() -> None:
    client = make_client()
    with pytest.raises(UntrustedDownloadError):
        await client.download("http://10.0.0.5:8181/secret")


def test_parse_result_escapes_all_rendered_fields() -> None:
    torrent = {
        "Title": "Movie",
        "Description": None,
        "CategoryDesc": "Movies <b>HD</b>",
        "PublishDate": "2026-07-14T00:00:00+00:00",
        "TrackerId": "tracker&<script>",
        "Details": 'https://x/"><b>',
        "Link": "http://jackett:9117/dl/1",
        "Seeders": 1,
        "Peers": 0,
        "Size": 100,
    }
    data = parse_result(torrent)
    assert data is not None
    assert "<" not in data.category and "&lt;" in data.category
    assert "<script>" not in data.tracker
    assert '"><b>' not in data.details_url


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("../../etc/passwd", "etc_passwd.torrent"),
        ("a/b\\c:d", "a_b_c_d.torrent"),
        ("  ...  ", "torrent.torrent"),
        ("Normal Title 1080p", "Normal Title 1080p.torrent"),
    ],
)
def test_safe_torrent_filename(title: str, expected: str) -> None:
    assert safe_torrent_filename(title) == expected
