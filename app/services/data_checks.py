"""Consistency checks between logged flights and their GPS tracks.

The logbook is authoritative (services.gps_link never changes a recorded
flight), but it is hand-written: an airfield code misread (EDBK for EDRK), a
date off by a day. A flight's GPS track says where and when it really flew,
so crossing the two finds those slips. check_flight() is the one place that
does it; the Data checks page (config.data_checks) lists the findings with a
way to correct the logbook or dismiss them, and the daily checks keep the
count of open findings that the navbar bell and the configuration page show.

Cost: a check reads the track's points, which is the heavy part on a large
fleet. Findings are therefore cached per aircraft with a fingerprint of its
tracked flights (how many, the latest change to any of them, the newest
track); an aircraft is only checked again when that changes — a new flight,
an edit, a new or combined track.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AppSetting,
    DataCheckDismissal,
    Flight,
    GpsTrack,
    db,
)

# Kinds of finding.
DEPARTURE = "departure"
ARRIVAL = "arrival"
DATE = "date"
OUTSIDE_TIMES = "outside_times"

# A GPS end within this distance of the logged airfield agrees with it
# (same radius services.gps_import uses to name the nearest airfield).
_SAME_FIELD_KM = 5.0
# The GPS track starting or ending this much outside the logged times. A
# logbook records to 0.1 h and the recording usually runs from avionics
# power-on to shutdown, so only a clear gap counts.
_OUTSIDE_TIMES_MARGIN = timedelta(minutes=30)
_UNKNOWN_ICAO = ("", "????")

_CACHE_PREFIX = "data_checks:aircraft:"
_COUNT_PREFIX = "data_checks:open:"


@dataclass(frozen=True)
class Finding:
    """One difference between a flight and its GPS track."""

    flight_id: int
    aircraft_id: int
    flight_date: str  # the logged date, ISO
    kind: str
    logged: str  # what the logbook says ("" when empty)
    gps: str  # what the GPS says ("" when it can't name it)
    suggestion: str  # the value "use the GPS value" would set ("" = none)
    distance_km: float | None = None  # departure/arrival: GPS end ↔ logged field

    @property
    def key(self) -> str:
        """Identifies the finding for dismissals: a different GPS value
        (a re-imported or combined track) makes it a new finding."""
        return f"{self.kind}:{self.gps}"[:96]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _track_ends(geojson: Any) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """(lat, lon) of a track's first and last point."""
    if not isinstance(geojson, dict):
        return None
    geometry = geojson.get("geometry", geojson)
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, list) or not coords:
        return None
    try:
        first = (float(coords[0][1]), float(coords[0][0]))
        last = (float(coords[-1][1]), float(coords[-1][0]))
    except (TypeError, ValueError, IndexError):
        return None
    return first, last


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _logged_window(flight: Any) -> tuple[datetime, datetime] | None:
    start_t = flight.departure_time or flight.takeoff_time
    end_t = flight.arrival_time or flight.landing_time
    if flight.date is None or start_t is None or end_t is None:
        return None
    start = datetime.combine(flight.date, start_t, tzinfo=UTC)
    end = datetime.combine(flight.date, end_t, tzinfo=UTC)
    if end < start:  # past midnight UTC
        end += timedelta(days=1)
    return start, end


def check_flight(
    flight: Any, track: Any, airports: dict[str, tuple[float, float]]
) -> list[Finding]:
    """The differences between *flight* and its GPS *track*."""
    from aircraft.gps_import import resolve_icao  # pyright: ignore[reportMissingImports]

    def finding(
        kind: str, logged: str, gps: str, suggestion: str, **kw: Any
    ) -> Finding:
        return Finding(
            flight_id=flight.id,
            aircraft_id=flight.aircraft_id,
            flight_date=flight.date.isoformat() if flight.date else "",
            kind=kind,
            logged=logged,
            gps=gps,
            suggestion=suggestion,
            **kw,
        )

    found: list[Finding] = []
    ends = _track_ends(track.geojson)
    if ends is not None:
        for kind, logged_icao, (lat, lon) in (
            (DEPARTURE, flight.departure_icao, ends[0]),
            (ARRIVAL, flight.arrival_icao, ends[1]),
        ):
            logged = (logged_icao or "").strip().upper()
            field = airports.get(logged)
            if field is None and logged not in _UNKNOWN_ICAO:
                continue  # not in the airfield list: nothing to measure against
            distance = _haversine_km(lat, lon, field[0], field[1]) if field else None
            if distance is not None and distance <= _SAME_FIELD_KM:
                continue
            # Only now name the GPS end (the nearest-airfield search is the
            # slow part of a check).
            gps_icao = resolve_icao(lat, lon, airports) or ""
            if distance is None and not gps_icao:
                continue  # blank in the logbook, no airfield there either
            found.append(
                finding(
                    kind,
                    logged if field else "",
                    gps_icao,
                    gps_icao,
                    distance_km=round(distance, 1) if distance is not None else None,
                )
            )

    if track.block_off_utc is not None and flight.date is not None:
        gps_day = _utc(track.block_off_utc).date()
        if gps_day != flight.date:
            found.append(
                finding(
                    DATE,
                    flight.date.isoformat(),
                    gps_day.isoformat(),
                    gps_day.isoformat(),
                )
            )
        window = _logged_window(flight)
        if (
            window is not None
            and track.block_on_utc is not None
            and gps_day == flight.date
        ):
            off, on = _utc(track.block_off_utc), _utc(track.block_on_utc)
            if (
                off < window[0] - _OUTSIDE_TIMES_MARGIN
                or on > window[1] + _OUTSIDE_TIMES_MARGIN
            ):
                found.append(
                    finding(
                        OUTSIDE_TIMES,
                        f"{window[0]:%H:%M}–{window[1]:%H:%M}",
                        f"{off:%H:%M}–{on:%H:%M}",
                        "",
                    )
                )
    return found


# ── Per-aircraft cache ────────────────────────────────────────────────────────


def _fingerprint(aircraft_id: int) -> str:
    """Changes whenever a tracked flight of the aircraft is added, edited or
    removed, or a track is added or combined (the flight changes too)."""
    count, last_change, newest_track = (
        db.session.query(
            db.func.count(Flight.id),
            db.func.max(Flight.updated_at),
            db.func.max(Flight.gps_track_id),
        )
        .filter(Flight.aircraft_id == aircraft_id, Flight.gps_track_id.isnot(None))
        .one()
    )
    stamp = _utc(last_change).isoformat() if last_change else ""
    return f"{count}|{stamp}|{newest_track or ''}"


def _check_aircraft(aircraft_id: int) -> list[Finding]:
    from aircraft.gps_import import _load_airports  # pyright: ignore[reportMissingImports]

    airports = _load_airports()
    rows = (
        db.session.query(Flight, GpsTrack)
        .join(GpsTrack, Flight.gps_track_id == GpsTrack.id)
        .filter(Flight.aircraft_id == aircraft_id)
        .order_by(Flight.date.desc(), Flight.id.desc())
        .all()
    )
    return [f for flight, track in rows for f in check_flight(flight, track, airports)]


def aircraft_findings(aircraft_id: int, force: bool = False) -> list[Finding]:
    """All findings for the aircraft's tracked flights, dismissed ones
    included — from the cache when nothing changed since the last check,
    unless *force* (the "Run data checks" tool: e.g. after the airfield
    database was updated, which the cache's fingerprint doesn't see).
    Caller commits (a refreshed cache is added to the session)."""
    key = f"{_CACHE_PREFIX}{aircraft_id}"
    fingerprint = _fingerprint(aircraft_id)
    setting = db.session.get(AppSetting, key)
    if setting is not None and setting.value and not force:
        try:
            cached = json.loads(setting.value)
            if cached.get("fingerprint") == fingerprint:
                return [Finding(**f) for f in cached["findings"]]
        except (ValueError, TypeError, KeyError):
            pass  # unreadable cache: check again
    findings = _check_aircraft(aircraft_id)
    value = json.dumps(
        {"fingerprint": fingerprint, "findings": [asdict(f) for f in findings]}
    )
    if setting is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    return findings


def _dismissed(flight_ids: list[int]) -> set[tuple[int, str]]:
    if not flight_ids:
        return set()
    rows = DataCheckDismissal.query.filter(
        DataCheckDismissal.flight_id.in_(flight_ids)
    ).all()
    return {(r.flight_id, r.key) for r in rows}


def open_findings(
    tenant_id: int, force: bool = False
) -> list[tuple[Any, list[Finding]]]:
    """(aircraft, its findings not dismissed) for each of the tenant's
    aircraft that has any, by registration — every aircraft checked anew
    when *force* (see aircraft_findings). Caller commits."""
    result: list[tuple[Any, list[Finding]]] = []
    for ac in (
        Aircraft.query.filter_by(tenant_id=tenant_id)
        .order_by(Aircraft.registration)
        .all()
    ):
        findings = aircraft_findings(ac.id, force=force)
        dismissed = _dismissed(sorted({f.flight_id for f in findings}))
        kept = [f for f in findings if (f.flight_id, f.key) not in dismissed]
        if kept:
            result.append((ac, kept))
    return result


def refresh_open_count(tenant_id: int, force: bool = False) -> int:
    """Re-count the tenant's open findings (checking aircraft whose flights
    changed — or all of them when *force*) and store it for the navbar
    bell. Caller commits."""
    count = sum(len(f) for _ac, f in open_findings(tenant_id, force=force))
    key = f"{_COUNT_PREFIX}{tenant_id}"
    setting = db.session.get(AppSetting, key)
    if setting is None:
        db.session.add(AppSetting(key=key, value=str(count)))
    else:
        setting.value = str(count)
    return count


def open_count(tenant_id: int | None) -> int:
    """The stored count of open findings (as of the last refresh)."""
    if tenant_id is None:
        return 0
    setting = db.session.get(AppSetting, f"{_COUNT_PREFIX}{tenant_id}")
    try:
        return int(setting.value) if setting and setting.value else 0
    except ValueError:
        return 0


def refresh_all_open_counts() -> None:
    """Daily: refresh every tenant's count (see refresh_open_count)."""
    from models import Tenant  # pyright: ignore[reportMissingImports]

    for tenant in Tenant.query.all():
        refresh_open_count(tenant.id)
    db.session.commit()


def find(flight: Any, key: str) -> Finding | None:
    """The finding *key* for *flight* as its data stands now (None when it
    no longer applies — already corrected, or the track changed)."""
    from aircraft.gps_import import _load_airports  # pyright: ignore[reportMissingImports]

    if flight.gps_track is None:
        return None
    return next(
        (
            f
            for f in check_flight(flight, flight.gps_track, _load_airports())
            if f.key == key
        ),
        None,
    )


def apply_suggestion(flight: Any, finding: Finding) -> bool:
    """Correct the electronic logbook with the GPS value of *finding*, and
    note on the flight what the paper logbook says — the paper one stays as
    written, so the two can still be reconciled. False when there's no GPS
    value to apply."""
    from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

    if not finding.suggestion:
        return False
    paper = finding.logged or "—"
    if finding.kind == DEPARTURE:
        flight.departure_icao = finding.suggestion
        note = _(
            "Departure airfield corrected from the GPS track; the paper logbook says %(value)s.",
            value=paper,
        )
    elif finding.kind == ARRIVAL:
        flight.arrival_icao = finding.suggestion
        note = _(
            "Arrival airfield corrected from the GPS track; the paper logbook says %(value)s.",
            value=paper,
        )
    elif finding.kind == DATE:
        flight.date = date.fromisoformat(finding.suggestion)
        note = _(
            "Date corrected from the GPS track; the paper logbook says %(value)s.",
            value=paper,
        )
    else:
        return False
    flight.notes = f"{flight.notes.rstrip()}\n{note}" if flight.notes else note
    return True
