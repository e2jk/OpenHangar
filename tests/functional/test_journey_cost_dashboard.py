"""J6 — Expenses to cost dashboard (docs/functional_test_plan.md).

Intent: the wet-rate figure an owner sees is the correct consequence of
the expenses and flights they entered.

Deviation from the plan (documented per its own "deviate only with a
documented reason" rule, discovered while writing this journey): a
per-flight expense (`Expense.flight_entry_id`) has no HTTP route anywhere
in the app — `POST /aircraft/<id>/expenses/add` (`_validate_and_save`,
app/expenses/routes.py) never reads a `flight_id`/`flight_entry_id` form
field, and no other route sets it either (confirmed by grep — it's only
ever constructed directly in Python: seed helpers and existing tests).
The landing fee is therefore created with a sanctioned direct write (per
the plan's own exception for "things the UI cannot create"), with this
comment as the one-line reason.

`period=N` resolves to a *rolling window ending today* (`resolve_period`,
app/expenses/cost_dashboard.py), not a fixed calendar range, so this
journey dates its flights/expenses relative to `date.today()` rather than
literal 2024 dates — otherwise a bounded period query (period=3/6/12)
would never include them.

Existing partial coverage: tests/test_cost_dashboard.py unit-tests
`_compute_stats`-level maths; figures-as-rendered after route-driven data
entry are new.
"""

from datetime import date, timedelta

from tests.functional.conftest import log_flight, submit

# Fixed: 1200.00 insurance over a 200-day coverage span ending today ->
# fully counted (unprorated) at period=0 ("all time").
_FIXED_AMOUNT = "1200.00"
_COVERAGE_DAYS = 200
# Operating: 300.00 fuel, no coverage span -> never prorated.
_OPERATING_AMOUNT = "300.00"
# 10.0 h flown (two 5.0 h legs) inside the period.
# fixed/hour = 1200.00 / 10 = 120.00; operating/hour = 300.00 / 10 = 30.00;
# wet = fixed + operating + reserve(0) = 1500.00 -> wet/hour = 150.00.
_FIXED_PER_HOUR = "120.00"
_OPERATING_PER_HOUR = "30.00"
_WET_PER_HOUR = "150.00"


def test_expenses_to_cost_dashboard(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id
    today = date.today()

    # Fixed expense (annual insurance, pro-rated span) fully inside the
    # all-time window.
    coverage_start = today - timedelta(days=_COVERAGE_DAYS - 1)
    submit(
        client,
        f"/aircraft/{aircraft_id}/expenses/add",
        {
            "date": today.isoformat(),
            "expense_type": "insurance",
            "expense_category": "fixed",
            "amount": _FIXED_AMOUNT,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": today.isoformat(),
        },
    )

    # Operating expense (fuel, no span -> never prorated).
    submit(
        client,
        f"/aircraft/{aircraft_id}/expenses/add",
        {
            "date": today.isoformat(),
            "expense_type": "fuel",
            "expense_category": "operating",
            "amount": _OPERATING_AMOUNT,
        },
    )

    # 10.0 h flown inside the period: two legs, 5.0 h each.
    leg1_date = today - timedelta(days=10)
    leg2_date = today - timedelta(days=9)
    log_flight(
        client,
        app,
        aircraft_id,
        date=leg1_date.isoformat(),
        flight_time_counter_start="1000.0",
        flight_time_counter_end="1005.0",
    )
    fe2_id = log_flight(
        client,
        app,
        aircraft_id,
        date=leg2_date.isoformat(),
        flight_time_counter_start="1005.0",
        flight_time_counter_end="1010.0",
    )

    # All-time view: fixed/hour 120.00, operating/hour 30.00, wet 150.00.
    dashboard = client.get(f"/aircraft/{aircraft_id}/costs?period=0")
    assert dashboard.status_code == 200
    body = dashboard.data.decode()
    assert _FIXED_PER_HOUR in body
    assert _OPERATING_PER_HOUR in body
    assert _WET_PER_HOUR in body

    # A per-flight landing fee linked to the second flight — must stay
    # excluded from the rate entirely (see module docstring for why this
    # is a direct write).
    with app.app_context():
        from models import Expense, db  # pyright: ignore[reportMissingImports]

        landing_fee = Expense(
            aircraft_id=aircraft_id,
            date=leg2_date,
            expense_type="other",
            expense_category="operating",
            amount=15.00,
            flight_entry_id=fe2_id,
        )
        db.session.add(landing_fee)
        db.session.commit()

    dashboard_after_fee = client.get(f"/aircraft/{aircraft_id}/costs?period=0")
    body_after_fee = dashboard_after_fee.data.decode()
    assert _WET_PER_HOUR in body_after_fee
    assert _OPERATING_PER_HOUR in body_after_fee  # unchanged — fee excluded
    assert "15.00" not in body_after_fee or "115.00" not in body_after_fee

    # A second, bounded period pro-rates the fixed cost: period=3 resolves
    # to a round(3*365/12)=91-day lookback (resolve_period), i.e.
    # [today-91, today] — overlapping the last 92 days (inclusive) of the
    # 200-day coverage span. 1200.00 * 92/200 = 552.00.
    period3 = client.get(f"/aircraft/{aircraft_id}/costs?period=3")
    assert period3.status_code == 200
    assert "552.00" in period3.data.decode()
