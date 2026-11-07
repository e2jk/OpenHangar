"""Linking a GPS track to a flight — including a flight that already has one.

A flight usually gets one track from one file. Two real situations give a
flight more than one: the logger lost power in flight and started a new file
(e.g. the battery switched off), or the flight was logged as one logbook
entry although the aircraft landed in between (e.g. returned to the field
after a transponder failure and departed again). The recorded flight is
authoritative — it's what the paper logbook says — so GPS data never changes
its times or date; it only adds the track.

link_track() decides, for a flight and a new GPS segment:
  - no track yet → the segment's track becomes the flight's;
  - the segment's files are all already linked to the flight's track → no
    change (re-importing the same logs adds nothing);
  - the segment covers the same time as the existing track → it replaces it
    (a re-processed or corrected log of the same flight);
  - otherwise → the two are combined into one track: coordinates joined in
    time order, files listed together, block times spanning both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models import GpsTrack, db  # pyright: ignore[reportMissingImports]


def _line_coords(geojson: Any) -> list[Any] | None:
    """The coordinates of a GeoJSON LineString Feature (or bare geometry)."""
    if not isinstance(geojson, dict):
        return None
    geometry = geojson.get("geometry", geojson)
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return None
    coords = geometry.get("coordinates")
    return coords if isinstance(coords, list) else None


def combine_geojson(first: Any, second: Any) -> Any:
    """One LineString Feature with *first*'s coordinates then *second*'s;
    when either isn't a LineString, *second* alone (nothing sensible to join)."""
    a, b = _line_coords(first), _line_coords(second)
    if a is None or b is None:
        return second
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": a + b},
        "properties": dict(first.get("properties") or {})
        if isinstance(first, dict)
        else {},
    }


def _utc(value: datetime) -> datetime:
    """Comparable UTC datetime: stored values may come back naive (SQLite),
    GPS ones are always aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    return bool(_utc(a_start) < _utc(b_end) and _utc(b_start) < _utc(a_end))


def link_track(
    flight: Any,
    *,
    geojson: Any,
    files: list[str],
    block_off: datetime | None,
    block_on: datetime | None,
    source_filename: str | None = None,
    device_id: str | None = None,
    departure_icao: str | None = None,
    arrival_icao: str | None = None,
) -> Any:
    """Give *flight* the track of a GPS segment (see the module docstring)
    and return the track it now has. Caller commits."""
    existing = (
        db.session.get(GpsTrack, flight.gps_track_id) if flight.gps_track_id else None
    )
    if existing is not None:
        known = set(existing.source_files or [])
        if files and set(files) <= known:
            return existing
        # Combined only when both spans are known and apart; anything else
        # (overlapping, or a time missing) is the same flight's data again.
        if (
            block_off is not None
            and block_on is not None
            and existing.block_off_utc is not None
            and existing.block_on_utc is not None
            and not _overlaps(
                existing.block_off_utc, existing.block_on_utc, block_off, block_on
            )
        ):
            return _combine(flight, existing, geojson, files, block_off, block_on)
    track = GpsTrack(
        source_filename=source_filename,
        source_files=files or None,
        device_id=device_id,
        block_off_utc=block_off,
        block_on_utc=block_on,
        departure_icao=departure_icao,
        arrival_icao=arrival_icao,
        geojson=geojson,
    )
    db.session.add(track)
    db.session.flush()
    flight.gps_track_id = track.id
    flight.block_off_utc = block_off
    flight.block_on_utc = block_on
    if existing is not None:
        db.session.delete(existing)
    return track


def _combine(
    flight: Any,
    track: Any,
    geojson: Any,
    files: list[str],
    block_off: datetime,
    block_on: datetime,
) -> Any:
    new_first = _utc(block_off) < _utc(track.block_off_utc)
    track.geojson = (
        combine_geojson(geojson, track.geojson)
        if new_first
        else combine_geojson(track.geojson, geojson)
    )
    old_files = list(track.source_files or [])
    added = [f for f in files if f not in old_files]
    track.source_files = (added + old_files if new_first else old_files + added) or None
    track.block_off_utc = min(_utc(track.block_off_utc), _utc(block_off))
    track.block_on_utc = max(_utc(track.block_on_utc), _utc(block_on))
    # The cached renders show the old track — rebuilt on next request.
    track.cached_png = None
    track.cached_gif = None
    flight.block_off_utc = track.block_off_utc
    flight.block_on_utc = track.block_on_utc
    return track
