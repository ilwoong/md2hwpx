"""Images for the HWPX: their data and format, and the size a picture takes on the page."""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

from PIL import Image

from md2hwpx.diagram import LANGS

# File names render_diagrams gives the PNGs it renders.
_DIAGRAM_IMAGE = re.compile(rf"^({'|'.join(LANGS)})-[0-9a-f]{{12}}\.png$")
# An image's width / height attribute: percent of the page body, or millimetres.
_LENGTH = re.compile(r"(\d+(?:\.\d+)?)(%|mm)")
# Shown in place of an image whose file is not there, or that cannot be read.
MISSING_IMAGE = Path(__file__).resolve().parent / "static" / "missing-image.png"
SVG_TIMEOUT = 60  # seconds for rsvg-convert
HWPUNIT_PER_PIXEL = 75  # 7200 HWPUNIT per inch at 96 DPI
HWPUNIT_PER_MM = 7200 / 25.4  # 7200 HWPUNIT per inch


class ImageError(Exception):
    """An image that cannot go in the HWPX. The message is a Korean clause ending in "-어" / "-해",
    which a warning goes on from ("... 대체 이미지를 넣었습니다")."""


def image_format(data: bytes) -> str | None:
    """The format of a PNG, JPEG or GIF image, as the HWPX names it, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def image_pixels(data: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels of a PNG, GIF or JPEG image, or None."""
    kind = image_format(data)
    if kind == "png" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if kind == "gif":
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if kind == "jpg":
        i = 2
        while i + 9 <= len(data) and data[i] == 0xFF:
            marker = data[i + 1]
            # Start-of-frame markers hold the size; C4 / C8 / CC are other tables.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
            i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    return None


def picture_data(path: Path) -> tuple[bytes, str]:
    """The image at *path* as (data, format) to put in the HWPX: PNG, JPEG and GIF as they are, any other
    image as PNG (SVG by rsvg-convert, the rest by Pillow). Raises ImageError if it cannot be either."""
    if not path.is_file():
        raise ImageError("이미지 파일이 없어")
    data = path.read_bytes()
    kind = image_format(data)
    if kind is not None:
        return data, kind
    if path.suffix.lower() == ".svg" or b"<svg" in data[:4096]:
        return _svg_to_png(data), "png"
    try:
        with Image.open(io.BytesIO(data)) as image:
            png = io.BytesIO()
            image.convert("RGBA").save(png, "PNG")  # RGBA: PNG cannot hold every mode (CMYK, ...)
    # UnidentifiedImageError is an OSError; DecompressionBombError, for one too large to open, is not.
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageError("이미지 형식을 읽을 수 없어") from None
    return png.getvalue(), "png"


def _svg_to_png(svg: bytes) -> bytes:
    """*svg* drawn as PNG at 96 DPI by rsvg-convert. Given on stdin, it has no base to load files from."""
    try:
        result = subprocess.run(["rsvg-convert", "--format=png"], input=svg, capture_output=True, timeout=SVG_TIMEOUT)
    except FileNotFoundError:
        raise ImageError("SVG 를 변환할 rsvg-convert 가 없어") from None
    except subprocess.TimeoutExpired:
        raise ImageError("SVG 변환이 너무 오래 걸려") from None
    if result.returncode != 0 or image_format(result.stdout) != "png":
        raise ImageError("SVG 를 변환하지 못해")
    return result.stdout


def length(value: str | None, whole: int) -> int | None:
    """A width / height attribute ("50%" of *whole*, or "80mm") in HWPUNIT, or None if absent, zero or neither."""
    match = _LENGTH.fullmatch(value or "")
    if match is None:
        return None
    number = float(match[1])
    hwpunit = round(whole * number / 100 if match[2] == "%" else number * HWPUNIT_PER_MM)
    return hwpunit or None


def picture_size(
    image_path: Path, data: bytes, max_width: int, max_height: int, width: int | None = None, height: int | None = None
) -> tuple[int, int] | None:
    """Picture (width, height) in HWPUNIT within *max_width* x *max_height*, or None if the image size is unknown.

    The *width* / *height* asked for is used, the other side following the aspect ratio. Without either,
    a diagram is as wide as *max_width*; any other image keeps its size at 96 DPI.
    Any of them is scaled down, keeping its aspect ratio, to fit.
    """
    pixels = image_pixels(data)
    if pixels is None or 0 in pixels:
        return None
    ratio = pixels[1] / pixels[0]
    if width is None and height is None:
        width = max_width if _DIAGRAM_IMAGE.match(image_path.name) else pixels[0] * HWPUNIT_PER_PIXEL
    if width is None:
        width = height / ratio
    if height is None:
        height = width * ratio
    scale = min(1.0, max_width / width, max_height / height)
    return round(width * scale), round(height * scale)
