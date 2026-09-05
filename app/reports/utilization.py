"""Backlog: annual utilization & insurance-renewal summary.

Pure calculation functions, kept independent of Flask routing so they can be
unit-tested deterministically (mirrors the style of expenses/cost_dashboard.py,
whose resolve_period() this module reuses directly).
"""

from datetime import date as _date
from datetime import timedelta
from typing import Any

from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    oil_added,
    resolve_period,
)
from models import Flight, Refuel  # pyright: ignore[reportMissingImports]

DEFAULT_PERIOD_MONTHS = 12
PERIOD_OPTIONS = (3, 6, 12, 24, 0)  # months; 0 = all time

__all__ = [
    "DEFAULT_PERIOD_MONTHS",
    "PERIOD_OPTIONS",
    "compute_utilization_report",
    "engine_hours_flown",
    "flight_hours_flown",
    "flights_in_period",
    "fuel_added",
    "resolve_period",
]


def flights_in_period(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> list[Flight]:
    query = Flight.query.filter(
        Flight.aircraft_id == aircraft_id, Flight.date <= period_end
    )
    if period_start is not None:
        query = query.filter(Flight.date >= period_start)
    return query.all()  # type: ignore[no-any-return]


def engine_hours_flown(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> float:
    """Sum of engine hours for flights within [period_start, period_end].

    Mirrors cost_dashboard.hours_flown()'s "prefer the directly-logged
    figure over the counter delta" rule, applied to engine_time instead of
    flight_time."""
    flights = flights_in_period(aircraft_id, period_start, period_end)
    return sum(
        float(f.engine_time)
        if f.engine_time is not None
        else float(f.engine_time_counter_end) - float(f.engine_time_counter_start)
        for f in flights
        if f.engine_time is not None
        or (
            f.engine_time_counter_end is not None
            and f.engine_time_counter_start is not None
        )
    )


def flight_hours_flown(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> float:
    """Sum of flight hours for flights within [period_start, period_end].

    Mirrors engine_hours_flown()'s "prefer the directly-logged figure over
    the counter delta" rule, applied to flight_time instead of engine_time.
    """
    flights = flights_in_period(aircraft_id, period_start, period_end)
    return sum(
        float(f.flight_time)
        if f.flight_time is not None
        else float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
        for f in flights
        if f.flight_time is not None
        or (
            f.flight_time_counter_end is not None
            and f.flight_time_counter_start is not None
        )
    )


def fuel_added(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> dict[str, float]:
    """Total fuel added within [period_start, period_end], keyed by unit.

    Combines a flight's independent before/after top-ups with standalone
    Refuel records (not tied to any flight) — both are equally "fuel
    added" for a utilization report. Kept per-unit rather than converted,
    since a mix of L and gal entries has no unambiguous single total."""
    totals: dict[str, float] = {}

    def _add(qty: Any, unit: str | None) -> None:
        if qty is None:
            return
        u = unit or "L"
        totals[u] = totals.get(u, 0.0) + float(qty)

    for f in flights_in_period(aircraft_id, period_start, period_end):
        _add(f.fuel_added_before_qty, f.fuel_added_before_unit)
        _add(f.fuel_added_after_qty, f.fuel_added_after_unit)

    refuel_query = Refuel.query.filter(
        Refuel.aircraft_id == aircraft_id, Refuel.date <= period_end
    )
    if period_start is not None:
        refuel_query = refuel_query.filter(Refuel.date >= period_start)
    for r in refuel_query.all():
        _add(r.quantity, r.unit)

    return {u: round(v, 1) for u, v in totals.items()}


def _period_stats(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> dict[str, Any]:
    flights = flights_in_period(aircraft_id, period_start, period_end)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "flight_count": len(flights),
        "flight_hours": round(
            flight_hours_flown(aircraft_id, period_start, period_end), 1
        ),
        "engine_hours": round(
            engine_hours_flown(aircraft_id, period_start, period_end), 1
        ),
        "landings": sum(f.landing_count or 0 for f in flights),
        "fuel_added": fuel_added(aircraft_id, period_start, period_end),
        "oil_added_l": oil_added(aircraft_id, period_start, period_end),
    }


def compute_utilization_report(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> dict[str, Any]:
    """Utilization for [period_start, period_end], plus the immediately
    preceding period of the same length for comparison — insurers commonly
    ask for both hours flown in the past policy year and expected hours
    for the next; the prior-period figure is the honest baseline for that
    without inventing a forecast."""
    current = _period_stats(aircraft_id, period_start, period_end)

    previous = None
    if period_start is not None:
        length_days = (period_end - period_start).days
        prev_end = period_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length_days)
        previous = _period_stats(aircraft_id, prev_start, prev_end)

    return {
        "period_start": period_start,
        "period_end": period_end,
        "current": current,
        "previous": previous,
    }
