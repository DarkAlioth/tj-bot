import io
import re

import pytest
from PIL import Image

from tj_bot.services.enrich.images import (
    ImageError,
    generated_cover,
    mosaic,
    prepare_photo,
    sniff_format,
)


def _png(size: tuple[int, int], color: tuple[int, ...], mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format="PNG")
    return out.getvalue()


def _jpeg(img: Image.Image, **kwargs: object) -> bytes:
    out = io.BytesIO()
    img.save(out, format="JPEG", **kwargs)
    return out.getvalue()


def test_prepare_photo_downscales_large_and_strips_exif() -> None:
    img = Image.new("RGB", (3000, 4500), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW
    photo = prepare_photo(_jpeg(img, exif=exif))
    assert sniff_format(photo) == "jpeg"
    with Image.open(io.BytesIO(photo)) as result:
        assert result.size == (1280, 853)  # orientation applied: landscape now
        assert result.mode == "RGB"
        assert result.getexif().get(0x0112) is None


def test_prepare_photo_keeps_small_webp_and_converts_to_jpeg() -> None:
    out = io.BytesIO()
    Image.new("RGB", (450, 675), (10, 120, 200)).save(out, format="WEBP")
    photo = prepare_photo(out.getvalue())
    with Image.open(io.BytesIO(photo)) as result:
        assert result.size == (450, 675) and result.format == "JPEG"


def test_prepare_photo_flattens_transparency_on_dark_background() -> None:
    photo = prepare_photo(_png((40, 40), (255, 255, 255, 0), mode="RGBA"))
    with Image.open(io.BytesIO(photo)) as result:
        pixel = result.getpixel((20, 20))
        assert isinstance(pixel, tuple) and all(channel < 40 for channel in pixel)


def test_prepare_photo_clamps_extreme_aspect_ratio() -> None:
    photo = prepare_photo(_png((3000, 50), (0, 0, 255)))
    with Image.open(io.BytesIO(photo)) as result:
        width, height = result.size
        assert width / height <= 20.0


def test_prepare_photo_rejects_garbage() -> None:
    with pytest.raises(ImageError):
        prepare_photo(b"<html>not an image</html>")
    with pytest.raises(ImageError):
        mosaic(b"")


def test_mosaic_is_72_hex_row_major_12bit() -> None:
    img = Image.new("RGB", (400, 600), (0, 0, 0))
    img.paste((255, 0, 0), (0, 0, 400, 100))  # top row red
    colors = mosaic(_png((1, 1), (0, 0, 0)))
    assert len(colors) == 72 and re.fullmatch(r"[0-9a-f]{72}", colors)
    out = io.BytesIO()
    img.save(out, format="PNG")
    colors = mosaic(out.getvalue())
    cells = [colors[i : i + 3] for i in range(0, 72, 3)]
    assert cells[:4] == ["f00"] * 4 and cells[4:] == ["000"] * 20
    assert len(mosaic(out.getvalue(), cols=2, rows=3)) == 18


def test_mosaic_quantises_each_channel_to_a_nibble() -> None:
    assert mosaic(_png((8, 12), (0x12, 0xAB, 0xFF))) == "1af" * 24


def test_sniff_format_detects_webp_png_gif() -> None:
    assert sniff_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_format(_png((1, 1), (0, 0, 0))) == "png"
    assert sniff_format(b"GIF89a") == "gif"
    assert sniff_format(b"\x00\x01") is None


def test_generated_cover_is_a_600x900_gradient_with_text() -> None:
    cover = generated_cover("Дюна: Часть вторая / Dune: Part Two (2024) BDRemux", 262)
    assert sniff_format(cover) == "jpeg"
    with Image.open(io.BytesIO(cover)) as img:
        assert img.size == (600, 900) and img.mode == "RGB"
        top = img.getpixel((300, 20))
        bottom = img.getpixel((300, 880))
        assert isinstance(top, tuple) and isinstance(bottom, tuple)
        assert sum(top) > sum(bottom)  # darker towards the bottom
        # hue 262 is violet: blue dominates red and green at the top
        assert top[2] > top[0] > top[1]
    # the title area differs from an empty cover: text was drawn
    assert generated_cover("", 262) != cover
    assert len(cover) < 200_000
