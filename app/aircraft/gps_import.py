"""GPS log file parsing for aircraft logbook import — Phase 30.

Supported formats:
- GPX 1.1 (SkyDemon, ForeFlight): speed in m/s, UTC timestamps
- Garmin GTN/G1000 CSV: 3-row header, local time + UTC offset, GndSpd in kt
- KML with gx:Track (SkyDemon): lon/lat/alt order, speed derived from consecutive points
"""

from __future__ import annotations

import bisect
import csv
import functools
import io
import itertools
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from xml.etree.ElementTree import ParseError as _ETParseError

import defusedxml.ElementTree as ET  # guards against XML bomb / entity expansion

# ── Constants ─────────────────────────────────────────────────────────────────

_MS_TO_KT = 1.94384  # m/s → knots
_FT_TO_M = 0.3048  # ft → metres
_KM_PER_NM = 1.852  # km per nautical mile

_FLIGHT_SPEED_KT = 30.0  # sustained above this → airborne
_GROUND_MOVE_KT = 5.0  # above this (but not 30kt for 30s) → ground movement
_FLIGHT_SUSTAIN_S = 30.0  # seconds above 30kt required to classify as "flight"
_SEGMENT_GAP_S = 300.0  # 5 min of slow speed or time gap → segment break
_MAX_ICAO_DIST_KM = 5.0  # max distance for nearest-airport match
_MAX_TRACK_POINTS = 500  # downsample threshold for GeoJSON storage
# How many days from a GPS segment's date a hand-entered logbook date may be
# off (a typo) and the flight still be offered as its possible match.
NEAR_DATE_DAYS = 2
# How far a GPS segment may start before / end after a logbook flight's
# block times and still be part of what that flight records.
_LOGGED_WINDOW_TOLERANCE = timedelta(minutes=15)

# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class TrackPoint:
    lat: float
    lon: float
    alt_m: float
    speed_kt: float
    utc_dt: datetime  # always timezone-aware UTC


@dataclass
class FlightSegment:
    trackpoints: list[TrackPoint]
    block_off_utc: datetime
    takeoff_utc: datetime | None
    landing_utc: datetime | None
    block_on_utc: datetime
    departure_icao: str | None
    arrival_icao: str | None
    flight_time_raw_h: float  # block_on − block_off in decimal hours
    flight_time_rounded_h: float  # rounded per aircraft precision setting
    track_geojson: dict[str, Any]  # GeoJSON Feature
    landing_count: int
    is_ground_only: bool  # True when no airborne portion detected
    hint_departure_icao: str | None
    hint_arrival_icao: str | None
    # Set when the segment joins several flights logged as one (see
    # merge_segments_logged_as_one): their summed airborne time, which the
    # takeoff-to-landing span would overstate by the time on the ground.
    airborne_raw_h: float | None = None


@dataclass
class LoggedFlightWindow:
    """A logbook flight's block span and route (UTC), used to recognise GPS
    segments that it records as a single flight."""

    start_utc: datetime
    end_utc: datetime
    departure_icao: str | None
    arrival_icao: str | None


@dataclass
class ParsedGpsFile:
    trackpoints: list[TrackPoint]
    format: str  # "gpx" | "kml" | "garmin_csv"
    source_filename: str
    classification: str  # "flight" | "ground_movement" | "empty"
    hint_departure_icao: str | None
    hint_arrival_icao: str | None
    device_id: str | None = None  # avionics unit identifier (e.g. Garmin system_id)


# ── Airport database ──────────────────────────────────────────────────────────


_NOT_FOR_AEROPLANES = {"heliport", "closed", "balloonport"}


@functools.lru_cache(maxsize=1)
def _load_airports() -> dict[str, tuple[float, float]]:
    """Load app/data/airports.csv once. Returns {icao: (lat, lon)}.

    Only 4-letter ICAO codes are included, and only places an aeroplane
    lands: not heliports, closed fields or balloon ports — a heliport's
    reference point can be closer to an airport's runway than the airport's
    own (Oostende Heliport EBNH, 1.6 km from EBOS), so it would name the
    wrong field. Returns an empty dict if the data file is missing (ICAO
    lookup will return None for all queries).
    """
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "airports.csv")
    airports: dict[str, tuple[float, float]] = {}

    if os.path.exists(data_path):
        with open(data_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ident = row.get("ident", "").strip()
                if not re.match(r"^[A-Z]{4}$", ident):
                    continue
                if row.get("type", "") in _NOT_FOR_AEROPLANES:
                    continue
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (ValueError, KeyError):
                    continue
                airports[ident] = (lat, lon)

    return airports


def _reset_airports_cache() -> None:
    """Reset the airport cache (for testing)."""
    _load_airports.cache_clear()


# ── Haversine ─────────────────────────────────────────────────────────────────


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Format detection ──────────────────────────────────────────────────────────


def detect_format(data: bytes, filename: str) -> str:
    """Return "gpx", "kml", or "garmin_csv". Raise ValueError for unknown format."""
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".gpx":
        return "gpx"
    if ext == ".kml":
        return "kml"
    if ext == ".csv":
        lines = data.decode("utf-8-sig", errors="replace").splitlines()
        if lines and lines[0].startswith("#airframe_info"):
            return "garmin_csv"
    raise ValueError(f"Unsupported GPS file format: {filename!r}")


# ── File classification ───────────────────────────────────────────────────────


def classify_track(trackpoints: list[TrackPoint]) -> str:
    """Return "flight", "ground_movement", or "empty"."""
    if not trackpoints:
        return "empty"

    max_speed = max(tp.speed_kt for tp in trackpoints)
    if max_speed <= _GROUND_MOVE_KT:
        return "empty"

    # Check for sustained window above 30kt
    fast_window_s = 0.0
    for prev, tp in itertools.pairwise(trackpoints):
        if tp.speed_kt > _FLIGHT_SPEED_KT:
            dt = (tp.utc_dt - prev.utc_dt).total_seconds()
            fast_window_s += max(0.0, dt)
            if fast_window_s >= _FLIGHT_SUSTAIN_S:
                return "flight"
        else:
            fast_window_s = 0.0

    return "ground_movement"


# ── GPX parser ────────────────────────────────────────────────────────────────


def _extract_icao_hints(text: str) -> tuple[str | None, str | None]:
    """Extract departure and arrival ICAO codes from a track name string."""
    icao_matches = re.findall(r"\b([A-Z]{4})\b", text)
    dep = icao_matches[0] if len(icao_matches) >= 1 else None
    arr = icao_matches[-1] if len(icao_matches) >= 2 else None
    return dep, arr


def _parse_xml_up_to_error(text: str) -> Any:
    """The XML document's root element — or, when the document breaks off
    (seen on real GPX exports missing their closing </gpx> tag), what was
    read of it before the error. Raises the parse error when nothing was."""
    try:
        return ET.fromstring(text)
    except _ETParseError as exc:
        root = None
        try:
            for _event, element in ET.iterparse(
                io.BytesIO(text.encode("utf-8")), events=("start",)
            ):
                if root is None:
                    root = element
        except _ETParseError:
            pass
        if root is None:
            raise exc from None
        return root


# A jump between two consecutive points implying more than this is a bad
# fix, not flight — no light aircraft flies it, even across a recording gap.
_MAX_PLAUSIBLE_KT = 600.0
# …and covering at least this distance (a stale fix jumps tens of km).
_MIN_JUMP_NM = 0.5
# What's left between two such jumps, if shorter than this, is the bad fix.
_MIN_FIX_RUN_S = 60.0


def _drop_bad_fixes(
    points: list[tuple[float, float, float, float | None, datetime]],
) -> list[tuple[float, float, float, float | None, datetime]]:
    """Points (lat, lon, alt, speed, time) in time order, without the bad
    fixes cameras and phones record while acquiring the GPS signal: no fix
    at all (0°, 0° — often with an unset clock, e.g. a date in another
    month), or a stale last-known position the track then jumps away from
    (seen on GoPro exports: 41 km in one second). The track is cut at every
    implausible jump; pieces under _MIN_FIX_RUN_S are then dropped."""
    ordered = sorted(
        (p for p in points if not (p[0] == 0.0 and p[1] == 0.0)),
        key=lambda p: p[4],
    )
    runs: list[list[tuple[float, float, float, float | None, datetime]]] = []
    for point in ordered:
        if runs:
            prev = runs[-1][-1]
            dt_s = (point[4] - prev[4]).total_seconds()
            dist_nm = _haversine_km(prev[0], prev[1], point[0], point[1]) / _KM_PER_NM
            # Both far and fast: a few metres of jitter between two
            # timestamps a fraction of a second apart (GoPro exports have
            # those) computes as hundreds of knots too.
            jump = dist_nm > _MIN_JUMP_NM and (
                dt_s <= 0 or dist_nm * 3600.0 / dt_s > _MAX_PLAUSIBLE_KT
            )
            if not jump:
                runs[-1].append(point)
                continue
        runs.append([point])
    if len(runs) <= 1:  # no implausible jump: nothing to tell apart
        return ordered
    return [
        p
        for run in runs
        if (run[-1][4] - run[0][4]).total_seconds() >= _MIN_FIX_RUN_S
        for p in run
    ]


# Speed from positions is measured over at least this long: GPS positions
# jitter by a few metres, so point-to-point at 1 Hz turns a parked aircraft's
# noise — or one bad fix — into spurious knots.
_DERIVED_SPEED_SPAN_S = 5.0


def _speeds_from_positions(points: list[tuple[float, float, datetime]]) -> list[float]:
    """Ground speed in knots at each (lat, lon, time) point, from the
    distance covered since the latest point at least _DERIVED_SPEED_SPAN_S
    earlier (the previous point when none is that old yet)."""
    speeds: list[float] = []
    j = 0
    for i, (lat, lon, when) in enumerate(points):
        if i == 0:
            speeds.append(0.0)
            continue
        while (
            j + 1 < i
            and (when - points[j + 1][2]).total_seconds() >= _DERIVED_SPEED_SPAN_S
        ):
            j += 1
        k = (
            j
            if (when - points[j][2]).total_seconds() >= _DERIVED_SPEED_SPAN_S
            else i - 1
        )
        dt_s = (when - points[k][2]).total_seconds()
        dist_km = _haversine_km(points[k][0], points[k][1], lat, lon)
        speeds.append(dist_km / _KM_PER_NM * 3600.0 / dt_s if dt_s > 0 else 0.0)
    return speeds


def _parse_gpx(data: bytes, filename: str) -> ParsedGpsFile:
    """Parse GPX 1.1 track. Speed field is in m/s; converted to kt — or,
    when the file has none, derived from the positions."""
    try:
        root = _parse_xml_up_to_error(data.decode("utf-8-sig", errors="replace"))
    except _ETParseError as exc:
        raise ValueError(f"Invalid GPX XML in {filename!r}: {exc}") from exc

    # The namespace the file declares: GPX 1.1, GPX 1.0 (same structure, e.g.
    # ExifTool exports) — or none at all.
    ns = root.tag[1 : root.tag.index("}")] if root.tag.startswith("{") else ""

    def q(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    hint_dep: str | None = None
    hint_arr: str | None = None
    name_el = root.find(f".//{q('name')}")
    if name_el is not None and name_el.text:
        hint_dep, hint_arr = _extract_icao_hints(name_el.text)

    points: list[tuple[float, float, float, float | None, datetime]] = []
    for trkpt in root.findall(f".//{q('trkpt')}"):
        try:
            lat = float(trkpt.get("lat", ""))
            lon = float(trkpt.get("lon", ""))
        except (ValueError, TypeError):
            continue

        ele_el = trkpt.find(q("ele"))
        alt_m = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0

        speed_el = trkpt.find(q("speed"))
        speed_kt = (
            float(speed_el.text) * _MS_TO_KT
            if speed_el is not None and speed_el.text
            else None
        )

        time_el = trkpt.find(q("time"))
        if time_el is None or not time_el.text:
            continue
        try:
            utc_dt = datetime.fromisoformat(time_el.text)
        except ValueError:
            continue

        points.append((lat, lon, alt_m, speed_kt, utc_dt))

    points = _drop_bad_fixes(points)
    # GPX 1.1 has no speed element: many writers (e.g. ExifTool exporting
    # a GoPro's GPS) leave it out — derive it from the positions then.
    if points and all(p[3] is None for p in points):
        speeds = _speeds_from_positions([(p[0], p[1], p[4]) for p in points])
    else:
        speeds = [p[3] or 0.0 for p in points]
    trackpoints = [
        TrackPoint(lat=lat, lon=lon, alt_m=alt_m, speed_kt=kt, utc_dt=when)
        for (lat, lon, alt_m, _speed, when), kt in zip(points, speeds, strict=True)
    ]

    return ParsedGpsFile(
        trackpoints=trackpoints,
        format="gpx",
        source_filename=filename,
        classification=classify_track(trackpoints),
        hint_departure_icao=hint_dep,
        hint_arrival_icao=hint_arr,
    )


# ── Garmin CSV parser ─────────────────────────────────────────────────────────

_VALID_GPS_FIX = {"3D", "3DDiff"}


def _parse_garmin_csv(data: bytes, filename: str) -> ParsedGpsFile:
    """Parse Garmin GTN/G1000 CSV with 3-row header.

    Row 0: #airframe_info metadata
    Row 1: unit labels
    Row 2: column names (Lcl Date, Lcl Time, UTCOfst, Latitude, Longitude, AltMSL, GndSpd, …, GPSfix, …)
    Only rows with GPSfix in {"3D", "3DDiff"} are used.
    Departure ICAO hint is extracted from filename: log_YYMMDD_HHMMSS_ICAO.csv
    """
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    if len(lines) < 4:
        raise ValueError(f"Garmin CSV too short: {filename!r}")

    # Device ID from #airframe_info header line
    device_id: str | None = None
    _did_match = re.search(r'system_id="([^"]+)"', lines[0])
    if _did_match:
        device_id = _did_match.group(1)

    # Departure ICAO from filename pattern
    hint_dep: str | None = None
    base = os.path.splitext(os.path.basename(filename))[0]
    parts = base.split("_")
    if len(parts) >= 4:
        candidate = parts[-1].strip()
        if re.match(r"^[A-Z]{4}$", candidate):
            hint_dep = candidate

    # Skip rows 0–1 (metadata + units), use row 2 as header
    csv_text = "\n".join(lines[2:])
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]

    trackpoints: list[TrackPoint] = []
    for row in reader:
        gpsfx = row.get("GPSfix", "").strip()
        if gpsfx not in _VALID_GPS_FIX:
            continue

        try:
            lat = float(row["Latitude"].strip())
            lon = float(row["Longitude"].strip())
        except (ValueError, KeyError):
            continue

        try:
            alt_m = float(row["AltMSL"].strip()) * _FT_TO_M
        except (ValueError, KeyError):
            alt_m = 0.0

        try:
            speed_kt = float(row["GndSpd"].strip())
        except (ValueError, KeyError):
            speed_kt = 0.0

        try:
            date_str = row["Lcl Date"].strip()
            time_str = row["Lcl Time"].strip()
            utc_off = row["UTCOfst"].strip()
            local_dt = datetime.fromisoformat(f"{date_str}T{time_str}{utc_off}")
            utc_dt = local_dt.astimezone(UTC)
        except (ValueError, KeyError):
            continue

        trackpoints.append(
            TrackPoint(lat=lat, lon=lon, alt_m=alt_m, speed_kt=speed_kt, utc_dt=utc_dt)
        )

    return ParsedGpsFile(
        trackpoints=trackpoints,
        format="garmin_csv",
        source_filename=filename,
        classification=classify_track(trackpoints),
        hint_departure_icao=hint_dep,
        hint_arrival_icao=None,
        device_id=device_id,
    )


# ── KML parser ────────────────────────────────────────────────────────────────

_KML_NS = "http://www.opengis.net/kml/2.2"
_GX_NS = "http://www.google.com/kml/ext/2.2"


def _parse_kml(data: bytes, filename: str) -> ParsedGpsFile:
    """Parse SkyDemon KML with gx:Track.

    Coordinate order is lon/lat/alt (note: reversed from GPX).
    Speed is derived from consecutive point distance / time delta.
    """
    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except _ETParseError as exc:
        raise ValueError(f"Invalid KML XML in {filename!r}: {exc}") from exc

    hint_dep: str | None = None
    hint_arr: str | None = None
    for pm in root.findall(f".//{{{_KML_NS}}}Placemark"):
        name_el = pm.find(f"{{{_KML_NS}}}name")
        if name_el is not None and name_el.text:
            dep, arr = _extract_icao_hints(name_el.text)
            if dep and arr:
                hint_dep, hint_arr = dep, arr
                break

    track_el = root.find(f".//{{{_GX_NS}}}Track")
    if track_el is None:
        raise ValueError(f"No gx:Track element in KML: {filename!r}")

    whens: list[datetime | None] = []
    coords: list[tuple[float, float, float]] = []

    for child in track_el:
        if child.tag == f"{{{_KML_NS}}}when":
            if child.text:
                try:
                    dt = datetime.fromisoformat(child.text)
                    whens.append(dt.astimezone(UTC))
                except ValueError:
                    whens.append(None)
            else:
                whens.append(None)
        elif child.tag == f"{{{_GX_NS}}}coord":
            if child.text:
                parts = child.text.strip().split()
                if len(parts) >= 3:
                    try:
                        lon_c = float(parts[0])
                        lat_c = float(parts[1])
                        alt_c = float(parts[2])
                    except ValueError:
                        lon_c, lat_c, alt_c = 0.0, 0.0, 0.0
                    coords.append((lon_c, lat_c, alt_c))
                    continue
            coords.append((0.0, 0.0, 0.0))

    if len(whens) != len(coords):
        raise ValueError(
            f"KML when/coord count mismatch in {filename!r}: "
            f"{len(whens)} vs {len(coords)}"
        )

    trackpoints: list[TrackPoint] = []
    for i, (when, (lon, lat, alt_m)) in enumerate(zip(whens, coords)):
        if when is None:
            continue

        if trackpoints:
            prev = trackpoints[-1]
            dt_s = (when - prev.utc_dt).total_seconds()
            dist_km = _haversine_km(prev.lat, prev.lon, lat, lon)
            speed_kt = (dist_km / _KM_PER_NM * 3600.0 / dt_s) if dt_s > 0 else 0.0
        else:
            speed_kt = 0.0

        trackpoints.append(
            TrackPoint(lat=lat, lon=lon, alt_m=alt_m, speed_kt=speed_kt, utc_dt=when)
        )

    return ParsedGpsFile(
        trackpoints=trackpoints,
        format="kml",
        source_filename=filename,
        classification=classify_track(trackpoints),
        hint_departure_icao=hint_dep,
        hint_arrival_icao=hint_arr,
    )


# ── Entry point ───────────────────────────────────────────────────────────────


def parse_gps_file(data: bytes, filename: str) -> ParsedGpsFile:
    """Detect format and parse. Raises ValueError on unsupported or invalid data."""
    fmt = detect_format(data, filename)
    if fmt == "gpx":
        return _parse_gpx(data, filename)
    if fmt == "kml":
        return _parse_kml(data, filename)
    return _parse_garmin_csv(data, filename)


# ── Track merge ───────────────────────────────────────────────────────────────


def merge_and_sort(files: list[ParsedGpsFile]) -> list[TrackPoint]:
    """Merge non-empty trackpoints from all files, sorted chronologically."""
    all_pts: list[TrackPoint] = []
    for f in files:
        if f.classification != "empty":
            all_pts.extend(f.trackpoints)
    all_pts.sort(key=lambda tp: tp.utc_dt)
    return all_pts


# ── Segment detection ─────────────────────────────────────────────────────────


def _split_into_raw_groups(trackpoints: list[TrackPoint]) -> list[list[TrackPoint]]:
    """Split merged trackpoints into groups at slow/time gaps ≥ 5 min.

    Only looks for breaks between the first and last fast (≥ 30kt) points, so
    pre-flight taxi and post-landing taxi are preserved in the enclosing
    segment — and at a break between two flights, each keeps its own taxi
    (see _break_points).
    """
    n = len(trackpoints)
    if n == 0:
        return []

    fast_indices = [
        i for i, tp in enumerate(trackpoints) if tp.speed_kt >= _FLIGHT_SPEED_KT
    ]
    if not fast_indices:
        # Ground runs only: still one group per recording session.
        sessions: list[list[TrackPoint]] = []
        start = 0
        for k in range(n - 1):
            if _recording_gap(trackpoints, k):
                sessions.append(trackpoints[start : k + 1])
                start = k + 1
        sessions.append(trackpoints[start:])
        return sessions

    first_fast = fast_indices[0]
    last_fast = fast_indices[-1]

    groups: list[list[TrackPoint]] = []
    current_start = 0
    # Before the first flight and after the last one, a recording gap still
    # separates sessions — a ground-only file from days earlier or later
    # (e.g. engine run at the parking spot) isn't that flight's taxi.
    for k in range(first_fast):
        if _recording_gap(trackpoints, k):
            groups.append(trackpoints[current_start : k + 1])
            current_start = k + 1
    i = first_fast

    while i < last_fast:
        # Large time gap between consecutive points (gap between uploaded files)
        time_gap = (trackpoints[i + 1].utc_dt - trackpoints[i].utc_dt).total_seconds()
        if time_gap >= _SEGMENT_GAP_S:
            groups.append(trackpoints[current_start : i + 1])
            current_start = i + 1
            i += 1
            continue

        # Slow run starting at i+1
        if trackpoints[i + 1].speed_kt < _FLIGHT_SPEED_KT:
            j = i + 2
            while j <= last_fast and trackpoints[j].speed_kt < _FLIGHT_SPEED_KT:
                j += 1
            # Elapsed from the last fast point to the next one, so a
            # recording gap at either end of the slow run counts too.
            slow_dur = (trackpoints[j].utc_dt - trackpoints[i].utc_dt).total_seconds()
            if slow_dur >= _SEGMENT_GAP_S:
                # Real segment break between two flights — the taxi-in
                # stays with the first, the taxi-out with the second.
                end_prev, start_next = _break_points(trackpoints, i, j)
                groups.append(trackpoints[current_start : end_prev + 1])
                # Recording sessions in between, with no flight in them (a
                # taxi to the fuel station, an engine run on another day):
                # each its own ground-only group.
                if _recording_gap(trackpoints, end_prev):
                    session_start = end_prev + 1
                    for k in range(end_prev + 1, start_next):
                        if _recording_gap(trackpoints, k):
                            groups.append(trackpoints[session_start : k + 1])
                            session_start = k + 1
                current_start = start_next
                i = j
            else:
                i = j  # short slow run — keep in current segment
        else:
            i += 1

    for k in range(last_fast, n - 1):
        if _recording_gap(trackpoints, k):
            groups.append(trackpoints[current_start : k + 1])
            current_start = k + 1
    groups.append(trackpoints[current_start:])
    return [g for g in groups if g]


def _recording_gap(trackpoints: list[TrackPoint], k: int) -> bool:
    """Whether the recording stopped for _SEGMENT_GAP_S or more after point k."""
    gap = (trackpoints[k + 1].utc_dt - trackpoints[k].utc_dt).total_seconds()
    return gap >= _SEGMENT_GAP_S


def _break_points(
    trackpoints: list[TrackPoint], last_fast: int, next_fast: int
) -> tuple[int, int]:
    """Where to split the slow stretch between two flights — the points
    strictly between *last_fast* (the first flight's landing roll) and
    *next_fast* (the second's takeoff roll). Returns (end of the first
    segment, start of the second), both indices into *trackpoints*.

    Splitting must keep each flight's taxi with it: the taxi-in carries the
    first flight's landing (fast-to-slow transition, see _count_landings)
    and its block-on, the taxi-out the second flight's block-off. In order:
      - a recording gap (the GPS was off, e.g. one data-log file per
        flight): the first flight ends at the first gap, the second starts
        after the last one;
      - otherwise the longest stop (below _GROUND_MOVE_KT — the aircraft
        parked): the first flight ends where it came to rest, the second
        starts where it moved off;
      - otherwise (slow movement throughout, nothing to go by) the whole
        slow stretch is left out of both.
    """
    gaps = [
        k
        for k in range(last_fast, next_fast)
        if (trackpoints[k + 1].utc_dt - trackpoints[k].utc_dt).total_seconds()
        >= _SEGMENT_GAP_S
    ]
    if gaps:
        # Anything between the first and last gap is recording sessions
        # with no flight in them (e.g. an engine ground run on another day)
        # — _split_into_raw_groups keeps each as a ground-only group.
        return gaps[0], gaps[-1] + 1

    best: tuple[float, int, int] | None = None
    k = last_fast + 1
    while k < next_fast:
        if trackpoints[k].speed_kt >= _GROUND_MOVE_KT:
            k += 1
            continue
        stop_start = k
        while k + 1 < next_fast and trackpoints[k + 1].speed_kt < _GROUND_MOVE_KT:
            k += 1
        stop_s = (
            trackpoints[k].utc_dt - trackpoints[stop_start].utc_dt
        ).total_seconds()
        if best is None or stop_s > best[0]:
            best = (stop_s, stop_start, k)
        k += 1
    if best is not None:
        return best[1], best[2]
    return last_fast, next_fast


# A segment whose flying starts (ends) this close to its first (last)
# point was recorded from (until) the air, not from (until) the ground.
_IN_FLIGHT_EDGE_S = 10.0

# Above _FLIGHT_SPEED_KT for at least this long is flying, below it for at
# least this long after flying is a landing: a noisy GPS (a camera's, a
# phone's) briefly crosses the threshold while taxiing or in flight.
_SUSTAIN_S = 30.0


def _airborne_periods(pts: list[TrackPoint]) -> list[tuple[int, int]]:
    """(first, last) point indices of each stretch flown above
    _FLIGHT_SPEED_KT for at least _SUSTAIN_S, merging stretches separated
    by less than _SUSTAIN_S below it (noise, not a landing)."""
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(pts):
        if pts[i].speed_kt < _FLIGHT_SPEED_KT:
            i += 1
            continue
        j = i
        while j + 1 < len(pts) and pts[j + 1].speed_kt >= _FLIGHT_SPEED_KT:
            j += 1
        if (pts[j].utc_dt - pts[i].utc_dt).total_seconds() >= _SUSTAIN_S:
            runs.append((i, j))
        i = j + 1
    periods: list[tuple[int, int]] = []
    for first, last in runs:
        if (
            periods
            and (pts[first].utc_dt - pts[periods[-1][1]].utc_dt).total_seconds()
            < _SUSTAIN_S
        ):
            periods[-1] = (periods[-1][0], last)
        else:
            periods.append((first, last))
    return periods


def _count_landings(pts: list[TrackPoint]) -> int:
    """Full-stop landings: airborne periods (see _airborne_periods) the
    recording carries on after — i.e. followed by slow points."""
    return sum(1 for _first, last in _airborne_periods(pts) if last + 1 < len(pts))


def detect_segments(
    trackpoints: list[TrackPoint],
    aircraft_precision: str = "tenth_hour",
    hint_dep: str | None = None,
    hint_arr: str | None = None,
) -> list[FlightSegment]:
    """Build FlightSegment objects from merged trackpoints.

    hint_dep / hint_arr are optional ICAO codes from GPX/KML track names or
    Garmin filename patterns, used as fallback when GPS-proximity lookup fails.
    """
    raw_groups = _split_into_raw_groups(trackpoints)
    airports = _load_airports()
    segments: list[FlightSegment] = []

    for idx, pts in enumerate(raw_groups):
        block_off = pts[0].utc_dt
        block_on = pts[-1].utc_dt

        periods = _airborne_periods(pts)
        takeoff_utc: datetime | None = pts[periods[0][0]].utc_dt if periods else None
        landing_utc: datetime | None = pts[periods[-1][1]].utc_dt if periods else None

        is_ground_only = takeoff_utc is None
        landing_count = _count_landings(pts)

        raw_h = (block_on - block_off).total_seconds() / 3600.0
        rounded_h = round_flight_time(raw_h, aircraft_precision)

        # A recording that starts (or ends) already flying — the camera or
        # logger switched on in flight — has no departure (arrival) airfield
        # to tell: whatever it's flying over isn't one.
        started_flying = bool(
            periods
            and (pts[periods[0][0]].utc_dt - pts[0].utc_dt).total_seconds()
            < _IN_FLIGHT_EDGE_S
        )
        ended_flying = bool(
            periods
            and (pts[-1].utc_dt - pts[periods[-1][1]].utc_dt).total_seconds()
            < _IN_FLIGHT_EDGE_S
        )
        # (The file's own hint — a Garmin file name, a GPX track name —
        # still counts: it's the recorder's own record of the field.)
        dep_icao = (
            None if started_flying else resolve_icao(pts[0].lat, pts[0].lon, airports)
        ) or (hint_dep if idx == 0 else None)
        arr_icao = (
            None if ended_flying else resolve_icao(pts[-1].lat, pts[-1].lon, airports)
        ) or (hint_arr if idx == len(raw_groups) - 1 else None)

        downsampled = downsample_track(pts)
        geojson = build_geojson(downsampled)

        segments.append(
            FlightSegment(
                trackpoints=pts,
                block_off_utc=block_off,
                takeoff_utc=takeoff_utc,
                landing_utc=landing_utc,
                block_on_utc=block_on,
                departure_icao=dep_icao,
                arrival_icao=arr_icao,
                flight_time_raw_h=raw_h,
                flight_time_rounded_h=rounded_h,
                track_geojson=geojson,
                landing_count=landing_count,
                is_ground_only=is_ground_only,
                hint_departure_icao=hint_dep if idx == 0 else None,
                hint_arrival_icao=hint_arr if idx == len(raw_groups) - 1 else None,
            )
        )

    return segments


def logged_flight_windows(flights: list[Any]) -> list[LoggedFlightWindow]:
    """LoggedFlightWindow for each Flight-like object whose block times
    (departure/arrival, else takeoff/landing — all UTC times of day) are
    both known; flights without them can't vouch for anything."""
    windows: list[LoggedFlightWindow] = []
    for f in flights:
        start_t = f.departure_time or f.takeoff_time
        end_t = f.arrival_time or f.landing_time
        if f.date is None or start_t is None or end_t is None:
            continue
        start = datetime.combine(f.date, start_t, tzinfo=UTC)
        end = datetime.combine(f.date, end_t, tzinfo=UTC)
        if end < start:  # past midnight UTC
            end += timedelta(days=1)
        windows.append(LoggedFlightWindow(start, end, f.departure_icao, f.arrival_icao))
    return windows


def _same_place(a: str | None, b: str | None) -> bool:
    """Equal, or unknown on either side (nothing to contradict)."""
    return not a or not b or a == b


def _recorded_throughout(times: list[datetime], start: datetime, end: datetime) -> bool:
    """True when the recording has no gap of _SEGMENT_GAP_S or more between
    *start* and *end* — the avionics stayed on (one data-log file), so the
    engine most likely kept running too."""
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_right(times, end)
    return all(
        (times[k + 1] - times[k]).total_seconds() < _SEGMENT_GAP_S
        for k in range(lo, hi - 1)
    )


def _logged_run_end(
    segments: list[FlightSegment],
    times: list[datetime],
    windows: list[LoggedFlightWindow],
    i: int,
) -> int:
    """Index of the last segment of the longest run starting at *i* that a
    single logbook flight records as one, or *i* when there is none."""
    best = i
    for w in windows:
        lo = w.start_utc - _LOGGED_WINDOW_TOLERANCE
        hi = w.end_utc + _LOGGED_WINDOW_TOLERANCE
        first = segments[i]
        if (
            first.is_ground_only
            or first.block_off_utc < lo
            or not _same_place(first.departure_icao, w.departure_icao)
        ):
            continue
        k = i
        while k + 1 < len(segments):
            prev, nxt = segments[k], segments[k + 1]
            if (
                nxt.is_ground_only
                or nxt.block_on_utc > hi
                or not _same_place(prev.arrival_icao, nxt.departure_icao)
                or not _recorded_throughout(times, prev.block_on_utc, nxt.block_off_utc)
            ):
                break
            k += 1
            if k > best and _same_place(nxt.arrival_icao, w.arrival_icao):
                best = k
    return best


def _merge_run(
    run: list[FlightSegment], trackpoints: list[TrackPoint], precision: str
) -> FlightSegment:
    """One FlightSegment spanning *run*, including the ground stops between
    its legs."""
    times = [tp.utc_dt for tp in trackpoints]
    lo = bisect.bisect_left(times, run[0].trackpoints[0].utc_dt)
    hi = bisect.bisect_right(times, run[-1].trackpoints[-1].utc_dt)
    pts = trackpoints[lo:hi]
    block_off = run[0].block_off_utc
    block_on = run[-1].block_on_utc
    raw_h = (block_on - block_off).total_seconds() / 3600.0
    airborne_h = sum(
        (s.landing_utc - s.takeoff_utc).total_seconds() / 3600.0
        for s in run
        if s.takeoff_utc and s.landing_utc
    )
    return FlightSegment(
        trackpoints=pts,
        block_off_utc=block_off,
        takeoff_utc=run[0].takeoff_utc,
        landing_utc=run[-1].landing_utc,
        block_on_utc=block_on,
        departure_icao=run[0].departure_icao,
        arrival_icao=run[-1].arrival_icao,
        flight_time_raw_h=raw_h,
        flight_time_rounded_h=round_flight_time(raw_h, precision),
        track_geojson=build_geojson(downsample_track(pts)),
        landing_count=sum(s.landing_count for s in run),
        is_ground_only=False,
        hint_departure_icao=run[0].hint_departure_icao,
        hint_arrival_icao=run[-1].hint_arrival_icao,
        airborne_raw_h=airborne_h,
    )


def merge_segments_logged_as_one(
    segments: list[FlightSegment],
    trackpoints: list[TrackPoint],
    windows: list[LoggedFlightWindow],
    precision: str = "tenth_hour",
) -> list[FlightSegment]:
    """Join consecutive segments that one logbook flight records as a single
    flight — e.g. a landing to check something with the engine running,
    then on to the destination, logged as one flight with two landings.

    Segment detection alone splits at any 5-minute stop; only the logbook
    can say the pilot counted it as one flight. Segments are joined when
    they all fall within that flight's block times, chain from its
    departure to its arrival, and the recording never stopped in between
    (a gap means the avionics, and most likely the engine, were switched
    off — a separate flight whatever the logbook says).
    """
    if len(segments) < 2 or not windows:
        return segments
    times = [tp.utc_dt for tp in trackpoints]
    out: list[FlightSegment] = []
    i = 0
    while i < len(segments):
        k = _logged_run_end(segments, times, windows, i)
        out.append(
            segments[i]
            if k == i
            else _merge_run(segments[i : k + 1], trackpoints, precision)
        )
        i = k + 1
    return out


# ── ICAO resolution ───────────────────────────────────────────────────────────


def resolve_icao(
    lat: float,
    lon: float,
    airports: dict[str, tuple[float, float]] | None = None,
) -> str | None:
    """Return the nearest ICAO code within 5 km, or None if none is close enough."""
    if airports is None:
        airports = _load_airports()

    best_code: str | None = None
    best_dist = _MAX_ICAO_DIST_KM

    for code, (ap_lat, ap_lon) in airports.items():
        d = _haversine_km(lat, lon, ap_lat, ap_lon)
        if d < best_dist:
            best_dist = d
            best_code = code

    return best_code


# ── Time rounding ─────────────────────────────────────────────────────────────


def round_flight_time(raw_hours: float, precision: str) -> float:
    """Round raw_hours up to the nearest precision boundary.

    precision="tenth_hour": round up to nearest 0.1 h (6-min boundary).
    precision="minute": round up to nearest 1/60 h (1-min boundary).
    """
    if raw_hours <= 0:
        return 0.0
    if precision == "minute":
        minutes = math.ceil(raw_hours * 60)
        return round(minutes / 60, 4)
    # tenth_hour: ceiling to nearest 0.1
    return round(math.ceil(raw_hours * 10) / 10, 1)


# ── GeoJSON / downsampling ────────────────────────────────────────────────────


@dataclass
class SegmentTimes:
    """A confirmed GPS segment's two duration tracks (see models.Flight's
    docstring): block-off to block-on is the engine side (block time,
    feeds the pilot logbook), the detected takeoff-to-landing span is the
    airborne side the airframe log's flight_time tracks."""

    block_time: Decimal
    airborne_time: Decimal | None
    takeoff_time: time | None
    landing_time: time | None


def segment_times(seg: dict[str, Any], precision: str) -> SegmentTimes:
    """SegmentTimes for a serialised segment dict (as kept between the
    review and confirm steps), rounded to the aircraft's *precision*."""
    takeoff = (
        datetime.fromisoformat(seg["takeoff_utc"]) if seg.get("takeoff_utc") else None
    )
    landing = (
        datetime.fromisoformat(seg["landing_utc"]) if seg.get("landing_utc") else None
    )
    airborne: Decimal | None = None
    if seg.get("airborne_raw_h") is not None:
        airborne = Decimal(str(round_flight_time(seg["airborne_raw_h"], precision)))
    elif takeoff is not None and landing is not None:
        hours = (landing - takeoff).total_seconds() / 3600.0
        airborne = Decimal(str(round_flight_time(hours, precision)))
    return SegmentTimes(
        block_time=Decimal(
            str(round_flight_time(seg.get("flight_time_raw_h", 0), precision))
        ),
        airborne_time=airborne,
        takeoff_time=takeoff.time().replace(tzinfo=None) if takeoff else None,
        landing_time=landing.time().replace(tzinfo=None) if landing else None,
    )


def downsample_track(
    trackpoints: list[TrackPoint], max_points: int = _MAX_TRACK_POINTS
) -> list[TrackPoint]:
    """Return ≤ max_points trackpoints using uniform stride; first and last preserved."""
    n = len(trackpoints)
    if n <= max_points:
        return trackpoints

    stride = n / max_points
    indices: set[int] = {round(i * stride) for i in range(max_points)}
    indices.add(0)
    indices.add(n - 1)
    return [trackpoints[i] for i in sorted(indices) if i < n]


def build_geojson(trackpoints: list[TrackPoint]) -> dict[str, Any]:
    """Return a GeoJSON Feature with a LineString geometry.

    Coordinates: [lon, lat, alt_m] per GeoJSON spec (RFC 7946).
    Properties carry parallel arrays of altitudes_m and speeds_kt for
    colour-gradient rendering in Leaflet.
    """
    coords = [
        [round(tp.lon, 6), round(tp.lat, 6), round(tp.alt_m, 1)] for tp in trackpoints
    ]
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "altitudes_m": [round(tp.alt_m, 1) for tp in trackpoints],
            "speeds_kt": [round(tp.speed_kt, 1) for tp in trackpoints],
        },
    }


# ── Near-match scoring against existing Flight rows without GPS block data ───


def score_gps_candidates(
    fields: dict[str, Any],
    candidates: list[Any],
    scorer: Callable[[dict[str, Any], Any], float],
    min_score: int,
) -> list[Any]:
    """Rank *candidates* (Flight rows) against a parsed GPS segment's
    *fields* dict using *scorer*, best match first.

    A GPS segment's block_off_utc/block_on_utc only ever exists on rows
    that themselves came from a GPS import — a flight logged manually or
    via CSV import (airframe or pilot logbook) has neither, so the exact
    block-time overlap check the review routes try first can never find
    it. This is the fallback: reuse the exact same near-match scorers the
    CSV-import review flows already use where possible
    (_score_gps_candidate below, built on
    flights.airframe_import._score_airframe_non_time_signals, for
    aircraft-linked rows; pilots.logbook_import._score_candidate directly
    for standalone ones) so a previously-imported flight is recognised
    instead of silently duplicated — rather than inventing a second scoring
    implementation that could drift from the first.
    """
    scored = [
        (score, c) for c in candidates if (score := scorer(fields, c)) >= min_score
    ]
    scored.sort(key=lambda t: -t[0])
    return [c for _score, c in scored]


def near_date_range(gps_date: date) -> tuple[date, date]:
    """First and last logbook date a near-date match may carry."""
    span = timedelta(days=NEAR_DATE_DAYS)
    return gps_date - span, gps_date + span


def rank_near_date_candidates(
    fields: dict[str, Any],
    gps_date: date,
    candidates: list[Any],
    scorer: Callable[[dict[str, Any], Any], float],
    min_score: int,
) -> list[Any]:
    """Flights logged on another date close to a GPS segment's — a typo in
    a hand-entered logbook date — ranked closest date first, then by score.

    The GPS date is the reliable one, so this is only a fallback for a
    segment nothing matched on its own date, and stricter than a same-day
    match: the route must be known and identical at both ends, and the
    date at most NEAR_DATE_DAYS days off. A flight years away never
    qualifies, whatever else it shares.
    """
    dep = fields.get("departure_icao")
    arr = fields.get("arrival_icao")
    if not dep or not arr:
        return []
    pool = [
        c
        for c in candidates
        if c.date != gps_date
        and abs((c.date - gps_date).days) <= NEAR_DATE_DAYS
        and c.departure_icao == dep
        and c.arrival_icao == arr
    ]
    ranked = score_gps_candidates(fields, pool, scorer, min_score)
    return sorted(ranked, key=lambda c: abs((c.date - gps_date).days))


def _score_gps_candidate(
    fields: dict[str, Any], existing: Any, offset_hours: float
) -> float:
    """Score how likely *existing* (a Flight row) is the same real-world
    flight as *fields* (a parsed GPS segment) — the 5 non-time signals from
    flights.airframe_import._score_airframe_non_time_signals, plus a
    GPS-specific comparison for the other 2.

    A GPS track's block_off/block_on record a single observed instant per
    end, and — unlike a same-source CSV re-import — there's no guarantee it
    lines up tightly with either departure_time (engine start) or
    takeoff_time (wheels-up): recording can start before the engine does
    (setting up a flight plan on a tablet) and keep running a little after
    landing. So instead of preferring one time field over the other, the
    full-credit window spans whichever of departure_time/takeoff_time (and
    landing_time/arrival_time) the existing row has, widened further on
    each side — see services.time_band_matching.widened_span_score.
    """
    from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
        _score_airframe_non_time_signals,
    )
    from services.time_band_matching import (  # pyright: ignore[reportMissingImports]
        offset_ring_step_minutes,
        widened_span_score,
    )

    score = _score_airframe_non_time_signals(fields, existing)
    widen_minutes = offset_ring_step_minutes(offset_hours)

    takeoff_edges = tuple(
        t for t in (existing.departure_time, existing.takeoff_time) if t is not None
    )
    score += widened_span_score(
        fields.get("takeoff_time"), takeoff_edges, widen_minutes
    )

    landing_edges = tuple(
        t for t in (existing.landing_time, existing.arrival_time) if t is not None
    )
    score += widened_span_score(
        fields.get("landing_time"), landing_edges, widen_minutes
    )

    return score
