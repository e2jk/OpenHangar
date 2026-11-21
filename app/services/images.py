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
