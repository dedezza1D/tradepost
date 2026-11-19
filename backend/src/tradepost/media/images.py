"""Validation and normalisation of uploaded photos.

Uploads are untrusted: the type is decided by the file's magic bytes (never the name or the
declared content type), dimensions are checked before decoding pixels, and every photo is
re-encoded. Re-encoding drops all metadata, including EXIF GPS coordinates, and any payload
appended to the original file.
"""

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from tradepost.errors import AppError

FULL_MAX_SIDE = 1600
THUMB_MAX_SIDE = 400
_WEBP_QUALITY = 82

_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImage(AppError):
    code = "invalid_image"


@dataclass(frozen=True)
class ProcessedImage:
    full: bytes
    thumb: bytes
    width: int
    height: int


def sniff_format(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _encode(image: Image.Image, max_side: int) -> bytes:
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    copy.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
    return out.getvalue()


OG_SIZE = (1200, 630)


def share_preview(data: bytes) -> bytes:
    """A 1200x630 JPEG for link previews (Open Graph). Messaging apps handle JPEG everywhere,
    WebP not always. Input is a photo this app already re-encoded, so it is trusted."""
    with Image.open(io.BytesIO(data)) as image:
        fitted = ImageOps.fit(image.convert("RGB"), OG_SIZE, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    fitted.save(out, format="JPEG", quality=80, optimize=True, progressive=True)
    return out.getvalue()


MODEL_MAX_SIDE = 768


def model_input(data: bytes, *, max_bytes: int, max_pixels: int) -> bytes:
    """An uploaded photo as a model sees it: validated like any upload, re-encoded as a JPEG of
    at most 768 px (vision models slow down with image size; detail past that is noise)."""
    rgb = _decode(data, max_bytes=max_bytes, max_pixels=max_pixels)
    rgb.thumbnail((MODEL_MAX_SIDE, MODEL_MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=85)
    return out.getvalue()


def brightness(data: bytes) -> float:
    """Mean luminance from 0 (black) to 1 (white) of an image this app already stored."""
    with Image.open(io.BytesIO(data)) as image:
        small = image.convert("L")
        small.thumbnail((64, 64))
        histogram = small.histogram()
    total = sum(histogram) or 1
    return sum(level * count for level, count in enumerate(histogram)) / total / 255


def process_image(data: bytes, *, max_bytes: int, max_pixels: int) -> ProcessedImage:
    rgb = _decode(data, max_bytes=max_bytes, max_pixels=max_pixels)
    return ProcessedImage(
        full=_encode(rgb, FULL_MAX_SIDE),
        thumb=_encode(rgb, THUMB_MAX_SIDE),
        width=rgb.width,
        height=rgb.height,
    )


def _decode(data: bytes, *, max_bytes: int, max_pixels: int) -> Image.Image:
    if len(data) > max_bytes:
        raise InvalidImage(f"Photos must be at most {max_bytes // (1024 * 1024)} MB.")
    sniffed = sniff_format(data)
    if sniffed is None:
        raise InvalidImage("Only JPEG, PNG and WebP photos are accepted.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format != sniffed or probe.format not in _FORMATS:
                raise InvalidImage("The file content does not match a supported image type.")
            width, height = probe.size
            if width * height > max_pixels or width < 1 or height < 1:
                raise InvalidImage("The photo dimensions are too large.")
            probe.verify()
        # verify() leaves the image unusable; decode again for real.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            oriented = ImageOps.exif_transpose(image) or image
            rgb = oriented.convert("RGB")
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise InvalidImage("The photo could not be read.") from None
    return rgb
