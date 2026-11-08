"""Linking a GPS track to a flight — including a flight that already has one.

A flight usually gets one track from one file. Real situations give a flight
more than one: the logger lost power in flight and started a new file (e.g.
the battery switched off), the flight was logged as one logbook entry
although the aircraft landed in between (e.g. returned to the field after a
transponder failure and departed again), or the aircraft taxied with the
engine stopped in between — to the fuel station or customs before departure,
back to the hangar after arrival — each engine start a recording of its own.
The recorded flight is authoritative — it's what the paper logbook says — so
GPS data never changes its times or date; it only adds the track.

A track is kept as parts in time order (GeoJSON properties "parts": start,
end, number of points, files, whether it's a taxi part, and the import batch
that added it), its coordinates
those of all parts joined. link_track() decides, for a flight and a new GPS
segment:
  - no track yet → the segment's track becomes the flight's;
  - the segment's files are all already linked to the flight's track → no
    change (re-importing the same logs adds nothing);
  - the segment covers the same time as a part of the existing track → it
    replaces that part (a re-processed or corrected log of the same flight);
  - otherwise → it's added as another part, in time order.
The track's — and the flight's — GPS block times span its flight parts
only: a taxi part (taxi=True) is shown with the flight and its file listed,
but never stretches the flight's times.
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


def _utc(value: datetime) -> datetime:
    """Comparable UTC datetime: stored values may come back naive (SQLite),
    GPS ones are always aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """The same time, for GPS data: overlapping by at least half of the
    shorter span. Two recordings sharing a few minutes at their edges — a
    camera left running across a dual flight and the solo straight after
    it — are two flights, not the same one twice."""
    overlap = (
        min(_utc(a_end), _utc(b_end)) - max(_utc(a_start), _utc(b_start))
    ).total_seconds()
    if overlap <= 0:
        return False
    shorter = min(
        (_utc(a_end) - _utc(a_start)).total_seconds(),
        (_utc(b_end) - _utc(b_start)).total_seconds(),
    )
    return bool(overlap >= shorter / 2)


# Codes that say "unknown": blank, the import placeholder, and ZZZZ (ICAO's
# "no code for this place").
_UNKNOWN_ICAO = ("", "????", "ZZZZ")


def _same_icao(a: str | None, b: str | None) -> bool:
    """Same airfield — or unknown on either side (nothing says otherwise)."""
    known = [
        x.strip().upper()
        for x in (a, b)
        if x and x.strip().upper() not in _UNKNOWN_ICAO
    ]
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
    second leg would land on the first leg's flight — and the segment lies
    within the flight's logged times (when it has any): a dual flight and
    the solo flown straight after it share a route, but each its own times."""
    if _overlaps(flight.block_off_utc, flight.block_on_utc, block_off, block_on):
        return True
    return (
        _same_icao(flight.departure_icao, departure_icao)
        and _same_icao(flight.arrival_icao, arrival_icao)
        and _within_logged_times(flight, block_off, block_on)
    )


_LOGGED_WINDOW_TOLERANCE = timedelta(minutes=15)


def _logged_span(flight: Any) -> tuple[datetime, datetime] | None:
    """The flight's logged departure→arrival times as UTC datetimes."""
    start_t = flight.departure_time or flight.takeoff_time
    end_t = flight.arrival_time or flight.landing_time
    if flight.date is None or start_t is None or end_t is None:
        return None
    start = datetime.combine(flight.date, start_t, tzinfo=UTC)
    end = datetime.combine(flight.date, end_t, tzinfo=UTC)
    if end < start:  # past midnight UTC
        end += timedelta(days=1)
    return start, end


def _within_logged_times(flight: Any, block_off: datetime, block_on: datetime) -> bool:
    """Whether a segment lies within the flight's logged departure→arrival
    times (UTC, 15 min either side) — or the flight has no logged times."""
    span = _logged_span(flight)
    if span is None:
        return True
    start, end = span
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
    gps_spans: dict[int, tuple[datetime, datetime]] | None = None,
) -> list[Any]:
    """Among *flights* (on the segment's date), those this segment could be
    (another part of): a later file of the same flight, a piece of it after
    the recording stopped and restarted in flight, or a recording that
    covers only part of it. The segment starts where the flight did or ends
    where it did (or the airfield isn't known), lies within the flight's
    logged times, and — when the flight has GPS times (its track's, or —
    *gps_spans* — another segment's of this same import) — covers another
    time than those. Fallback when nothing else matches."""
    found = []
    for f in flights:
        span = (gps_spans or {}).get(f.id) or (
            (f.block_off_utc, f.block_on_utc) if f.gps_track_id else (None, None)
        )
        if span[0] is None or span[1] is None:
            # No GPS times yet: only by its logged times — a recording that
            # stopped before landing (or started after takeoff) lacks the
            # signals the fuzzy match needs, but lies within them.
            if _logged_span(f) is None:
                continue
        elif _overlaps(span[0], span[1], block_off, block_on):
            continue
        if (
            _same_icao(f.departure_icao, departure_icao)
            or _same_icao(f.arrival_icao, arrival_icao)
        ) and _within_logged_times(f, block_off, block_on):
            found.append(f)
    return found


# A ground run this close before a departure or after an arrival, at that
# airfield, is the flight's taxi (to the fuel station, customs, the hangar).
TAXI_WINDOW = timedelta(minutes=45)


def ground_run_target(
    block_off: datetime,
    block_on: datetime,
    airfield: str | None,
    flights: list[Any],
    gps_spans: dict[int, tuple[datetime, datetime]],
    claimed: set[int],
) -> tuple[Any, str, str] | None:
    """Where a ground-only GPS recording (engine running, never airborne)
    belongs among *flights* (the aircraft's flights that day), as
    (flight, role, when):
      - ("entry", ""): a logged ground entry (an engine run, a taxi logged
        on its own) at that airfield with no more logged flight time than
        the recording lasted, not yet tracked nor claimed by another
        segment — the recording is its track;
      - ("taxi", "before"/"after"): the flight departing from that airfield
        within TAXI_WINDOW after the recording ends, or arriving there
        within TAXI_WINDOW before it starts — the closest one; its times are
        its GPS block times (*gps_spans*: from this import, else its track)
        or else its logged times.
    None: nothing near — the recording stays unlinked."""
    hours = (_utc(block_on) - _utc(block_off)).total_seconds() / 3600
    entries = [
        f
        for f in flights
        if f.gps_track_id is None
        and f.id not in claimed
        and _same_icao(f.departure_icao, airfield)
        and _same_icao(f.arrival_icao, airfield)
        and float(f.flight_time or 0) <= hours + 0.1
        and _within_logged_times(f, block_off, block_on)
    ]
    if entries:
        return entries[0], "entry", ""

    best: tuple[timedelta, Any, str] | None = None
    for f in flights:
        if f.id in gps_spans:
            span: tuple[datetime, datetime] | None = gps_spans[f.id]
        elif f.block_off_utc is not None and f.block_on_utc is not None:
            span = (f.block_off_utc, f.block_on_utc)
        else:
            span = _logged_span(f)
        if span is None:
            continue
        start, end = _utc(span[0]), _utc(span[1])
        options = []
        if _utc(block_on) <= start and _same_icao(f.departure_icao, airfield):
            options.append((start - _utc(block_on), "before"))
        if _utc(block_off) >= end and _same_icao(f.arrival_icao, airfield):
            options.append((_utc(block_off) - end, "after"))
        for gap, when in options:
            if gap <= TAXI_WINDOW and (best is None or gap < best[0]):
                best = (gap, f, when)
    if best is None:
        return None
    return best[1], "taxi", best[2]


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


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _when(part: dict[str, Any], key: str) -> datetime | None:
    raw = part.get(key)
    try:
        return datetime.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _parts_of(track: Any) -> list[dict[str, Any]]:
    """The track's parts, each with its own coordinates. A track stored
    before parts were recorded (or whose parts no longer add up) is one
    flight part."""
    coords = _line_coords(track.geojson) or []
    props = track.geojson.get("properties") if isinstance(track.geojson, dict) else None
    stored = props.get("parts") if isinstance(props, dict) else None
    if isinstance(stored, list) and stored:
        try:
            counts = [int(p["n"]) for p in stored]
        except (TypeError, KeyError, ValueError):
            counts = []
        if counts and sum(counts) == len(coords):
            parts, at = [], 0
            for p, n in zip(stored, counts, strict=True):
                parts.append({**p, "coords": coords[at : at + n]})
                at += n
            return parts
    return [
        {
            "start": _iso(track.block_off_utc),
            "end": _iso(track.block_on_utc),
            "files": list(track.source_files or []),
            "taxi": False,
            "coords": coords,
        }
    ]


def _store(flight: Any, track: Any, parts: list[dict[str, Any]]) -> None:
    """Write *parts* (in time order) back to *track*, and the GPS block
    times its flight parts span to the track and the flight."""
    parts.sort(key=lambda p: _when(p, "start") or datetime.min.replace(tzinfo=UTC))
    coords = [c for p in parts for c in p["coords"]]
    track.geojson = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "parts": [
                {
                    "start": p.get("start"),
                    "end": p.get("end"),
                    "n": len(p["coords"]),
                    "files": p.get("files") or [],
                    "taxi": bool(p.get("taxi")),
                    # The import batch that added it (services.gps_removal:
                    # "Undo import" removes only its own parts).
                    **({"batch": p["batch"]} if p.get("batch") else {}),
                }
                for p in parts
            ]
        },
    }
    files: list[str] = []
    for p in parts:
        files += [f for f in p.get("files") or [] if f not in files]
    track.source_files = files or None
    flight_parts = [p for p in parts if not p.get("taxi")]
    starts = [t for p in flight_parts if (t := _when(p, "start"))]
    ends = [t for p in flight_parts if (t := _when(p, "end"))]
    track.block_off_utc = min(starts) if starts else None
    track.block_on_utc = max(ends) if ends else None
    if flight_parts:
        flight.block_off_utc = track.block_off_utc
        flight.block_on_utc = track.block_on_utc
    # The cached renders show the old track — rebuilt on next request.
    track.cached_png = None
    track.cached_gif = None


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
    taxi: bool = False,
    batch_id: int | None = None,
) -> Any:
    """Give *flight* the track of a GPS segment (see the module docstring)
    and return the track it now has. *taxi*: a ground movement before or
    after the flight, shown with it but never changing its times.
    *batch_id*: the GPS import batch it comes from, recorded on its part.
    Caller commits."""
    new_part = {
        "start": _iso(block_off),
        "end": _iso(block_on),
        "files": list(files),
        "taxi": taxi,
        "coords": _line_coords(geojson) or [],
        **({"batch": batch_id} if batch_id else {}),
    }
    track = (
        db.session.get(GpsTrack, flight.gps_track_id) if flight.gps_track_id else None
    )
    if track is None:
        track = GpsTrack(
            source_filename=source_filename,
            device_id=device_id,
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
        )
        db.session.add(track)
        db.session.flush()
        flight.gps_track_id = track.id
        _store(flight, track, [new_part])
        return track
    if files and set(files) <= set(track.source_files or []):
        return track

    def same_time(part: dict[str, Any]) -> bool:
        # Overlapping the new segment — or a time unknown on either side,
        # which can't be told apart: the same data again.
        start, end = _when(part, "start"), _when(part, "end")
        if start is None or end is None or block_off is None or block_on is None:
            return True
        return _overlaps(start, end, block_off, block_on)

    kept = [
        p for p in _parts_of(track) if bool(p.get("taxi")) != taxi or not same_time(p)
    ]
    if not taxi:
        if not [p for p in kept if not p.get("taxi")]:
            # Every flight part replaced: the new log's details take over.
            track.source_filename = source_filename
            track.device_id = device_id or track.device_id
        track.departure_icao = track.departure_icao or departure_icao
        track.arrival_icao = arrival_icao or track.arrival_icao
    _store(flight, track, [*kept, new_part])
    return track
