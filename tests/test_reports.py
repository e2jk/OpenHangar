"""
Tests for backlog item: annual utilization & insurance-renewal summary.
"""

from datetime import date, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    Refuel,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from reports.utilization import (  # pyright: ignore[reportMissingImports]
    compute_utilization_report,
    engine_hours_flown,
    fuel_added,
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


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id, **kwargs):
    with app.app_context():
        fe = Flight(
            aircraft_id=aircraft_id,
            date=kwargs.pop("flight_date", None) or date.today(),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            **kwargs,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _add_refuel(app, aircraft_id, quantity=40.0, unit="L", refuel_date=None):
    with app.app_context():
        r = Refuel(
            aircraft_id=aircraft_id,
            date=refuel_date or date.today(),
            quantity=quantity,
            unit=unit,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


# ── Pure calculation: engine_hours_flown ────────────────────────────────────


class TestEngineHoursFlown:
    def test_prefers_direct_engine_time_over_counters(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(
            app,
            ac_id,
            engine_time=3.5,
            engine_time_counter_start=100.0,
            engine_time_counter_end=999.0,
        )
        with app.app_context():
            hours = engine_hours_flown(ac_id, None, date.today())
            assert hours == 3.5

    def test_falls_back_to_counter_delta(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(
            app, ac_id, engine_time_counter_start=100.0, engine_time_counter_end=102.5
        )
        with app.app_context():
            hours = engine_hours_flown(ac_id, None, date.today())
            assert hours == 2.5

    def test_flight_with_no_engine_data_excluded(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id)
        with app.app_context():
            hours = engine_hours_flown(ac_id, None, date.today())
            assert hours == 0.0

    def test_respects_period_bounds(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        old_date = date.today() - timedelta(days=400)
        _add_flight(app, ac_id, engine_time=1.0, flight_date=old_date)
        _add_flight(app, ac_id, engine_time=2.0)
        with app.app_context():
            hours = engine_hours_flown(
                ac_id, date.today() - timedelta(days=30), date.today()
            )
            assert hours == 2.0


# ── Pure calculation: fuel_added ────────────────────────────────────────────


class TestFuelAdded:
    def test_combines_before_after_and_refuel(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(
            app,
            ac_id,
            fuel_added_before_qty=10.0,
            fuel_added_before_unit="L",
            fuel_added_after_qty=15.0,
            fuel_added_after_unit="L",
        )
        _add_refuel(app, ac_id, quantity=5.0, unit="L")
        with app.app_context():
            totals = fuel_added(ac_id, None, date.today())
            assert totals == {"L": 30.0}

    def test_keeps_units_separate(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, fuel_added_before_qty=10.0, fuel_added_before_unit="L")
        _add_refuel(app, ac_id, quantity=20.0, unit="gal")
        with app.app_context():
            totals = fuel_added(ac_id, None, date.today())
            assert totals == {"L": 10.0, "gal": 20.0}

    def test_empty_when_no_fuel_logged(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id)
        with app.app_context():
            totals = fuel_added(ac_id, None, date.today())
            assert totals == {}

    def test_respects_period_bounds(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        old_date = date.today() - timedelta(days=400)
        _add_refuel(app, ac_id, quantity=99.0, refuel_date=old_date)
        _add_refuel(app, ac_id, quantity=5.0)
        with app.app_context():
            totals = fuel_added(ac_id, date.today() - timedelta(days=30), date.today())
            assert totals == {"L": 5.0}


# ── Pure calculation: compute_utilization_report ────────────────────────────


class TestComputeUtilizationReport:
    def test_current_period_stats(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(
            app,
            ac_id,
            flight_time=2.0,
            engine_time=2.2,
            landing_count=3,
        )
        with app.app_context():
            today = date.today()
            report = compute_utilization_report(
                ac_id, today - timedelta(days=30), today
            )
            c = report["current"]
            assert c["flight_count"] == 1
            assert c["flight_hours"] == 2.0
            assert c["engine_hours"] == 2.2
            assert c["landings"] == 3

    def test_previous_period_is_same_length_immediately_before(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            today = date.today()
            period_start = today - timedelta(days=9)  # 10-day window
            report = compute_utilization_report(ac_id, period_start, today)
            prev = report["previous"]
            assert prev is not None
            assert prev["period_end"] == period_start - timedelta(days=1)
            expected_len = (today - period_start).days
            assert (prev["period_end"] - prev["period_start"]).days == expected_len

    def test_all_time_has_no_previous_period(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            report = compute_utilization_report(ac_id, None, date.today())
            assert report["previous"] is None

    def test_landings_default_to_zero_when_unset(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, flight_time=1.0)
        with app.app_context():
            report = compute_utilization_report(ac_id, None, date.today())
            assert report["current"]["landings"] == 0


# ── Route: utilization report page ──────────────────────────────────────────


class TestUtilizationReportRoute:
    def test_renders(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert resp.status_code == 200

    def test_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert resp.status_code == 302

    def test_404_wrong_tenant(self, client, app):
        _, _t1 = _create_user_and_tenant(app, "owner@example.com")
        _, t2 = _create_user_and_tenant(app, "other@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "owner@example.com")
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert resp.status_code == 404

    def test_invalid_period_falls_back_to_default(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization?period=not-a-number")
        assert resp.status_code == 200

    def test_shows_flight_and_engine_hours(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, flight_time=4.5, engine_time=5.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert b"4.5" in resp.data
        assert b"5.0" in resp.data

    def test_custom_date_range(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, flight_time=1.0, flight_date=date(2025, 6, 15))
        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_id}/reports/utilization?from=2025-06-01&to=2025-06-30"
        )
        assert resp.status_code == 200
        assert b"2025-06-01" in resp.data
        assert b"2025-06-30" in resp.data
        assert b"1.0" in resp.data

    def test_custom_range_excludes_out_of_range_flight(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, flight_time=9.0, flight_date=date(2024, 1, 1))
        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_id}/reports/utilization?from=2025-06-01&to=2025-06-30"
        )
        assert resp.status_code == 200
        assert b"No flights logged in the selected period" in resp.data

    def test_invalid_custom_range_falls_back_to_preset(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_id}/reports/utilization?from=2025-06-30&to=2025-06-01"
        )
        assert resp.status_code == 200

    def test_malformed_custom_range_falls_back_to_preset(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(
            f"/aircraft/{ac_id}/reports/utilization?from=not-a-date&to=2025-06-01"
        )
        assert resp.status_code == 200

    def test_403_when_user_has_no_tenant(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            orphan = User(
                email="orphan@example.com",
                password_hash=_pw_hash.hash("x"),
                is_active=True,
            )
            db.session.add(orphan)
            db.session.commit()
            uid = orphan.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert resp.status_code == 403

    def test_zero_flights_shows_empty_state(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization")
        assert b"No flights logged in the selected period" in resp.data

    def test_linked_from_aircraft_detail_page(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id, registration="OO-UTL")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert b"/aircraft/OO-UTL/reports/utilization" in resp.data


# ── Route: CSV export ────────────────────────────────────────────────────────


class TestUtilizationReportCsv:
    def test_renders_csv(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_flight(app, ac_id, flight_time=3.0, engine_time=3.2, landing_count=2)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert "attachment" in resp.headers["Content-Disposition"]
        body = resp.data.decode()
        assert "3.0" in body
        assert "3.2" in body

    def test_requires_login(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv")
        assert resp.status_code == 302

    def test_404_wrong_tenant(self, client, app):
        _, _t1 = _create_user_and_tenant(app, "owner@example.com")
        _, t2 = _create_user_and_tenant(app, "other@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "owner@example.com")
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv")
        assert resp.status_code == 404

    def test_csv_includes_previous_period_column(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv?period=12")
        body = resp.data.decode()
        assert "Previous period" in body

    def test_csv_all_time_has_no_previous_period_column(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv?period=0")
        body = resp.data.decode()
        assert "Previous period" not in body

    def test_csv_includes_fuel_added(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_refuel(app, ac_id, quantity=42.0, unit="L")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/reports/utilization.csv")
        assert "42.0 L" in resp.data.decode()
