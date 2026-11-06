"""Matching an engine run that never took off across the pilot and airframe
logbooks.

A flight cancelled after engine start — the pilot taxied out, then decided
not to go (weather, a technical issue, aeronautical decision making) — is
logged on both sides: the pilot logs the block time (it is PIC time even
without a takeoff), and the airframe log records the engine counter
advancing while the flight counter doesn't move. Neither side has takeoff
or landing clock times, and the pilot log often has no block times either,
so the usual cross-logbook matching (route + clock times within the taxi
window, see services.time_band_matching) has nothing to work with and would
log the one real-world event twice.

This module recognises the pair from what both sides *do* record: same
day, same aircraft (the callers' candidate pools already guarantee both),
same known route, positive evidence on the airframe side that the aircraft
never got airborne (zero airborne time, no landings), no landings on the
pilot side, and the pilot's block time agreeing with the engine counter
difference. Missing data is never read as "did not fly": a row whose
airborne time simply wasn't recorded doesn't qualify.

Pure functions over plain values, so both the pilot import
(pilots/logbook_import.py) and the airframe import
(flights/airframe_import.py) can feed it from either a parsed spreadsheet
row or an existing Flight row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

# Block time and the engine counter difference are both logged to 0.1 h;
# allow one step of rounding disagreement between the two logbooks.
NO_TAKEOFF_TIME_TOLERANCE = 0.1
_EPS = 1e-9


@dataclass(frozen=True)
class AirframeSide:
    """The airframe-log view of a flight."""

    departure_icao: str | None
    arrival_icao: str | None
    takeoff_time: time | None
    landing_time: time | None
    flight_time: Any  # airborne hours (float/Decimal/None)
    landing_count: int | None
    engine_time: Any  # engine-run hours (float/Decimal/None)


@dataclass(frozen=True)
class PilotSide:
    """The pilot-log view of a flight."""

    departure_icao: str | None
    arrival_icao: str | None
    departure_time: time | None
    arrival_time: time | None
    block_time: Any  # SE + ME + multi-pilot hours (float/Decimal/None)
    landings_day: int | None
    landings_night: int | None


def _place(value: str | None) -> str | None:
    """Upper-cased place code, or None when unknown (blank or "ZZZZ")."""
    clean = (value or "").strip().upper()
    return None if not clean or clean == "ZZZZ" else clean


def _hours(value: Any) -> float | None:
    return None if value is None else float(value)


def airframe_never_airborne(side: AirframeSide) -> bool:
    """The airframe log positively records an engine run without a flight:
    zero airborne time, no landings, no takeoff/landing clock times, and a
    positive engine time."""
    flight_time = _hours(side.flight_time)
    engine_time = _hours(side.engine_time)
    return (
        side.takeoff_time is None
        and side.landing_time is None
        and flight_time is not None
        and abs(flight_time) < _EPS
        and not side.landing_count
        and engine_time is not None
        and engine_time > 0
    )


def pilot_no_landings(side: PilotSide) -> bool:
    """The pilot log records block time but no landings and no block clock
    times — consistent with an engine run that never took off."""
    block = _hours(side.block_time)
    return (
        side.departure_time is None
        and side.arrival_time is None
        and not side.landings_day
        and not side.landings_night
        and block is not None
        and block > 0
    )


def is_no_takeoff_pair(airframe: AirframeSide, pilot: PilotSide) -> bool:
    """True when *airframe* and *pilot* are the same cancelled-before-takeoff
    engine run (see the module docstring). The caller is responsible for
    both being on the same date and aircraft."""
    if not (airframe_never_airborne(airframe) and pilot_no_landings(pilot)):
        return False
    route_af = (_place(airframe.departure_icao), _place(airframe.arrival_icao))
    route_pl = (_place(pilot.departure_icao), _place(pilot.arrival_icao))
    if None in route_af or route_af != route_pl:
        return False
    # Both are known and positive here — checked by the two predicates above.
    difference = float(pilot.block_time) - float(airframe.engine_time)
    return abs(difference) <= NO_TAKEOFF_TIME_TOLERANCE + _EPS


def airframe_side_of_flight(flight: Any) -> AirframeSide:
    """AirframeSide of an existing Flight row."""
    return AirframeSide(
        departure_icao=flight.departure_icao,
        arrival_icao=flight.arrival_icao,
        takeoff_time=flight.takeoff_time,
        landing_time=flight.landing_time,
        flight_time=flight.flight_time,
        landing_count=flight.landing_count,
        engine_time=flight.engine_time,
    )


def pilot_side_of_flight(flight: Any) -> PilotSide:
    """PilotSide of an existing Flight row."""
    return PilotSide(
        departure_icao=flight.departure_icao,
        arrival_icao=flight.arrival_icao,
        departure_time=flight.departure_time,
        arrival_time=flight.arrival_time,
        block_time=flight.total_flight_time,
        landings_day=flight.landings_day,
        landings_night=flight.landings_night,
    )
