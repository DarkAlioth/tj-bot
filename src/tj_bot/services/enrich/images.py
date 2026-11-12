"""Poster download, Telegram-ready processing, ambient mosaic and generated covers."""

import asyncio
import colorsys
import io
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

from tj_bot.services.enrich import fetch
from tj_bot.services.enrich.model import NotFoundError, ScrapeError

MAX_SIDE = 1280
MAX_RATIO = 20.0  # Telegram sendPhoto: width/height ratio <= 20
JPEG_QUALITY = 85
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
# Hosts that answer 404 to any Referer but 200 without one (fastpic, verified).
NO_REFERER_HOSTS = frozenset({"fastpic.org", "fastpic.ru"})
MOSAIC_COLS = 4
MOSAIC_ROWS = 6
COVER_SIZE = (600, 900)
# Pillow's bundled default font (Aileron) has no Cyrillic; the runtime image
# installs fonts-dejavu-core for the generated covers.
COVER_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
_CHUNK = 64 * 1024


class ImageError(ScrapeError):
    """The image could not be downloaded or decoded."""


def sniff_format(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _wants_referer(url: str, referer: str | None) -> bool:
    host = urlsplit(url).hostname or ""
    return bool(referer) and not any(
        host == h or host.endswith("." + h) for h in NO_REFERER_HOSTS
    )


async def _read_limited(resp: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    if resp.content_length is not None and resp.content_length > max_bytes:
        msg = f"image too large: {resp.content_length} > {max_bytes}"
        raise ImageError(msg)
    buf = bytearray()
    async for chunk in resp.content.iter_chunked(_CHUNK):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            msg = f"image exceeds {max_bytes} bytes"
            raise ImageError(msg)
    return bytes(buf)


async def download_image(
    session: aiohttp.ClientSession,
    url: str,
    *,
    proxy: str | None = None,
    max_bytes: int = 5_000_000,
    retries: int = 3,
    referer: str | None = None,
    timeout_seconds: float = 20.0,
    user_agent: str | None = None,
) -> bytes:
    """Download a JPEG/PNG/WebP/GIF with the same retry policy as ``fetch_page``.

    ``referer`` is the topic URL (hotlink protection); on 403/404 with a
    Referer the request is repeated once without it, and hosts known to
    reject any Referer never get one.
    """
    fetch.check_proxy(proxy)
    host = fetch.host_of(url) or url
    base_headers = fetch.request_headers(user_agent)
    base_headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
    send_referer = _wants_referer(url, referer)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    last_error = "no attempts"
    attempt = 1
    while attempt <= retries:
        headers = dict(base_headers)
        if send_referer and referer:
            headers["Referer"] = referer
        await fetch.throttle.wait(host)
        try:
            async with session.get(
                url, headers=headers, proxy=proxy, timeout=timeout
            ) as resp:
                if resp.status in (403, 404) and send_referer:
                    send_referer = False  # retry at once without the Referer
                    continue
                if resp.status in (404, 410):
                    msg = f"{resp.status} for {url}"
                    raise NotFoundError(msg)
                if resp.status >= 500 or resp.status == 429:
                    last_error = f"HTTP {resp.status}"
                elif resp.status >= 300:
                    msg = f"HTTP {resp.status} for {url}"
                    raise ImageError(msg)
                else:
                    data = await _read_limited(resp, max_bytes)
                    if sniff_format(data) is None or (
                        resp.content_type not in ALLOWED_TYPES
                        and resp.content_type != "application/octet-stream"
                    ):
                        msg = f"not an image: {resp.content_type} at {url}"
                        raise ImageError(msg)
                    return data
        except fetch.RETRY_ERRORS as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            await asyncio.sleep(fetch.backoff_delay(attempt))
        attempt += 1
    msg = f"giving up on {url} after {retries} attempts: {last_error}"
    raise ImageError(msg)


def _open_rgb(data: bytes) -> Image.Image:
    """Decode, apply EXIF orientation, flatten alpha onto dark grey, return RGB."""
    try:
        with Image.open(io.BytesIO(data)) as raw:
            raw.load()
            img = ImageOps.exif_transpose(raw) or raw
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                rgba = img.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (24, 24, 24, 255))
                background.alpha_composite(rgba)
                return background.convert("RGB")
            return img.convert("RGB")
    except (OSError, ValueError) as exc:
        msg = f"cannot decode image: {exc}"
        raise ImageError(msg) from exc


def _clamp_ratio(img: Image.Image) -> Image.Image:
    width, height = img.size
    if max(width, height) <= MAX_RATIO * min(width, height):
        return img
    if width > height:
        new_width = int(height * MAX_RATIO)
        left = (width - new_width) // 2
        return img.crop((left, 0, left + new_width, height))
    new_height = int(width * MAX_RATIO)
    top = (height - new_height) // 2
    return img.crop((0, top, width, top + new_height))


def _to_jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def prepare_photo(data: bytes) -> bytes:
    """JPEG q85, longest side <= 1280, EXIF stripped, ratio <= 20 (sendPhoto limits)."""
    img = _clamp_ratio(_open_rgb(data))
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return _to_jpeg(img)


def mosaic(data: bytes, cols: int = MOSAIC_COLS, rows: int = MOSAIC_ROWS) -> str:
    """cols×rows box-filtered thumbnail as 12-bit ``rgb`` hex per cell, row-major.

    4×6 cells give the 72-character string the search widget expects.
    """
    small = _open_rgb(data).resize((cols, rows), Image.Resampling.BOX)
    pixels = small.tobytes()
    return "".join(
        f"{pixels[i] >> 4:x}{pixels[i + 1] >> 4:x}{pixels[i + 2] >> 4:x}"
        for i in range(0, len(pixels), 3)
    )


def _hsl(hue: int, saturation: float, lightness: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(hue / 360, lightness, saturation)
    return int(r * 255), int(g * 255), int(b * 255)


def _cover_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in COVER_FONTS:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _wrap(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.getlength(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def generated_cover(title: str, hue: int) -> bytes:
    """600×900 JPEG: category-hue gradient plus the title, for posterless releases."""
    width, height = COVER_SIZE
    gradient = Image.linear_gradient("L").resize((width, height))
    img = ImageOps.colorize(
        gradient, black=_hsl(hue, 0.45, 0.30), white=_hsl(hue, 0.55, 0.08)
    )
    draw = ImageDraw.Draw(img)
    font = _cover_font(44)
    lines = _wrap(title, font, width - 2 * 48)[:7]
    line_height = 54
    y = height - 72 - line_height * len(lines)
    for line in lines:
        draw.text((48 + 2, y + 2), line, font=font, fill=(0, 0, 0))
        draw.text((48, y), line, font=font, fill=(245, 245, 245))
        y += line_height
    return _to_jpeg(img)
