"""Re-encode photos stored before every upload was cleaned (`flask clean-photos`).

Aircraft photos (also on the public showcase) and flight counter/fuel photos
used to be stored as uploaded, GPS position and other EXIF metadata included.
This walks every stored one: a JPEG without metadata is left alone, any
other is re-encoded through services.images (orientation applied, JPEG
without metadata) under a new name and its database row updated. Without
--apply it only reports. The originals are deleted, since they hold the very
metadata being removed; --keep-originals moves them to _trash/ instead.
HEIC, GIF and unreadable files can't be decoded: they are reported, untouched.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from models import AircraftPhoto, Flight, db
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from services.images import PhotoError, clean_photo
from services.upload_layout import TRASH_ROOT

FLIGHT_PHOTO_COLUMNS = (
    "flight_counter_photo",
    "engine_counter_photo",
    "fuel_photo",
    "flight_counter_photo_preflight",
    "engine_counter_photo_preflight",
)


@dataclass
class Report:
    clean: list[str] = field(default_factory=list)
    to_clean: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _already_clean(path: Path) -> bool | None:
    """True for a JPEG without metadata, False for anything to re-encode,
    None when Pillow can't read it (HEIC, GIF frames aside, broken files)."""
    try:
        with Image.open(path) as img:
            if img.format == "GIF":
                return None
            has_metadata = bool(img.getexif()) or any(
                key in img.info for key in ("exif", "icc_profile", "comment", "xmp")
            )
            return img.format == "JPEG" and not has_metadata
    except (UnidentifiedImageError, OSError):
        return None


def _cleaned_bytes(path: Path) -> bytes | None:
    with path.open("rb") as stream:
        try:
            return clean_photo(FileStorage(stream=stream, filename=path.name))
        except PhotoError:
            return None


def _retire(folder: Path, relpath: str, keep: bool) -> None:
    source = folder / relpath
    if not source.exists():
        return
    if keep:
        target = folder / TRASH_ROOT / "photos" / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    else:
        source.unlink()


def _photos(folder: Path) -> list[tuple[object, str, str]]:
    """(row, column, relpath) of every stored photo."""
    rows: list[tuple[object, str, str]] = [
        (photo, "filename", photo.filename) for photo in AircraftPhoto.query.all()
    ]
    for column in FLIGHT_PHOTO_COLUMNS:
        for flight in Flight.query.filter(getattr(Flight, column).isnot(None)):
            rows.append((flight, column, getattr(flight, column)))
    return rows


def reencode(
    upload_folder: str, *, apply: bool = False, keep_originals: bool = False
) -> Report:
    from aircraft.routes import (  # pyright: ignore[reportMissingImports]
        _generate_photo_thumbnail,
        thumb_path_for,
    )

    folder = Path(upload_folder)
    report = Report()
    for row, column, relpath in _photos(folder):
        path = folder / relpath
        if not path.is_file():
            report.missing.append(relpath)
            continue
        state = _already_clean(path)
        if state is None:
            report.unreadable.append(relpath)
            continue
        if state:
            report.clean.append(relpath)
            continue
        if not apply:
            report.to_clean.append(relpath)
            continue
        data = _cleaned_bytes(path)
        if data is None:
            report.unreadable.append(relpath)
            continue
        directory, name = os.path.split(relpath)
        stem = os.path.splitext(name)[0]
        new_relpath = os.path.join(
            directory, f"{stem}-clean{uuid.uuid4().hex[:6]}.jpg"
        ).replace("\\", "/")
        (folder / new_relpath).write_bytes(data)
        setattr(row, column, new_relpath)
        db.session.commit()
        if isinstance(row, AircraftPhoto):
            _retire(folder, thumb_path_for(relpath), keep_originals)
            _generate_photo_thumbnail(
                str(folder / new_relpath), str(folder / thumb_path_for(new_relpath))
            )
        _retire(folder, relpath, keep_originals)
        report.cleaned.append(relpath)
    return report
