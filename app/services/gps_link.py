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

from datetime import UTC, datetime, timedelta
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


def _same_icao(a: str | None, b: str | None) -> bool:
    """Same airfield — or unknown on either side (nothing says otherwise)."""
    known = [x.strip().upper() for x in (a, b) if x and x.strip() not in ("", "????")]
    return len(known) < 2 or known[0] == known[1]


def block_match(
    flight: Any,
    block_off: datetime,
    block_on: datetime,
    departure_icao: str | None,
    arrival_icao: str | None,
) -> bool:
    """Whether a GPS segment is *flight*'s own GPS data again, given that
    their block times are within the matching tolerance of each other:
    overlapping times are; times merely close (the next leg, minutes after
    landing) only when the route agrees too — otherwise a quick turnaround's
    second leg would land on the first leg's flight."""
    if _overlaps(flight.block_off_utc, flight.block_on_utc, block_off, block_on):
        return True
    return _same_icao(flight.departure_icao, departure_icao) and _same_icao(
        flight.arrival_icao, arrival_icao
    )


_LOGGED_WINDOW_TOLERANCE = timedelta(minutes=15)


def _within_logged_times(flight: Any, block_off: datetime, block_on: datetime) -> bool:
    """Whether a segment lies within the flight's logged departure→arrival
    times (UTC, 15 min either side) — or the flight has no logged times."""
    start_t = flight.departure_time or flight.takeoff_time
    end_t = flight.arrival_time or flight.landing_time
    if flight.date is None or start_t is None or end_t is None:
        return True
    start = datetime.combine(flight.date, start_t, tzinfo=UTC)
    end = datetime.combine(flight.date, end_t, tzinfo=UTC)
    if end < start:  # past midnight UTC
        end += timedelta(days=1)
    return bool(
        start - _LOGGED_WINDOW_TOLERANCE <= _utc(block_off)
        and _utc(block_on) <= end + _LOGGED_WINDOW_TOLERANCE
    )


def continuation_candidates(
    flights: list[Any],
    block_off: datetime,
    block_on: datetime,
    departure_icao: str | None,
    arrival_icao: str | None,
) -> list[Any]:
    """Among *flights* (already-tracked ones, on the segment's date), those
    this segment could be another part of — a later file of the same flight
    uploaded on its own: its track covers another time, the segment starts
    where the flight did or ends where it did, and it lies within the
    flight's logged times. Fallback when nothing else matches."""
    return [
        f
        for f in flights
        if f.block_off_utc is not None
        and f.block_on_utc is not None
        and not _overlaps(f.block_off_utc, f.block_on_utc, block_off, block_on)
        and (
            _same_icao(f.departure_icao, departure_icao)
            or _same_icao(f.arrival_icao, arrival_icao)
        )
        and _within_logged_times(f, block_off, block_on)
    ]


def adds_to_track(flight: Any, block_off: datetime, block_on: datetime) -> bool:
    """Whether linking a segment to *flight* adds to its existing track (it
    covers another time) rather than replacing it — for the review page."""
    return bool(
        flight.gps_track_id is not None
        and flight.block_off_utc is not None
        and flight.block_on_utc is not None
        and not _overlaps(
            flight.block_off_utc, flight.block_on_utc, block_off, block_on
        )
    )


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
