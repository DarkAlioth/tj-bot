"""Widget HTML shipped as package data in ``tj_bot/widgets/html``."""

import zlib
from functools import cache
from importlib import resources

MAX_HTML_BYTES = 32_768
MAX_COMPRESSED_BYTES = 4_096


class WidgetTooLargeError(ValueError):
    """The widget HTML exceeds the Bot API limits (32768 raw / 4096 zlib bytes)."""


def check_widget_size(html: str) -> tuple[int, int]:
    """Return ``(raw, compressed)`` UTF-8 byte sizes, raising above the limits."""
    encoded = html.encode("utf-8")
    raw = len(encoded)
    compressed = len(zlib.compress(encoded, level=9))
    if raw > MAX_HTML_BYTES or compressed > MAX_COMPRESSED_BYTES:
        raise WidgetTooLargeError(
            f"widget is {raw} bytes raw / {compressed} compressed, "
            f"limits are {MAX_HTML_BYTES} / {MAX_COMPRESSED_BYTES}"
        )
    return raw, compressed


@cache
def load_widget(name: str) -> str:
    """Read ``html/<name>.html`` from the package, checked against the size limits."""
    html = (
        resources.files("tj_bot.widgets")
        .joinpath("html", f"{name}.html")
        .read_text(encoding="utf-8")
    )
    check_widget_size(html)
    return html
