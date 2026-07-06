"""
Tests for Phase 36: Aircraft Operating Cost Dashboard.
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Expense,
    ExpenseCategory,
    ExpenseType,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)

from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    DEFAULT_PERIOD_MONTHS,
    PERIOD_OPTIONS,
    _in_period,
    _prorated_amount,
    compute_cost_dashboard,
    hours_flown,
    resolve_period,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, reserve_hourly_rate=None):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration="OO-TST",
            make="Cessna",
            model="172S",
            reserve_hourly_rate=reserve_hourly_rate,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_expense(
    app,
    aircraft_id,
    expense_type=ExpenseType.FUEL,
    expense_category=ExpenseCategory.OPERATING,
    amount=100.0,
    exp_date=None,
    coverage_start=None,
    coverage_end=None,
    flight_entry_id=None,
):
    with app.app_context():
        exp = Expense(
            aircraft_id=aircraft_id,
            date=exp_date or date.today(),
            expense_type=expense_type,
            expense_category=expense_category,
            amount=amount,
            currency="EUR",
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            flight_entry_id=flight_entry_id,
        )
        db.session.add(exp)
        db.session.commit()
        return exp.id


def _add_flight(app, aircraft_id, hobbs_start=100.0, hobbs_end=101.5, flight_date=None):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date.today(),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=hobbs_start,
            flight_time_counter_end=hobbs_end,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


# ── Pure calculation: resolve_period ──────────────────────────────────────────


class TestResolvePeriod:
    def test_default_12_months_is_exactly_365_days(self):
        today = date(2026, 7, 6)
        start, end = resolve_period(12, today)
        assert end == today
        assert (end - start).days == 365

    def test_all_time_has_no_lower_bound(self):
        today = date(2026, 7, 6)
        start, end = resolve_period(0, today)
        assert start is None
        assert end == today

    def test_3_months_is_proportional(self):
        today = date(2026, 7, 6)
        start, end = resolve_period(3, today)
        assert (end - start).days == round(3 * 365 / 12)


# ── Pure calculation: proration ───────────────────────────────────────────────


class TestProratedAmount:
    def test_no_coverage_span_counts_in_full(self):
        exp = Expense(amount=100.0, coverage_start=None, coverage_end=None)
        result = _prorated_amount(exp, date(2026, 1, 1), date(2026, 12, 31))
        assert result == 100.0

    def test_full_overlap_counts_in_full(self):
        exp = Expense(
            amount=1200.0,
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 12, 31),
        )
        result = _prorated_amount(exp, date(2025, 1, 1), date(2025, 12, 31))
        assert result == 1200.0

    def test_annual_premium_prorated_to_half_year_window(self):
        """Spec example: an annual premium paid in January contributes only
        the fraction of its value that overlaps a Jul-Dec report window."""
        exp = Expense(
            amount=1200.0,
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 12, 31),
        )
        period_start, period_end = date(2025, 7, 1), date(2025, 12, 31)
        result = _prorated_amount(exp, period_start, period_end)
        overlap_days = (period_end - period_start).days + 1
        coverage_days = (date(2025, 12, 31) - date(2025, 1, 1)).days + 1
        expected = round(1200.0 * overlap_days / coverage_days, 2)
        assert result == expected
        assert 0 < result < 1200.0

    def test_no_overlap_counts_as_zero(self):
        exp = Expense(
            amount=1200.0,
            coverage_start=date(2024, 1, 1),
            coverage_end=date(2024, 12, 31),
        )
        result = _prorated_amount(exp, date(2025, 1, 1), date(2025, 12, 31))
        assert result == 0.0

    def test_all_time_window_uses_coverage_start_as_lower_bound(self):
        exp = Expense(
            amount=1200.0,
            coverage_start=date(2025, 1, 1),
            coverage_end=date(2025, 12, 31),
        )
        result = _prorated_amount(exp, None, date(2025, 12, 31))
        assert result == 1200.0

    def test_degenerate_coverage_span_counts_in_full(self):
        """coverage_end before coverage_start can't happen via form validation,
        but the function falls back to counting the amount in full rather than
        dividing by a non-positive span."""
        exp = Expense(
            amount=500.0,
            coverage_start=date(2025, 12, 31),
            coverage_end=date(2025, 1, 1),
        )
        result = _prorated_amount(exp, date(2025, 1, 1), date(2025, 12, 31))
        assert result == 500.0


# ── _in_period ─────────────────────────────────────────────────────────────────


class TestInPeriod:
    def test_coverage_span_starting_after_period_end_excluded(self):
        exp = Expense(coverage_start=date(2026, 1, 1), coverage_end=date(2026, 6, 30))
        assert _in_period(exp, date(2025, 1, 1), date(2025, 12, 31)) is False

    def test_coverage_span_ending_before_bounded_period_start_excluded(self):
        exp = Expense(coverage_start=date(2024, 1, 1), coverage_end=date(2024, 6, 30))
        assert _in_period(exp, date(2025, 1, 1), date(2025, 12, 31)) is False

    def test_plain_expense_dated_after_period_end_excluded(self):
        exp = Expense(date=date(2026, 1, 1), coverage_start=None, coverage_end=None)
        assert _in_period(exp, date(2025, 1, 1), date(2025, 12, 31)) is False

    def test_plain_expense_dated_before_bounded_period_start_excluded(self):
        exp = Expense(date=date(2024, 6, 1), coverage_start=None, coverage_end=None)
        assert _in_period(exp, date(2025, 1, 1), date(2025, 12, 31)) is False

    def test_plain_expense_within_period_included(self):
        exp = Expense(date=date(2025, 6, 1), coverage_start=None, coverage_end=None)
        assert _in_period(exp, date(2025, 1, 1), date(2025, 12, 31)) is True


# ── hours_flown ────────────────────────────────────────────────────────────────


class TestHoursFlown:
    def test_zero_when_no_flights(self, app):
        _, tenant_id = _create_user_and_tenant(app, "hf1@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            assert hours_flown(ac_id, None, date.today()) == 0.0

    def test_sums_counter_deltas_in_period(self, app):
        _, tenant_id = _create_user_and_tenant(app, "hf2@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=102.0)
        _add_flight(app, ac_id, hobbs_start=200.0, hobbs_end=201.5)
        with app.app_context():
            assert hours_flown(ac_id, None, date.today()) == 3.5

    def test_excludes_flights_outside_period(self, app):
        _, tenant_id = _create_user_and_tenant(app, "hf3@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        old_date = date.today() - timedelta(days=400)
        _add_flight(
            app, ac_id, hobbs_start=100.0, hobbs_end=102.0, flight_date=old_date
        )
        with app.app_context():
            start, end = resolve_period(12, date.today())
            assert hours_flown(ac_id, start, end) == 0.0


# ── compute_cost_dashboard ─────────────────────────────────────────────────────


class TestComputeCostDashboard:
    def test_zero_hours_edge_case(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd1@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=100.0)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["hours_flown"] == 0.0
            assert result["fixed_per_hour"] is None
            assert result["operating_per_hour"] is None
            assert result["wet_per_hour"] is None

    def test_fixed_and_operating_split(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd2@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(app, ac_id, amount=600.0, expense_category=ExpenseCategory.FIXED)
        _add_expense(
            app, ac_id, amount=200.0, expense_category=ExpenseCategory.OPERATING
        )
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=104.0)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["hours_flown"] == 4.0
            assert result["fixed_total"] == 600.0
            assert result["operating_total"] == 200.0
            assert result["fixed_per_hour"] == 150.0
            assert result["operating_per_hour"] == 50.0
            assert result["wet_total"] == 800.0
            assert result["wet_per_hour"] == 200.0

    def test_fuel_and_maintenance_subtotals(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd2b@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(
            app,
            ac_id,
            amount=120.0,
            expense_type=ExpenseType.FUEL,
            expense_category=ExpenseCategory.OPERATING,
        )
        _add_expense(
            app,
            ac_id,
            amount=80.0,
            expense_type=ExpenseType.PARTS,
            expense_category=ExpenseCategory.OPERATING,
        )
        _add_expense(
            app,
            ac_id,
            amount=1000.0,
            expense_type=ExpenseType.INSURANCE,
            expense_category=ExpenseCategory.FIXED,
        )
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=104.0)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["fuel_total"] == 120.0
            assert result["fuel_per_hour"] == 30.0
            assert result["maintenance_total"] == 80.0
            assert result["maintenance_per_hour"] == 20.0
            # Fixed (insurance) is excluded from the fuel/maintenance breakdown.
            assert result["operating_total"] == 200.0

    def test_reserve_not_configured(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd3@example.com")
        ac_id = _add_aircraft(app, tenant_id, reserve_hourly_rate=None)
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=101.0)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["reserve_per_hour"] is None
            assert result["reserve_total"] == 0.0

    def test_reserve_configured_contributes_to_wet_rate(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd4@example.com")
        ac_id = _add_aircraft(app, tenant_id, reserve_hourly_rate=20.0)
        _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=105.0)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["reserve_per_hour"] == 20.0
            assert result["reserve_total"] == 100.0
            assert result["wet_total"] == 100.0
            assert result["wet_per_hour"] == 20.0

    def test_per_flight_fee_excluded_from_rate(self, app):
        _, tenant_id = _create_user_and_tenant(app, "cd5@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        flight_id = _add_flight(app, ac_id, hobbs_start=100.0, hobbs_end=102.0)
        _add_expense(
            app,
            ac_id,
            amount=30.0,
            expense_category=ExpenseCategory.OPERATING,
            flight_entry_id=flight_id,
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)
            assert result["operating_total"] == 0.0
            assert result["excluded_per_flight_total"] == 30.0

    def test_annual_premium_prorated_with_deterministic_today(self, app):
        """A 6-month rolling window ending 2025-12-31 only partially overlaps
        an annual premium's Jan-Dec coverage span, so it should be prorated
        rather than counted in full — matching the expected ratio computed
        the same way compute_cost_dashboard does internally."""
        _, tenant_id = _create_user_and_tenant(app, "cd6@example.com")
        ac_id = _add_aircraft(app, tenant_id)
        coverage_start, coverage_end = date(2025, 1, 1), date(2025, 12, 31)
        _add_expense(
            app,
            ac_id,
            amount=1200.0,
            expense_category=ExpenseCategory.FIXED,
            exp_date=coverage_start,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
        _add_flight(
            app, ac_id, hobbs_start=0.0, hobbs_end=10.0, flight_date=date(2025, 12, 1)
        )
        today = date(2025, 12, 31)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            result = compute_cost_dashboard(ac, 6, today=today)
            period_start, period_end = resolve_period(6, today)
            overlap_days = (period_end - max(period_start, coverage_start)).days + 1
            coverage_days = (coverage_end - coverage_start).days + 1
            expected = round(1200.0 * overlap_days / coverage_days, 2)
            assert result["fixed_total"] == expected
            assert 0 < result["fixed_total"] < 1200.0

    def test_period_options_include_default(self):
        assert DEFAULT_PERIOD_MONTHS in PERIOD_OPTIONS


# ── Route: cost dashboard page ────────────────────────────────────────────────


class TestCostDashboardRoute:
    def test_renders(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert resp.status_code == 200

    def test_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert resp.status_code == 302

    def test_404_wrong_tenant(self, client, app):
        _, t1 = _create_user_and_tenant(app, "owner@example.com")
        _, t2 = _create_user_and_tenant(app, "other@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "owner@example.com")
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert resp.status_code == 404

    def test_invalid_period_falls_back_to_default(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs?period=not-a-number")
        assert resp.status_code == 200

    def test_unsupported_period_falls_back_to_default(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs?period=999")
        assert resp.status_code == 200

    def test_wet_rate_figure_shown(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(
            app, ac_id, amount=400.0, expense_category=ExpenseCategory.OPERATING
        )
        _add_flight(app, ac_id, hobbs_start=0.0, hobbs_end=4.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert b"100.00" in resp.data  # 400 / 4h = 100/h

    def test_fuel_and_maintenance_breakdown_shown(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_expense(
            app,
            ac_id,
            amount=120.0,
            expense_type=ExpenseType.FUEL,
            expense_category=ExpenseCategory.OPERATING,
        )
        _add_expense(
            app,
            ac_id,
            amount=80.0,
            expense_type=ExpenseType.PARTS,
            expense_category=ExpenseCategory.OPERATING,
        )
        _add_flight(app, ac_id, hobbs_start=0.0, hobbs_end=4.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert b"Fuel &amp; oil" in resp.data or "Fuel & oil".encode() in resp.data
        assert b"Variable maintenance" in resp.data
        assert b"120.00" in resp.data
        assert b"80.00" in resp.data

    def test_reserve_not_configured_shows_placeholder(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, reserve_hourly_rate=None)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert b"Not configured" in resp.data

    def test_zero_hours_shows_empty_state(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/costs")
        assert b"cannot be computed yet" in resp.data

    def test_cost_dashboard_linked_from_aircraft_detail(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert f"/aircraft/{ac_id}/costs".encode() in resp.data
