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
import zipfile
from datetime import datetime
from typing import IO

from flask import Response  # pyright: ignore[reportMissingImports]
from werkzeug.utils import secure_filename  # pyright: ignore[reportMissingImports]

from services.safe_paths import (  # pyright: ignore[reportMissingImports]
    regular_files,
    resolve_inside,
)
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


def archive_relpath(path: str, upload_folder: str) -> str:
    """*path* (as returned by archive_gps_file) relative to the uploads
    folder, "/"-separated — what GpsTrack.source_files lists."""
    return os.path.relpath(path, upload_folder).replace(os.sep, "/")


def resolve_pilot_archive_name(
    upload_folder: str, user_id: int, name: str | None
) -> str | None:
    """The relative path of file *name* in *user_id*'s own GPS archive, if
    it exists there. *name* comes back from a form (the flight form's
    upload is archived when parsed, then saved with the flight), so it's
    only ever looked up as a plain file name inside the user's own folder —
    never a path the form could point elsewhere."""
    if not name or secure_filename(name) != name:
        return None
    rel = "/".join((*pilot_archive_parts(user_id), name))
    return rel if os.path.isfile(os.path.join(upload_folder, *rel.split("/"))) else None


def sources_for_window(
    file_metas: list[dict[str, object]], start: datetime, end: datetime
) -> list[str]:
    """Which uploaded file(s) a flight [start, end] came from, among a batch
    of uploaded files (each meta with "archive_path" and its recorded time
    span "t_start"/"t_end" as ISO strings): every file whose span overlaps
    it, in time order — usually one, two when the logger was cut off in
    flight and started a new file — or the only file of a single-file
    upload."""
    with_path = [m for m in file_metas if m.get("archive_path")]
    if len(with_path) == 1:
        return [str(with_path[0]["archive_path"])]
    found: list[tuple[datetime, str]] = []
    for meta in with_path:
        try:
            t0 = datetime.fromisoformat(str(meta["t_start"]))
            t1 = datetime.fromisoformat(str(meta["t_end"]))
        except (KeyError, ValueError):
            continue
        if min(t1, end) > max(t0, start):
            found.append((t0, str(meta["archive_path"])))
    return [path for _t0, path in sorted(found)]


def iter_archive_files(upload_folder: str, rel_dir: str) -> list[str]:
    """Relative paths of the files kept in archive folder *rel_dir*."""
    root = os.path.join(upload_folder, *rel_dir.split("/"))
    if not os.path.isdir(root):
        return []
    return sorted(
        f"{rel_dir}/{entry.name}"
        for entry in os.scandir(root)
        if entry.is_file() and not entry.name.startswith(".")
    )


def write_zip(upload_folder: str, relpaths: list[str], fileobj: IO[bytes]) -> int:
    """Write the given archived files (relative paths) into a zip in
    *fileobj*, each under its own file name (made unique if two folders
    hold the same name). Returns the number of files written; missing
    files are skipped. GPS logs compress well, so deflate."""
    written = 0
    names: set[str] = set()
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in dict.fromkeys(relpaths):  # de-duplicated, order kept
            full = resolve_inside(upload_folder, *rel.split("/"))
            if full is None or not os.path.isfile(full):
                continue
            name = os.path.basename(full)
            if name in names:
                stem, ext = os.path.splitext(name)
                name = f"{stem}-{written}{ext}"
            names.add(name)
            zf.write(full, arcname=name)
            written += 1
    return written


def archive_stats(upload_folder: str) -> tuple[int, int]:
    """(file count, total bytes) of every kept GPS log, aircraft and pilot
    archives together — for the configuration page."""
    count = size = 0
    if not os.path.isdir(upload_folder):
        return 0, 0
    for path in regular_files(upload_folder):
        if os.path.basename(os.path.dirname(path)) != GPS_ARCHIVE_DIRNAME:
            continue
        if os.path.basename(path).startswith("."):
            continue
        count += 1
        size += os.path.getsize(path)
    return count, size


def send_archived_file(upload_folder: str, relpath: str) -> Response:
    """Flask response downloading one kept file (404 if it's gone). The
    path comes from the database (GpsTrack.source_files), never a request,
    and is still confined to the uploads folder."""
    from services.safe_paths import send_inside

    return send_inside(upload_folder, relpath, as_attachment=True)


def send_archive_zip(
    upload_folder: str, relpaths: list[str], download_name: str
) -> Response | None:
    """Flask response downloading the given kept files as one zip, built in
    a temporary file (a pilot's or aircraft's logs can be large) that's
    deleted once the response has been sent. None when none of the files
    exist."""
    import tempfile

    from flask import send_file  # pyright: ignore[reportMissingImports]

    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    with os.fdopen(fd, "wb") as fh:
        written = write_zip(upload_folder, relpaths, fh)
    if not written:
        os.remove(tmp_path)
        return None
    response = send_file(
        tmp_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )
    response.call_on_close(lambda: os.remove(tmp_path))
    return response
