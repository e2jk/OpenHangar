"""Taking a GPS file off a flight — and putting it back.

A flight's track is kept as parts, one per GPS file in the usual case
(services.gps_link). Removing a file takes its part off the track: the map
and the flight's GPS block times are rebuilt from the parts left, and with
none left the track is gone. A part made of several files (a log split over
files during one flight) goes as a whole — its files can't be told apart.
Logged times are never touched: GPS data never changed them.

The original file moves to the trash folder (upload_layout.TRASH_ROOT) for
TRASH_DAYS days, then the daily job purges it. Each removal is recorded
(GpsFileRemoval) with the removed part, so it can be restored — file and
track part together — until then; the record stays afterwards as a log of
who removed what. A file the track (or another flight's track) still uses
stays where it is.

"Undo import" goes through here too: it removes the parts its batch added
(parts carry their batch since link_track records it), not the whole track.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from models import (  # pyright: ignore[reportMissingImports]
    Flight,
    GpsFileRemoval,
    GpsTrack,
    db,
)
from sqlalchemy import String, cast  # pyright: ignore[reportMissingImports]

from services.gps_link import (  # pyright: ignore[reportMissingImports]
    _parts_of,
    _store,
    _when,
    link_track,
)
from services.upload_layout import TRASH_ROOT  # pyright: ignore[reportMissingImports]

TRASH_DAYS = 7
_TRASH_GPS = "gps"


def part_key(part: dict[str, Any]) -> str:
    """Identifies a part in a removal form, so a page left open while the
    track changed can't remove a different part than the one shown."""
    return f"{part.get('start') or ''}|{','.join(part.get('files') or [])}"


def listed_parts(track: Any) -> list[dict[str, Any]]:
    """The track's parts as the flight page lists them: index, key, times,
    whether it's a taxi part, and its files."""
    if track is None or not track.geojson:
        return []
    listed = list(track.source_files or [])
    return [
        {
            "index": i,
            "key": part_key(p),
            "start": _when(p, "start"),
            "end": _when(p, "end"),
            "taxi": bool(p.get("taxi")),
            # name, and its index in source_files (the download link's)
            "files": [
                (f.rsplit("/", 1)[-1], listed.index(f) if f in listed else None)
                for f in p.get("files") or []
            ],
        }
        for i, p in enumerate(_parts_of(track))
    ]


def _abs(upload_folder: str, relpath: str) -> str:
    """*relpath* (a "/"-separated path under the uploads folder, as stored)
    as a real path — never outside the uploads folder."""
    root = os.path.realpath(upload_folder)
    path = os.path.realpath(os.path.join(root, *relpath.split("/")))
    if os.path.commonpath([root, path]) != root:
        raise ValueError(f"path outside the uploads folder: {relpath!r}")
    return path


def _used_by_other_track(path: str, track_id: int) -> bool:
    """Whether another flight's track still lists file *path*."""
    candidates = GpsTrack.query.filter(
        GpsTrack.id != track_id,
        GpsTrack.source_files.isnot(None),
        cast(GpsTrack.source_files, String).like(f"%{path}%"),
    )
    return any(path in (t.source_files or []) for t in candidates)


def _to_trash(upload_folder: str, relpath: str) -> str | None:
    """Move file *relpath* to the trash; its path there, or None when it's
    missing."""
    src = _abs(upload_folder, relpath)
    if not os.path.isfile(src):
        return None
    trash_rel = "/".join(
        (TRASH_ROOT, _TRASH_GPS, uuid.uuid4().hex, relpath.rsplit("/", 1)[-1])
    )
    dst = _abs(upload_folder, trash_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    return trash_rel


def flight_label(fe: Any) -> str:
    route = " → ".join(x for x in (fe.departure_icao, fe.arrival_icao) if x)
    return f"{fe.date} {route}".strip()[:96]


def remove_part(
    fe: Any,
    index: int,
    *,
    tenant_id: int,
    user_id: int | None,
    upload_folder: str,
    reason: str = "removed",
) -> GpsFileRemoval:
    """Take part *index* off *fe*'s track and its file(s) to the trash, and
    record it. Caller checks access and commits."""
    track = db.session.get(GpsTrack, fe.gps_track_id) if fe.gps_track_id else None
    if track is None:
        raise ValueError(f"flight {fe.id} has no GPS track")
    parts = _parts_of(track)
    part = parts.pop(index)
    if parts:
        _store(fe, track, parts)
        if all(p.get("taxi") for p in parts):
            # Only taxi parts left: they never give the flight its times.
            fe.block_off_utc = fe.block_on_utc = None
    else:
        fe.gps_track_id = None
        fe.block_off_utc = fe.block_on_utc = None
        db.session.delete(track)
    still_listed = {f for p in parts for f in p.get("files") or []}
    files = []
    for path in part.get("files") or []:
        kept = path in still_listed or _used_by_other_track(path, track.id)
        files.append(
            {"path": path, "trash": None if kept else _to_trash(upload_folder, path)}
        )
    removal = GpsFileRemoval(
        tenant_id=tenant_id,
        flight_id=fe.id,
        flight_label=flight_label(fe),
        removed_by_user_id=user_id,
        removed_at=datetime.now(UTC),
        reason=reason,
        files=files,
        part={
            **{k: v for k, v in part.items() if k != "n"},
            "track": {
                "source_filename": track.source_filename,
                "device_id": track.device_id,
                "departure_icao": track.departure_icao,
                "arrival_icao": track.arrival_icao,
            },
        },
    )
    db.session.add(removal)
    db.session.flush()
    return removal


def remove_whole_track(fe: Any, **kwargs: Any) -> list[GpsFileRemoval]:
    """Remove every part of *fe*'s track (see remove_part)."""
    removals = []
    while fe.gps_track_id is not None:
        removals.append(remove_part(fe, 0, **kwargs))
    return removals


def remove_batch_parts(fe: Any, batch_id: int, **kwargs: Any) -> list[GpsFileRemoval]:
    """Remove the parts of *fe*'s track that import batch *batch_id* added.
    A track from before parts recorded their batch can't tell: all of it
    goes, as it always did."""
    if fe.gps_track_id is None:
        return []
    parts = _parts_of(db.session.get(GpsTrack, fe.gps_track_id))
    if not any("batch" in p for p in parts):
        return remove_whole_track(fe, **kwargs)
    indexes = [i for i, p in enumerate(parts) if p.get("batch") == batch_id]
    return [remove_part(fe, i, **kwargs) for i in reversed(indexes)]


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _put_back(upload_folder: str, entry: dict[str, Any]) -> str:
    """Move a trashed file back where it was — or next to it, under a
    suffixed name, when another file took its place meanwhile. Returns its
    path. A file never moved (missing, or still used) is left as it is."""
    original = str(entry["path"])
    trash = entry.get("trash")
    if not trash:
        return original
    src = _abs(upload_folder, trash)
    if not os.path.isfile(src):
        return original
    rel = original
    dst = _abs(upload_folder, rel)
    if os.path.exists(dst):
        digest = _sha256(src)
        if _sha256(dst) == digest:
            os.remove(src)
            return rel
        stem, ext = os.path.splitext(rel)
        rel = f"{stem}-{digest[:8]}{ext}"
        dst = _abs(upload_folder, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    _remove_empty_dir(os.path.dirname(src))
    return rel


def _remove_empty_dir(path: str) -> None:
    with contextlib.suppress(OSError):  # not empty, or already gone
        os.rmdir(path)


def restorable(removal: Any) -> bool:
    return removal.restored_at is None and removal.purged_at is None


def restore(removal: Any, upload_folder: str) -> Any:
    """Undo *removal*: its files back where they were, and its part back on
    the flight's track if the flight still exists. Returns the flight, or
    None when it's gone (the files are back in the archive either way).
    Caller checks access and commits."""
    paths = [_put_back(upload_folder, entry) for entry in removal.files or []]
    removal.restored_at = datetime.now(UTC)
    fe = db.session.get(Flight, removal.flight_id) if removal.flight_id else None
    part = removal.part or {}
    if fe is None or not part.get("coords"):
        return fe
    meta = part.get("track") or {}
    link_track(
        fe,
        geojson={
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": part["coords"]},
        },
        files=paths,
        block_off=_when(part, "start"),
        block_on=_when(part, "end"),
        source_filename=meta.get("source_filename"),
        device_id=meta.get("device_id"),
        departure_icao=meta.get("departure_icao"),
        arrival_icao=meta.get("arrival_icao"),
        taxi=bool(part.get("taxi")),
        batch_id=part.get("batch"),
    )
    return fe


def purge_expired(upload_folder: str, now: datetime | None = None) -> int:
    """Delete the trashed files of removals older than TRASH_DAYS (the daily
    job). The removal records stay, marked purged. Returns how many
    removals were purged."""
    now = now or datetime.now(UTC)
    expired = GpsFileRemoval.query.filter(
        GpsFileRemoval.restored_at.is_(None),
        GpsFileRemoval.purged_at.is_(None),
        GpsFileRemoval.removed_at < now - timedelta(days=TRASH_DAYS),
    ).all()
    for removal in expired:
        for entry in removal.files or []:
            if entry.get("trash"):
                path = _abs(upload_folder, entry["trash"])
                if os.path.isfile(path):
                    os.remove(path)
                _remove_empty_dir(os.path.dirname(path))
        removal.purged_at = now
    db.session.commit()
    return len(expired)


def purge_date(removal: Any) -> datetime:
    removed_at: datetime = removal.removed_at
    if removed_at.tzinfo is None:
        removed_at = removed_at.replace(tzinfo=UTC)
    return removed_at + timedelta(days=TRASH_DAYS)
