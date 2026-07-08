"""
Tests for oil consumption tracking:
  - oil_added_l saved from the flight form (create + edit) with validation
  - oil_warning_lph saved from the aircraft form with validation
  - cost dashboard oil stats: total, L/h rate, and warning threshold
"""

from datetime import date

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    compute_cost_dashboard,
    oil_added,
)
from models import (
    Aircraft,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-OIL", **kwargs):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            **kwargs,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id, on, hours, oil_l=None):
    """Add a flight of `hours` flight-counter hours with optional oil top-up."""
    with app.app_context():
        prev_end = (
            db.session.query(db.func.max(FlightEntry.flight_time_counter_end))
            .filter(FlightEntry.aircraft_id == aircraft_id)
            .scalar()
            or 100.0
        )
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=on,
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=float(prev_end),
            flight_time_counter_end=float(prev_end) + hours,
            oil_added_l=oil_l,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _flight_form(acid, **kwargs):
    data = {
        "aircraft_id": str(acid),
        "date": "2024-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.5",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
    }
    data.update(kwargs)
    return data


class TestFlightFormOil:
    def test_oil_added_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=_flight_form(acid, oil_added_l="0.5"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert float(fe.oil_added_l) == 0.5

    def test_oil_blank_saved_as_none(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post("/flights/new", data=_flight_form(acid, oil_added_l=""))
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.oil_added_l is None

    def test_negative_oil_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post("/flights/new", data=_flight_form(acid, oil_added_l="-0.5"))
        assert resp.status_code == 200
        assert b"Oil added must be a non-negative number." in resp.data
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 0

    def test_non_numeric_oil_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post("/flights/new", data=_flight_form(acid, oil_added_l="a lot"))
        assert resp.status_code == 200
        assert b"Oil added must be a non-negative number." in resp.data

    def test_oil_updated_on_edit(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, date(2024, 6, 1), 1.5, oil_l=0.5)
        _login(app, client)
        resp = client.post(
            f"/flights/{fid}/edit",
            data=_flight_form(acid, oil_added_l="1.25"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            assert float(fe.oil_added_l) == 1.25


class TestAircraftFormOilThreshold:
    def _aircraft_form(self, **kwargs):
        data = {
            "registration": "OO-OIL",
            "make": "Cessna",
            "model": "172S",
        }
        data.update(kwargs)
        return data

    def test_threshold_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/aircraft/new",
            data=self._aircraft_form(oil_warning_lph="0.10"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            ac = Aircraft.query.filter_by(registration="OO-OIL").first()
            assert float(ac.oil_warning_lph) == 0.1

    def test_threshold_blank_saved_as_none(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        client.post("/aircraft/new", data=self._aircraft_form(oil_warning_lph=""))
        with app.app_context():
            ac = Aircraft.query.filter_by(registration="OO-OIL").first()
            assert ac.oil_warning_lph is None

    def test_invalid_threshold_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/aircraft/new", data=self._aircraft_form(oil_warning_lph="-1")
        )
        assert resp.status_code == 200
        assert (
            b"Oil consumption warning threshold must be a non-negative number."
            in resp.data
        )


class TestOilDashboardStats:
    def test_oil_added_sums_period_only(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, date(2024, 6, 1), 2.0, oil_l=0.5)
        _add_flight(app, acid, date(2024, 6, 15), 2.0, oil_l=0.25)
        _add_flight(app, acid, date(2020, 1, 1), 2.0, oil_l=9.0)  # outside window
        with app.app_context():
            assert oil_added(acid, date(2024, 1, 1), date(2024, 12, 31)) == 0.75
            assert oil_added(acid, None, date(2024, 12, 31)) == 9.75

    def test_dashboard_oil_rate_and_no_warning_without_threshold(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, date(2024, 5, 1), 5.0, oil_l=0.25)
        _add_flight(app, acid, date(2024, 6, 1), 5.0, oil_l=0.25)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            d = compute_cost_dashboard(ac, 12, today=date(2024, 7, 1))
            assert d["oil_total_l"] == 0.5
            assert d["oil_per_hour"] == 0.05
            assert d["oil_warning"] is False

    def test_dashboard_oil_warning_above_threshold(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, oil_warning_lph=0.1)
        _add_flight(app, acid, date(2024, 6, 1), 5.0, oil_l=1.0)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            d = compute_cost_dashboard(ac, 12, today=date(2024, 7, 1))
            assert d["oil_per_hour"] == 0.2
            assert d["oil_warning"] is True

    def test_dashboard_oil_no_warning_below_threshold(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, oil_warning_lph=0.3)
        _add_flight(app, acid, date(2024, 6, 1), 5.0, oil_l=1.0)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            d = compute_cost_dashboard(ac, 12, today=date(2024, 7, 1))
            assert d["oil_warning"] is False

    def test_dashboard_oil_rate_none_without_oil_or_hours(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            d = compute_cost_dashboard(ac, 12, today=date(2024, 7, 1))
            assert d["oil_total_l"] == 0.0
            assert d["oil_per_hour"] is None
            assert d["oil_warning"] is False

    def test_dashboard_page_shows_warning(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, oil_warning_lph=0.1)
        _add_flight(app, acid, date.today(), 5.0, oil_l=1.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/costs")
        assert resp.status_code == 200
        assert b"Oil consumption" in resp.data
        assert b"bi-exclamation-triangle-fill" in resp.data
