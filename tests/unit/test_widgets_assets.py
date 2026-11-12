import secrets

import pytest

from tj_bot.widgets.assets import (
    MAX_COMPRESSED_BYTES,
    MAX_HTML_BYTES,
    WidgetTooLargeError,
    check_widget_size,
    load_widget,
)


def test_load_widget_reads_package_html_and_caches() -> None:
    html = load_widget("search")

    assert "<script>" in html
    assert load_widget("search") is html


def test_load_widget_unknown_name_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_widget("no-such-widget")


def test_check_widget_size_reports_utf8_and_compressed_sizes() -> None:
    raw, compressed = check_widget_size("<b>é</b>")

    assert raw == 9
    assert 0 < compressed <= MAX_COMPRESSED_BYTES


def test_check_widget_size_rejects_raw_overflow() -> None:
    with pytest.raises(WidgetTooLargeError):
        check_widget_size("a" * (MAX_HTML_BYTES + 1))


def test_check_widget_size_rejects_compressed_overflow() -> None:
    # random hex does not compress below half its length: 10000 chars stay
    # under the raw limit but above the compressed one
    html = secrets.token_hex(5000)

    with pytest.raises(WidgetTooLargeError):
        check_widget_size(html)
