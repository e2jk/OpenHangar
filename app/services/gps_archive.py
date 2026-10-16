"""Keep every uploaded GPS log file as-is, on disk.

A GPS import only stores a simplified track in the database (at most
_MAX_TRACK_POINTS points, a handful of fields — see aircraft/gps_import.py).
The original files carry far more (one point per second, and for Garmin
data logs also airspeed, attitude, engine and fuel figures, …), so a copy of
each is kept under the uploads folder, where backups pick it up like any
other uploaded file, for future features to reprocess. Nothing about the
copy goes into the database.

Layout (relative to UPLOAD_FOLDER, see services.upload_layout):
  <tenant-slug>/<REG>/gps_logs/<file>   — uploaded for a managed aircraft
  _pilots/<user-id>/gps_logs/<file>     — uploaded from a pilot's own logbook
                                          (or for an aircraft not managed here)
"""

from __future__ import annotations

import hashlib
import os

from werkzeug.utils import secure_filename  # pyright: ignore[reportMissingImports]

from services.upload_layout import (  # pyright: ignore[reportMissingImports]
    GPS_ARCHIVE_DIRNAME,
    PILOT_ARCHIVE_ROOT,
)


def pilot_archive_parts(user_id: int) -> tuple[str, ...]:
    """Path components (under UPLOAD_FOLDER) of a pilot's GPS log archive."""
    return (PILOT_ARCHIVE_ROOT, str(user_id), GPS_ARCHIVE_DIRNAME)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_identical(dest_dir: str, data: bytes, digest: str) -> str | None:
    """Path of a file already in *dest_dir* with exactly this content, if any.

    Only files of the same size are hashed, so an upload usually costs no
    reads at all.
    """
    size = len(data)
    for entry in os.scandir(dest_dir):
        if entry.name.startswith(".") or not entry.is_file():
            continue
        if entry.stat().st_size == size and _sha256_file(entry.path) == digest:
            return entry.path
    return None


def archive_gps_file(data: bytes, original_filename: str, dest_dir: str) -> str:
    """Store *data* in *dest_dir* under its (sanitised) original name and
    return the stored file's full path.

    A file whose content is already in *dest_dir* — under any name — stores
    nothing new; the existing copy's path is returned. This only governs the
    archive: the upload is parsed and offered for import either way (e.g.
    after its flight was deleted). A different file arriving under a name
    already taken — "flight.gpx" from two different days, say — gets a short
    content-hash suffix rather than overwriting the earlier one.
    """
    os.makedirs(dest_dir, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    existing = _find_identical(dest_dir, data, digest)
    if existing:
        return existing
    name = secure_filename(original_filename) or "gps_log"
    path = os.path.join(dest_dir, name)
    if os.path.exists(path):
        stem, ext = os.path.splitext(name)
        path = os.path.join(dest_dir, f"{stem}-{digest[:8]}{ext}")
        if os.path.exists(path):  # short-hash collision: use the full hash
            path = os.path.join(dest_dir, f"{stem}-{digest}{ext}")
    # Write under a dot-prefixed temporary name first (the document scan
    # ignores those) so an interrupted write never leaves a truncated file
    # under the real name.
    tmp_path = os.path.join(dest_dir, f".{os.path.basename(path)}.part")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, path)
    return path
