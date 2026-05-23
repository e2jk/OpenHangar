"""GPS log file parsing for aircraft logbook import — Phase 30.

Supported formats:
- GPX 1.1 (SkyDemon, ForeFlight): speed in m/s, UTC timestamps
- Garmin GTN/G1000 CSV: 3-row header, local time + UTC offset, GndSpd in kt
- KML with gx:Track (SkyDemon): lon/lat/alt order, speed derived from consecutive points
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import xml.etree.ElementTree as ET

# ── Constants ─────────────────────────────────────────────────────────────────

_MS_TO_KT = 1.94384  # m/s → knots
_FT_TO_M = 0.3048  # ft → metres
_KM_PER_NM = 1.852  # km per nautical mile

_FLIGHT_SPEED_KT = 30.0  # sustained above this → airborne
_GROUND_MOVE_KT = 5.0  # above this (but not 30kt for 30s) → ground movement
_FLIGHT_SUSTAIN_S = 30.0  # seconds above 30kt required to classify as "flight"
_SEGMENT_GAP_S = 300.0  # 5 min of slow speed or time gap → segment break
_MERGE_GAP_S = 1800.0  # 30 min: merge ground_movement with adjacent flight
_MAX_ICAO_DIST_KM = 5.0  # max distance for nearest-airport match
_MAX_TRACK_POINTS = 500  # downsample threshold for GeoJSON storage

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


@dataclass
class ParsedGpsFile:
    trackpoints: list[TrackPoint]
    format: str  # "gpx" | "kml" | "garmin_csv"
    source_filename: str
    classification: str  # "flight" | "ground_movement" | "empty"
    hint_departure_icao: str | None
    hint_arrival_icao: str | None


# ── Airport database ──────────────────────────────────────────────────────────

_AIRPORTS_CACHE: dict[str, tuple[float, float]] | None = None


def _load_airports() -> dict[str, tuple[float, float]]:
    """Load app/data/airports.csv once. Returns {icao: (lat, lon)}.

    Only 4-letter ICAO codes are included. Returns an empty dict if the
    data file is missing (ICAO lookup will return None for all queries).
    """
    global _AIRPORTS_CACHE
    if _AIRPORTS_CACHE is not None:
        return _AIRPORTS_CACHE

    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "airports.csv")
    airports: dict[str, tuple[float, float]] = {}

    if os.path.exists(data_path):
        with open(data_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ident = row.get("ident", "").strip()
                if not re.match(r"^[A-Z]{4}$", ident):
                    continue
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (ValueError, KeyError):
                    continue
                airports[ident] = (lat, lon)

    _AIRPORTS_CACHE = airports
    return airports


def _reset_airports_cache() -> None:
    """Reset the module-level airport cache (for testing)."""
    global _AIRPORTS_CACHE
    _AIRPORTS_CACHE = None


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
        try:
            first_line = data.decode("utf-8-sig", errors="replace").splitlines()[0]
            if first_line.startswith("#airframe_info"):
                return "garmin_csv"
        except IndexError:
            pass
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
    for prev, tp in zip(trackpoints, trackpoints[1:]):
        if tp.speed_kt > _FLIGHT_SPEED_KT:
            dt = (tp.utc_dt - prev.utc_dt).total_seconds()
            fast_window_s += max(0.0, dt)
            if fast_window_s >= _FLIGHT_SUSTAIN_S:
                return "flight"
        else:
            fast_window_s = 0.0

    return "ground_movement"


# ── GPX parser ────────────────────────────────────────────────────────────────

_GPX_NS = "http://www.topografix.com/GPX/1/1"


def _extract_icao_hints(text: str) -> tuple[str | None, str | None]:
    """Extract departure and arrival ICAO codes from a track name string."""
    icao_matches = re.findall(r"\b([A-Z]{4})\b", text)
    dep = icao_matches[0] if len(icao_matches) >= 1 else None
    arr = icao_matches[-1] if len(icao_matches) >= 2 else None
    return dep, arr


def _parse_gpx(data: bytes, filename: str) -> ParsedGpsFile:
    """Parse GPX 1.1 track. Speed field is in m/s; converted to kt."""
    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except ET.ParseError as exc:
        raise ValueError(f"Invalid GPX XML in {filename!r}: {exc}") from exc

    hint_dep: str | None = None
    hint_arr: str | None = None
    name_el = root.find(f".//{{{_GPX_NS}}}name")
    if name_el is not None and name_el.text:
        hint_dep, hint_arr = _extract_icao_hints(name_el.text)

    trackpoints: list[TrackPoint] = []
    for trkpt in root.findall(f".//{{{_GPX_NS}}}trkpt"):
        try:
            lat = float(trkpt.get("lat", ""))
            lon = float(trkpt.get("lon", ""))
        except (ValueError, TypeError):
            continue

        ele_el = trkpt.find(f"{{{_GPX_NS}}}ele")
        alt_m = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0

        speed_el = trkpt.find(f"{{{_GPX_NS}}}speed")
        speed_kt = (
            float(speed_el.text) * _MS_TO_KT
            if speed_el is not None and speed_el.text
            else 0.0
        )

        time_el = trkpt.find(f"{{{_GPX_NS}}}time")
        if time_el is None or not time_el.text:
            continue
        try:
            utc_dt = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
        except ValueError:
            continue

        trackpoints.append(
            TrackPoint(lat=lat, lon=lon, alt_m=alt_m, speed_kt=speed_kt, utc_dt=utc_dt)
        )

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
            utc_dt = local_dt.astimezone(timezone.utc)
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
    except ET.ParseError as exc:
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
                    dt = datetime.fromisoformat(child.text.replace("Z", "+00:00"))
                    whens.append(dt.astimezone(timezone.utc))
                except ValueError:
                    whens.append(None)
            else:
                whens.append(None)
        elif child.tag == f"{{{_GX_NS}}}coord":
            if child.text:
                parts = child.text.strip().split()
                if len(parts) >= 3:
                    try:
                        coords.append(
                            (float(parts[0]), float(parts[1]), float(parts[2]))
                        )
                        continue
                    except ValueError:
                        pass
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
    pre-flight taxi and post-landing taxi are preserved in the enclosing segment.
    """
    n = len(trackpoints)
    if n == 0:
        return []

    fast_indices = [
        i for i, tp in enumerate(trackpoints) if tp.speed_kt >= _FLIGHT_SPEED_KT
    ]
    if not fast_indices:
        return [trackpoints]

    first_fast = fast_indices[0]
    last_fast = fast_indices[-1]

    groups: list[list[TrackPoint]] = []
    current_start = 0
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
            slow_dur = (
                trackpoints[j - 1].utc_dt - trackpoints[i + 1].utc_dt
            ).total_seconds()
            if slow_dur >= _SEGMENT_GAP_S:
                # Real segment break — exclude slow gap from both segments
                groups.append(trackpoints[current_start : i + 1])
                current_start = j
                i = j
            else:
                i = j  # short slow run — keep in current segment
        else:
            i += 1

    groups.append(trackpoints[current_start:])
    return [g for g in groups if g]


def _count_landings(pts: list[TrackPoint]) -> int:
    """Count transitions from airborne (≥30kt) to ground (<30kt)."""
    count = 0
    was_fast = False
    for tp in pts:
        is_fast = tp.speed_kt >= _FLIGHT_SPEED_KT
        if was_fast and not is_fast:
            count += 1
        was_fast = is_fast
    return count


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

        takeoff_utc: datetime | None = None
        landing_utc: datetime | None = None
        for tp in pts:
            if tp.speed_kt >= _FLIGHT_SPEED_KT:
                if takeoff_utc is None:
                    takeoff_utc = tp.utc_dt
                landing_utc = tp.utc_dt

        is_ground_only = takeoff_utc is None
        landing_count = _count_landings(pts)

        raw_h = (block_on - block_off).total_seconds() / 3600.0
        rounded_h = round_flight_time(raw_h, aircraft_precision)

        dep_icao = resolve_icao(pts[0].lat, pts[0].lon, airports) or (
            hint_dep if idx == 0 else None
        )
        arr_icao = resolve_icao(pts[-1].lat, pts[-1].lon, airports) or (
            hint_arr if idx == len(raw_groups) - 1 else None
        )

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
