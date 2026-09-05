"""Backlog: maintenance due-date projection from utilization trend.

Hours-based triggers show "due at X h", but an owner plans on a calendar.
This computes a rolling utilization rate (hours/week) from an aircraft's
recent flight history and projects the calendar date at which an
hours-based trigger will reach its due value — always an estimate, never
a substitute for the real hours-based due figure.

Pure calculation functions, kept independent of Flask routing (mirrors the
style of reports/utilization.py) so they can be unit-tested deterministically.
"""

from datetime import date as _date
from datetime import timedelta

from models import HoursBasis  # pyright: ignore[reportMissingImports]
from reports.utilization import (  # pyright: ignore[reportMissingImports]
    engine_hours_flown,
    flight_hours_flown,
    flights_in_period,
)

__all__ = ["project_due_date", "weekly_utilization_rate"]

# Trailing window the rate is computed over.
WINDOW_DAYS = 90
# Minimum-data guard (see backlog item): an aircraft flown only once or
# twice in the window produces a meaningless trend — require at least this
# many flights, spanning at least MIN_SPAN_DAYS, before trusting the rate.
MIN_FLIGHTS = 3
MIN_SPAN_DAYS = 14


def weekly_utilization_rate(
    aircraft_id: int, hours_basis: str, today: "_date | None" = None
) -> "float | None":
    """Average hours/week over the trailing WINDOW_DAYS, or None if there
    isn't enough recent flight history to trust a trend."""
    today = today or _date.today()
    period_start = today - timedelta(days=WINDOW_DAYS)
    flights = flights_in_period(aircraft_id, period_start, today)
    if len(flights) < MIN_FLIGHTS:
        return None
    flight_dates = [f.date for f in flights]
    if (max(flight_dates) - min(flight_dates)).days < MIN_SPAN_DAYS:
        return None
    hours = (
        flight_hours_flown(aircraft_id, period_start, today)
        if hours_basis == HoursBasis.FLIGHT
        else engine_hours_flown(aircraft_id, period_start, today)
    )
    if hours <= 0:
        return None
    return hours / (WINDOW_DAYS / 7.0)


def project_due_date(
    current_hours: "float | None", due_hours: float, weekly_rate: "float | None"
) -> "_date | None":
    """Calendar date at which current_hours reaches due_hours, projecting
    forward at weekly_rate hours/week. None if there's no rate to project
    from, or no current-hours reading to project from."""
    if weekly_rate is None or weekly_rate <= 0 or current_hours is None:
        return None
    remaining = due_hours - current_hours
    if remaining <= 0:
        return _date.today()
    weeks = remaining / weekly_rate
    return _date.today() + timedelta(days=round(weeks * 7))
