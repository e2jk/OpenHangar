"""Shared photo decoding and metadata removal; storage has no model dependency."""

import io
import secrets
import warnings
from pathlib import Path

from flask_babel import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000


class PhotoError(ValueError):
    """A photo cannot be accepted; its message is suitable for a form error."""


def clean_photo(upload: FileStorage | None) -> bytes:
    if upload is None or not upload.filename:
        raise PhotoError(_("Choose a photo to upload."))
    raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise PhotoError(_("Photos must be at most 10 MB."))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if (
                    original.format not in {"JPEG", "PNG", "WEBP"}
                    or original.width * original.height > MAX_PHOTO_PIXELS
                ):
                    raise ValueError("Unsupported or oversized image")
                original.load()
                upright = ImageOps.exif_transpose(original)
                rgb = upright.convert("RGB")
                rgb.thumbnail((2000, 2000))
                # Copy pixels into a clean image: no EXIF, GPS, comments or profiles.
                clean = Image.new("RGB", rgb.size)
                clean.paste(rgb)
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=85)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise PhotoError(
            _("Upload a valid JPEG, PNG or WebP photo (up to 20 megapixels).")
        ) from exc
    return output.getvalue()


def store(folder: Path, data: bytes) -> str:
    """Store cleaned bytes under a random name; the caller owns the DB transaction."""
    filename = secrets.token_hex(16) + ".jpg"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    return filename


# JPEG APP1 holds EXIF (GPS position, camera, time) and XMP; APP13 holds
# IPTC. PNG keeps the same in its eXIf and text chunks.
_JPEG_METADATA_MARKERS = frozenset({0xE1, 0xED})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_METADATA_CHUNKS = frozenset({b"eXIf", b"tEXt", b"zTXt", b"iTXt"})


def _jpeg_without_metadata(data: bytes) -> bytes | None:
    out, i = bytearray(data[:2]), 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xDA:  # start of scan: the image data follows
            out += data[i:]
            return bytes(out)
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker not in _JPEG_METADATA_MARKERS:
            out += data[i : i + 2 + length]
        i += 2 + length
    return None


def _png_without_metadata(data: bytes) -> bytes | None:
    out, i = bytearray(_PNG_SIGNATURE), len(_PNG_SIGNATURE)
    while i + 12 <= len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        kind = data[i + 4 : i + 8]
        end = i + 12 + length
        if kind not in _PNG_METADATA_CHUNKS:
            out += data[i:end]
        if kind == b"IEND":
            return bytes(out)
        i = end
    return None


def strip_metadata(path: str) -> None:
    """Remove EXIF/XMP/IPTC (GPS position included) from a stored JPEG or
    PNG document without re-encoding it, so a scan keeps its quality. Any
    other file, or one that does not parse, is left as it is."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith(b"\xff\xd8"):
        cleaned = _jpeg_without_metadata(data)
    elif data.startswith(_PNG_SIGNATURE):
        cleaned = _png_without_metadata(data)
    else:
        return
    if cleaned is not None and cleaned != data:
        with open(path, "wb") as fh:
            fh.write(cleaned)
