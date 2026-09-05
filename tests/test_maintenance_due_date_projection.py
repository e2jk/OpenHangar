"""
Tests for backlog item: maintenance due-date projection from utilization trend.
"""

from datetime import date, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from maintenance.due_date_projection import (  # pyright: ignore[reportMissingImports]
    project_due_date,
    weekly_utilization_rate,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    HoursBasis,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from reports.utilization import (  # pyright: ignore[reportMissingImports]
    flight_hours_flown,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_tenant(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="pilot@example.com",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return tenant.id


def _add_aircraft(app, tenant_id):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration="OO-TST", make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id, flight_date, engine_time=2.0, flight_time=2.0):
    with app.app_context():
        f = Flight(
            aircraft_id=aircraft_id,
            date=flight_date,
            departure_icao="EBOS",
            arrival_icao="EBBR",
            engine_time=engine_time,
            flight_time=flight_time,
        )
        db.session.add(f)
        db.session.commit()
        return f.id


# ── flight_hours_flown ───────────────────────────────────────────────────────


class TestFlightHoursFlown:
    def test_prefers_flight_time_over_counter_delta(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, date(2024, 6, 1), flight_time=1.5)
        with app.app_context():
            hours = flight_hours_flown(acid, date(2024, 1, 1), date(2024, 12, 31))
        assert hours == 1.5

    def test_falls_back_to_counter_delta(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            f = Flight(
                aircraft_id=acid,
                date=date(2024, 6, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=None,
                flight_time_counter_start=100.0,
                flight_time_counter_end=101.5,
            )
            db.session.add(f)
            db.session.commit()
        with app.app_context():
            hours = flight_hours_flown(acid, date(2024, 1, 1), date(2024, 12, 31))
        assert hours == 1.5

    def test_ignores_flights_with_neither(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            f = Flight(
                aircraft_id=acid,
                date=date(2024, 6, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=None,
            )
            db.session.add(f)
            db.session.commit()
        with app.app_context():
            hours = flight_hours_flown(acid, date(2024, 1, 1), date(2024, 12, 31))
        assert hours == 0.0


# ── weekly_utilization_rate ──────────────────────────────────────────────────


class TestWeeklyUtilizationRate:
    def test_none_with_too_few_flights(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date.today()
        _add_flight(app, acid, today)
        _add_flight(app, acid, today - timedelta(days=5))
        with app.app_context():
            rate = weekly_utilization_rate(acid, HoursBasis.ENGINE, today=today)
        assert rate is None

    def test_none_when_flights_do_not_span_enough_days(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date.today()
        # 3 flights (meets MIN_FLIGHTS) but all within a couple of days
        # (fails the MIN_SPAN_DAYS guard).
        for offset in (0, 1, 2):
            _add_flight(app, acid, today - timedelta(days=offset))
        with app.app_context():
            rate = weekly_utilization_rate(acid, HoursBasis.ENGINE, today=today)
        assert rate is None

    def test_computes_rate_with_enough_history(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date.today()
        for days_ago in (0, 10, 20, 30):
            _add_flight(app, acid, today - timedelta(days=days_ago), engine_time=2.0)
        with app.app_context():
            rate = weekly_utilization_rate(acid, HoursBasis.ENGINE, today=today)
        assert rate is not None
        assert rate > 0

    def test_uses_flight_basis_when_requested(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date.today()
        for days_ago in (0, 10, 20, 30):
            _add_flight(
                app,
                acid,
                today - timedelta(days=days_ago),
                engine_time=2.0,
                flight_time=1.0,
            )
        with app.app_context():
            engine_rate = weekly_utilization_rate(acid, HoursBasis.ENGINE, today=today)
            flight_rate = weekly_utilization_rate(acid, HoursBasis.FLIGHT, today=today)
        # Same flight count/span, different hours summed (2.0 vs 1.0 per
        # flight) — the two bases must not collapse to the same rate.
        assert engine_rate is not None
        assert flight_rate is not None
        assert engine_rate > flight_rate

    def test_none_when_no_hours_logged(self, app):
        tid = _create_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date.today()
        for days_ago in (0, 10, 20, 30):
            with app.app_context():
                db.session.add(
                    Flight(
                        aircraft_id=acid,
                        date=today - timedelta(days=days_ago),
                        departure_icao="EBOS",
                        arrival_icao="EBBR",
                    )
                )
                db.session.commit()
        with app.app_context():
            rate = weekly_utilization_rate(acid, HoursBasis.ENGINE, today=today)
        assert rate is None


# ── project_due_date ─────────────────────────────────────────────────────────


class TestProjectDueDate:
    def test_none_without_a_rate(self):
        assert project_due_date(100.0, 200.0, None) is None
        assert project_due_date(100.0, 200.0, 0.0) is None

    def test_none_without_current_hours(self):
        assert project_due_date(None, 200.0, 5.0) is None

    def test_today_when_already_at_or_past_due(self):
        assert project_due_date(200.0, 200.0, 5.0) == date.today()
        assert project_due_date(250.0, 200.0, 5.0) == date.today()

    def test_projects_a_future_date(self):
        # 100 h remaining at 10 h/week -> 10 weeks -> 70 days out.
        result = project_due_date(100.0, 200.0, 10.0)
        assert result == date.today() + timedelta(days=70)
