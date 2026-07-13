"""
Tests for engine/propeller TBO & life-limited component tracking:
  - component hours and limit status computation (TBO, overhaul reset,
    calendar limits, warning windows)
  - integration into compute_aircraft_statuses (dashboard/fleet colour)
  - component form persistence and validation of the new fields
  - aircraft detail, maintenance overview, and component logbook surfaces
"""

from datetime import date, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Component,
    ComponentType,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from services.component_limits import (  # pyright: ignore[reportMissingImports]
    aircraft_limit_infos,
    component_hours,
    component_limit_info,
    fleet_limit_statuses,
)
from utils import compute_aircraft_statuses  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="owner@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_aircraft(app, tenant_id, registration="OO-TBO"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_engine(app, aircraft_id, **kwargs):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=ComponentType.ENGINE,
            make="Lycoming",
            model="IO-360",
            **kwargs,
        )
        db.session.add(comp)
        db.session.commit()
        return comp.id


def _add_flight(app, aircraft_id, on, start, end):
    with app.app_context():
        db.session.add(
            FlightEntry(
                aircraft_id=aircraft_id,
                date=on,
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time_counter_start=start,
                flight_time_counter_end=end,
            )
        )
        db.session.commit()


class TestComponentHours:
    def test_hours_are_install_time_plus_flown(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(app, acid, time_at_install=100.0)
        _add_flight(app, acid, date(2026, 5, 1), 200.0, 202.5)
        _add_flight(app, acid, date(2026, 6, 1), 202.5, 204.0)
        with app.app_context():
            assert component_hours(db.session.get(Component, cid)) == 104.0

    def test_installation_window_filters_flights(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(
            app,
            acid,
            time_at_install=0.0,
            installed_at=date(2026, 5, 15),
            removed_at=date(2026, 6, 15),
        )
        _add_flight(app, acid, date(2026, 5, 1), 100.0, 105.0)  # before install
        _add_flight(app, acid, date(2026, 6, 1), 105.0, 107.0)  # inside window
        _add_flight(app, acid, date(2026, 7, 1), 107.0, 110.0)  # after removal
        with app.app_context():
            assert component_hours(db.session.get(Component, cid)) == 2.0


class TestLimitStatus:
    def test_no_limits_returns_none(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(app, acid)
        with app.app_context():
            assert component_limit_info(db.session.get(Component, cid)) is None

    def test_tbo_ok_and_due_soon_and_overdue(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(app, acid, time_at_install=0.0, tbo_hours=100.0)
        with app.app_context():
            comp = db.session.get(Component, cid)
            assert component_limit_info(comp)["status"] == "ok"
        _add_flight(app, acid, date(2026, 5, 1), 0.0, 95.0)  # 95/100 → last 10%
        with app.app_context():
            info = component_limit_info(db.session.get(Component, cid))
            assert info["status"] == "due_soon"
            assert info["tbo_remaining"] == 5.0
        _add_flight(app, acid, date(2026, 6, 1), 95.0, 101.0)  # 101/100
        with app.app_context():
            info = component_limit_info(db.session.get(Component, cid))
            assert info["status"] == "overdue"

    def test_tbo_exact_boundaries(self, app):
        """Pin the <= boundaries: tbo_remaining == 0 is overdue (not due_soon),
        and tbo_remaining == tbo * HOURS_WARN_FRACTION exactly is due_soon
        (not ok)."""
        _uid, tid = _create_user_and_tenant(app)
        # Separate aircraft per component — component_hours() has no per-component
        # installed_at filter here, so sharing one aircraft would sum both flights.
        ac_warn = _add_aircraft(app, tid, "OO-WRN")
        ac_over = _add_aircraft(app, tid, "OO-OVR")
        # tbo=100, flown to exactly 90 → tbo_remaining=10 == 100*0.1 exactly.
        cid_warn = _add_engine(app, ac_warn, time_at_install=0.0, tbo_hours=100.0)
        _add_flight(app, ac_warn, date(2026, 5, 1), 0.0, 90.0)
        with app.app_context():
            info = component_limit_info(db.session.get(Component, cid_warn))
            assert info["tbo_remaining"] == 10.0
            assert info["status"] == "due_soon"

        # tbo=100, flown to exactly 100 → tbo_remaining=0 exactly.
        cid_over = _add_engine(app, ac_over, time_at_install=0.0, tbo_hours=100.0)
        _add_flight(app, ac_over, date(2026, 5, 1), 0.0, 100.0)
        with app.app_context():
            info = component_limit_info(db.session.get(Component, cid_over))
            assert info["tbo_remaining"] == 0.0
            assert info["status"] == "overdue"

    def test_overhaul_resets_reference_point(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(
            app,
            acid,
            time_at_install=1900.0,
            tbo_hours=2000.0,
            overhauled_at_hours=1900.0,
            overhauled_on=date(2026, 1, 1),
        )
        _add_flight(app, acid, date(2026, 5, 1), 0.0, 50.0)
        with app.app_context():
            info = component_limit_info(db.session.get(Component, cid))
            assert info["total_hours"] == 1950.0
            assert info["since_overhaul"] == 50.0
            assert info["tbo_remaining"] == 1950.0
            assert info["status"] == "ok"

    def test_calendar_limit_windows(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        today = date(2026, 7, 9)
        for limit, expected in [
            (today + timedelta(days=365), "ok"),
            (today + timedelta(days=91), "ok"),
            (today + timedelta(days=90), "due_soon"),
            (today + timedelta(days=30), "due_soon"),
            (today, "due_soon"),
            (today - timedelta(days=1), "overdue"),
        ]:
            cid = _add_engine(app, acid, life_limit_date=limit)
            with app.app_context():
                info = component_limit_info(db.session.get(Component, cid), today)
                assert info["status"] == expected, limit

    def test_removed_components_ignored(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_engine(
            app,
            acid,
            tbo_hours=1.0,
            time_at_install=999.0,
            removed_at=date(2025, 1, 1),
        )
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            assert aircraft_limit_infos(ac) == []
            assert fleet_limit_statuses([ac]) == {acid: "ok"}

    def test_fleet_status_feeds_aircraft_status(self, app):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_engine(app, acid, time_at_install=150.0, tbo_hours=100.0)  # overdue
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            statuses = compute_aircraft_statuses([ac], [], {acid: None})
            assert statuses[acid] == "overdue"


class TestComponentFormLimits:
    def _form(self, **kwargs):
        data = {"type": "engine", "make": "Lycoming", "model": "IO-360"}
        data.update(kwargs)
        return data

    def test_limits_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/components/new",
            data=self._form(
                tbo_hours="2000",
                life_limit_date="2038-01-01",
                overhauled_at_hours="1200.0",
                overhauled_on="2024-06-01",
            ),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            comp = Component.query.filter_by(aircraft_id=acid).one()
            assert float(comp.tbo_hours) == 2000.0
            assert comp.life_limit_date == date(2038, 1, 1)
            assert float(comp.overhauled_at_hours) == 1200.0
            assert comp.overhauled_on == date(2024, 6, 1)

    def test_invalid_tbo_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/components/new", data=self._form(tbo_hours="0")
        )
        assert b"TBO must be a positive number of hours." in resp.data

    def test_invalid_overhaul_hours_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/components/new",
            data=self._form(overhauled_at_hours="-5"),
        )
        assert b"Last overhaul hours must be a non-negative number." in resp.data


class TestSurfaces:
    def test_detail_page_shows_tbo_progress(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_engine(app, acid, time_at_install=1850.0, tbo_hours=2000.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert resp.status_code == 200
        assert b"TBO" in resp.data
        assert b"2,000" in resp.data or b"2000" in resp.data

    def test_maintenance_overview_lists_limited_component(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_engine(app, acid, time_at_install=1950.0, tbo_hours=2000.0)  # due_soon
        _login(app, client)
        resp = client.get("/maintenance")
        assert resp.status_code == 200
        assert b"Component life limits" in resp.data
        assert b"Lycoming IO-360" in resp.data

    def test_maintenance_overview_hides_ok_components(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_engine(app, acid, time_at_install=100.0, tbo_hours=2000.0)
        _login(app, client)
        resp = client.get("/maintenance")
        assert b"Component life limits" not in resp.data

    def test_component_logbook_since_overhaul(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(
            app,
            acid,
            time_at_install=1900.0,
            tbo_hours=2000.0,
            overhauled_at_hours=1900.0,
            overhauled_on=date(2026, 1, 1),
        )
        _add_flight(app, acid, date(2026, 5, 1), 0.0, 50.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"Since overhaul" in resp.data
        assert b"50.0 h" in resp.data

    def test_component_logbook_legacy_extras_tbo(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_engine(app, acid, extras={"tbo_hours": 2000})
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"TBO" in resp.data
