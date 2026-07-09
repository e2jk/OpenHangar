"""Engine / propeller TBO and life-limited component tracking.

A component may carry an hours limit (tbo_hours — time between overhauls or
an hours life limit) and/or a calendar limit (life_limit_date, e.g. 12-year
rubber hoses).  This module computes, per component:

  - total component hours (time_at_install + the aircraft's flight-counter
    deltas inside the installation window)
  - hours since the last recorded overhaul (overhauled_at_hours resets the
    reference point)
  - a status: 'overdue' | 'due_soon' | 'ok'

Hours limits warn inside the last 10 % of the interval; calendar limits warn
90 days ahead (mirroring the airworthiness document window).
"""

from datetime import date as _date, timedelta
from typing import Any

CALENDAR_WARN_DAYS = 90
HOURS_WARN_FRACTION = 0.1


def component_hours(comp: Any) -> float:
    """Total hours on the component: time at install + flight deltas since."""
    from models import FlightEntry, db  # pyright: ignore[reportMissingImports]

    query = db.session.query(
        db.func.sum(
            FlightEntry.flight_time_counter_end - FlightEntry.flight_time_counter_start
        )
    ).filter(
        FlightEntry.aircraft_id == comp.aircraft_id,
        FlightEntry.flight_time_counter_end.isnot(None),
        FlightEntry.flight_time_counter_start.isnot(None),
    )
    if comp.installed_at:
        query = query.filter(FlightEntry.date >= comp.installed_at)
    if comp.removed_at:
        query = query.filter(FlightEntry.date <= comp.removed_at)
    flown = float(query.scalar() or 0)
    return round(float(comp.time_at_install or 0) + flown, 1)


def component_limit_info(
    comp: Any, today: "_date | None" = None
) -> "dict[str, Any] | None":
    """Limit status for one component, or None when it has no limits set.

    Returns a dict with: component, total_hours, since_overhaul, tbo_hours,
    tbo_remaining, life_limit_date, status.
    """
    tbo = float(comp.tbo_hours) if comp.tbo_hours is not None else None
    limit_date = comp.life_limit_date
    if tbo is None and limit_date is None:
        return None
    if today is None:
        today = _date.today()

    statuses = []
    total_hours = component_hours(comp)
    since_overhaul = round(total_hours - float(comp.overhauled_at_hours or 0), 1)
    tbo_remaining = None
    if tbo is not None:
        tbo_remaining = round(tbo - since_overhaul, 1)
        if tbo_remaining <= 0:
            statuses.append("overdue")
        elif tbo_remaining <= tbo * HOURS_WARN_FRACTION:
            statuses.append("due_soon")
    if limit_date is not None:
        if limit_date < today:
            statuses.append("overdue")
        elif limit_date <= today + timedelta(days=CALENDAR_WARN_DAYS):
            statuses.append("due_soon")

    if "overdue" in statuses:
        status = "overdue"
    elif "due_soon" in statuses:
        status = "due_soon"
    else:
        status = "ok"
    return {
        "component": comp,
        "total_hours": total_hours,
        "since_overhaul": since_overhaul,
        "tbo_hours": tbo,
        "tbo_remaining": tbo_remaining,
        "life_limit_date": limit_date,
        "status": status,
    }


def aircraft_limit_infos(
    ac: Any, today: "_date | None" = None
) -> "list[dict[str, Any]]":
    """Limit info for every currently installed, limited component of ac."""
    infos = []
    for comp in ac.components:
        if comp.removed_at is not None:
            continue
        info = component_limit_info(comp, today)
        if info is not None:
            infos.append(info)
    return infos


def fleet_limit_statuses(
    aircraft_list: Any, today: "_date | None" = None
) -> "dict[int, str]":
    """Worst component-limit status per aircraft ('overdue'|'due_soon'|'ok')."""
    result: dict[int, str] = {}
    for ac in aircraft_list:
        statuses = [info["status"] for info in aircraft_limit_infos(ac, today)]
        if "overdue" in statuses:
            result[ac.id] = "overdue"
        elif "due_soon" in statuses:
            result[ac.id] = "due_soon"
        else:
            result[ac.id] = "ok"
    return result
