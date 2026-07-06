"""Phase 36: aircraft operating cost dashboard.

Pure calculation functions, kept independent of Flask routing so they can be
unit-tested deterministically (mirrors the style of app/pilots/currency.py).
"""

from datetime import date as _date, timedelta
from typing import Any

from models import Aircraft, Expense, ExpenseCategory, FlightEntry

DEFAULT_PERIOD_MONTHS = 12
PERIOD_OPTIONS = (3, 6, 12, 24, 0)  # 0 = all time


def resolve_period(period_months: int, today: _date) -> tuple[_date | None, _date]:
    """Return (period_start, period_end) for a given period-in-months selector.

    period_months <= 0 means "all time" (period_start is None). Otherwise the
    window is `round(period_months * 365 / 12)` days ending today, so the
    default 12-month option is exactly a rolling 365-day window.
    """
    period_end = today
    if period_months <= 0:
        return None, period_end
    days = round(period_months * 365 / 12)
    return period_end - timedelta(days=days), period_end


def hours_flown(
    aircraft_id: int, period_start: _date | None, period_end: _date
) -> float:
    """Sum of flight_time_counter deltas for flights within [period_start, period_end]."""
    query = FlightEntry.query.filter(
        FlightEntry.aircraft_id == aircraft_id,
        FlightEntry.date <= period_end,
    )
    if period_start is not None:
        query = query.filter(FlightEntry.date >= period_start)
    flights = query.all()
    return sum(
        float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
        for f in flights
        if f.flight_time_counter_end is not None
        and f.flight_time_counter_start is not None
    )


def _in_period(expense: Expense, period_start: _date | None, period_end: _date) -> bool:
    """True if the expense (or its coverage span) overlaps [period_start, period_end]."""
    if expense.coverage_start and expense.coverage_end:
        if expense.coverage_start > period_end:
            return False
        if period_start is not None and expense.coverage_end < period_start:
            return False
        return True
    if expense.date > period_end:
        return False
    if period_start is not None and expense.date < period_start:
        return False
    return True


def _prorated_amount(
    expense: Expense, period_start: _date | None, period_end: _date
) -> float:
    """Amount attributable to the report period, pro-rated by coverage-span overlap."""
    amount = float(expense.amount)
    coverage_start: _date | None = expense.coverage_start
    coverage_end: _date | None = expense.coverage_end
    if coverage_start is None or coverage_end is None:
        return amount

    coverage_days = (coverage_end - coverage_start).days + 1
    if coverage_days <= 0:
        return amount

    overlap_start = (
        coverage_start if period_start is None else max(coverage_start, period_start)
    )
    overlap_end = min(coverage_end, period_end)
    overlap_days = (overlap_end - overlap_start).days + 1
    if overlap_days <= 0:
        return 0.0
    return round(amount * overlap_days / coverage_days, 2)


def compute_cost_dashboard(
    aircraft: Aircraft, period_months: int, today: _date | None = None
) -> dict[str, Any]:
    """Compute the fixed/operating/reserve cost breakdown for one aircraft."""
    if today is None:
        today = _date.today()
    period_start, period_end = resolve_period(period_months, today)

    all_expenses = Expense.query.filter_by(aircraft_id=aircraft.id).all()
    in_period = [e for e in all_expenses if _in_period(e, period_start, period_end)]

    per_flight = [e for e in in_period if e.flight_entry_id is not None]
    counted = [e for e in in_period if e.flight_entry_id is None]

    fixed_total = round(
        sum(
            _prorated_amount(e, period_start, period_end)
            for e in counted
            if e.expense_category == ExpenseCategory.FIXED
        ),
        2,
    )
    operating_total = round(
        sum(
            _prorated_amount(e, period_start, period_end)
            for e in counted
            if e.expense_category != ExpenseCategory.FIXED
        ),
        2,
    )
    excluded_per_flight_total = round(sum(float(e.amount) for e in per_flight), 2)

    hours = hours_flown(aircraft.id, period_start, period_end)

    reserve_rate = (
        float(aircraft.reserve_hourly_rate)
        if aircraft.reserve_hourly_rate is not None
        else None
    )
    reserve_total = round(reserve_rate * hours, 2) if reserve_rate is not None else 0.0
    wet_total = round(fixed_total + operating_total + reserve_total, 2)

    def _per_hour(total: float) -> float | None:
        return round(total / hours, 2) if hours > 0 else None

    return {
        "period_start": period_start,
        "period_end": period_end,
        "hours_flown": hours,
        "fixed_total": fixed_total,
        "fixed_per_hour": _per_hour(fixed_total),
        "operating_total": operating_total,
        "operating_per_hour": _per_hour(operating_total),
        "reserve_per_hour": reserve_rate,
        "reserve_total": reserve_total,
        "wet_total": wet_total,
        "wet_per_hour": _per_hour(wet_total),
        "excluded_per_flight_total": excluded_per_flight_total,
    }
