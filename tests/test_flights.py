"""
Tests for Phase 3 + Phase 7: Flight logging routes (CRUD + auth guard + validation
+ pilot/notes/tach/photos + component logbooks).
"""

import os
import sys
from datetime import date
from io import BytesIO
from textwrap import dedent
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Component,
    ComponentType,
    Document,
    FlightCrew,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _login_orphan_user(app, client):
    """Create a User with no TenantUser and inject into session."""
    with app.app_context():
        user = User(
            email="orphan@example.com",
            password_hash=_pw_hash.hash("x"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com", password="testpassword123"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=email,
            password_hash=_pw_hash.hash(password),
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


def _add_aircraft(app, tenant_id, registration="OO-PNH"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(
    app,
    aircraft_id,
    comp_type=None,
    installed_at=None,
    removed_at=None,
    time_at_install=0.0,
    extras=None,
):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=comp_type or ComponentType.ENGINE,
            make="Lycoming",
            model="IO-360",
            time_at_install=time_at_install,
            installed_at=installed_at,
            removed_at=removed_at,
            extras=extras,
        )
        db.session.add(comp)
        db.session.commit()
        return comp.id


def _add_flight(
    app,
    aircraft_id,
    dep="EBOS",
    arr="EBBR",
    flight_time_counter_start=100.0,
    flight_time_counter_end=101.5,
    hobbs_start=None,
    hobbs_end=None,
    flight_date=None,
    pilot=None,
    notes=None,
    engine_time_counter_start=None,
    engine_time_counter_end=None,
    tach_start=None,
    tach_end=None,
    departure_time=None,
):
    # Support legacy kwarg names for backward compatibility
    if hobbs_start is not None:
        flight_time_counter_start = hobbs_start
    if hobbs_end is not None:
        flight_time_counter_end = hobbs_end
    if tach_start is not None:
        engine_time_counter_start = tach_start
    if tach_end is not None:
        engine_time_counter_end = tach_end
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date(2024, 1, 15),
            departure_icao=dep,
            arrival_icao=arr,
            departure_time=departure_time,
            flight_time_counter_start=flight_time_counter_start,
            flight_time_counter_end=flight_time_counter_end,
            notes=notes,
            engine_time_counter_start=engine_time_counter_start,
            engine_time_counter_end=engine_time_counter_end,
        )
        db.session.add(fe)
        db.session.flush()
        if pilot:
            db.session.add(
                FlightCrew(flight_id=fe.id, name=pilot, role="PIC", sort_order=0)
            )
        db.session.commit()
        return fe.id


def _gpx_bytes(speeds_ms=None) -> bytes:
    """Minimal valid GPX with per-point speeds (m/s). Default produces a flight segment."""
    if speeds_ms is None:
        speeds_ms = [0.0, 20.0, 20.0, 0.0]
    trkpts = ""
    for i, spd in enumerate(speeds_ms):
        trkpts += (
            f'\n      <trkpt lat="51.{i}" lon="4.{i}">'
            f"\n        <ele>100</ele><speed>{spd}</speed>"
            f"\n        <time>2024-06-01T10:0{i}:00Z</time>"
            f"\n      </trkpt>"
        )
    return dedent(f"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>test</name><trkseg>{trkpts}
      </trkseg></trk>
    </gpx>
    """).encode()


# ── Auth guard ────────────────────────────────────────────────────────────────


class TestAuthGuard:
    def test_flight_list_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_new_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/flights/new")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_edit_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/flights/1/edit")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_delete_flight_redirects_when_not_logged_in(self, client):
        response = client.post("/aircraft/1/flights/1/delete")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_component_logbook_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/components/1/logbook")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_serve_upload_redirects_when_not_logged_in(self, client):
        response = client.get("/uploads/somefile.jpg")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# ── Flight list / airframe logbook ────────────────────────────────────────────


class TestFlightList:
    def test_list_shows_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, dep="EBOS", arr="EBBR")
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
        assert b"EBBR" in resp.data

    def test_list_empty_state(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert resp.status_code == 200
        assert b"No flights logged" in resp.data

    def test_list_shows_duration(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=101.5)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"1.5" in resp.data

    def test_list_shows_pilot(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, pilot="J. Smith")
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"J. Smith" in resp.data

    def test_list_shows_notes_sub_row(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, notes="Smooth VFR flight")
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"Smooth VFR flight" in resp.data

    def test_list_shows_engine_counter_when_no_flight_counter(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(
            app,
            acid,
            flight_time_counter_start=None,
            flight_time_counter_end=None,
            tach_start=500.0,
            tach_end=501.3,
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"501.3" in resp.data

    def test_list_same_day_orders_by_departure_time_not_insertion(self, app, client):
        """Backfilling an earlier same-day flight after a later one must not
        make the earlier flight sort above the later one."""
        from datetime import time

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        # Insert the LATER flight first...
        _add_flight(
            app,
            acid,
            dep="LATERDEP",
            flight_date=date(2024, 3, 1),
            departure_time=time(14, 0),
        )
        # ...then backfill an EARLIER same-day flight, inserted (higher id) after.
        _add_flight(
            app,
            acid,
            dep="EARLYDEP",
            flight_date=date(2024, 3, 1),
            departure_time=time(8, 0),
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        pos_later = resp.data.find(b"LATERDEP")
        pos_earlier = resp.data.find(b"EARLYDEP")
        assert 0 <= pos_later < pos_earlier

    def test_list_404_for_other_tenant_aircraft(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        resp = client.get(f"/aircraft/{other_acid}/flights")
        assert resp.status_code == 404

    def test_fleet_flights_page_renders(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, dep="EBOS", arr="EBBR")
        _login(app, client)
        resp = client.get("/flights")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data


# ── Log flight ─────────────────────────────────────────────────────────────────


class TestLogFlight:
    def test_get_shows_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert resp.status_code == 200
        assert b"Log a flight" in resp.data

    def test_get_prefills_hobbs_from_existing_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=102.0)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert b"102.0" in resp.data

    def test_get_does_not_leak_other_tenant_counter_hint(self, app, client):
        """A tenant-A user requesting /flights/new?aircraft_id=<tenant-B aircraft>
        must not see tenant B's hour-meter counter values prefilled."""
        _create_user_and_tenant(app)
        _uid_b, tid_b = _create_user_and_tenant(app, "victim2@example.com")
        other_ac_id = _add_aircraft(app, tid_b, "OO-VC2")
        _add_flight(app, other_ac_id, hobbs_start=900.0, hobbs_end=987.6)
        _login(app, client, email="pilot@example.com")
        resp = client.get(f"/flights/new?aircraft_id={other_ac_id}")
        assert resp.status_code == 200
        assert b"987.6" not in resp.data

    def test_get_defaults_date_to_today(self, app, client):
        import datetime

        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/new")
        assert resp.status_code == 200
        today = datetime.date.today().isoformat().encode()
        assert today in resp.data

    def test_post_creates_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.departure_icao == "EBOS"
            assert float(fe.flight_time_counter_end) == 101.5

    def test_post_saves_pilot_and_notes(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "J. Smith",
                "crew_role_0": "PIC",
                "notes": "Test flight",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.crew[0].name == "J. Smith"
            assert fe.notes == "Test flight"

    def test_post_saves_tach(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501.3",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert float(fe.engine_time_counter_start) == 500.0
            assert float(fe.engine_time_counter_end) == 501.3

    def test_post_rejects_tach_end_not_greater(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_start": "502.0",
                "engine_time_counter_end": "501.0",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"Engine counter end" in resp.data

    def test_post_accepts_equal_flight_counter_start_and_end(self, app, client):
        """Ground-only entry (engine run-up / taxi, no airborne time): the
        flight counter does not move even though the engine counter does."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBOS",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "100.0",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "500.6",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert float(fe.flight_time_counter_start) == 100.0
            assert float(fe.flight_time_counter_end) == 100.0

    def test_post_accepts_equal_engine_counter_start_and_end(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "500.0",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert float(fe.engine_time_counter_start) == 500.0
            assert float(fe.engine_time_counter_end) == 500.0

    def test_post_rejects_negative_tach(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_start": "-1.0",
                "engine_time_counter_end": "1.0",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_rejects_negative_tach_end(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "-1.0",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_rejects_invalid_tach_end(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "engine_time_counter_end": "notanumber",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_uppercases_icao(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "ebos",
                "arrival_icao": "ebbr",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.departure_icao == "EBOS"
            assert fe.arrival_icao == "EBBR"

    def test_post_rejects_hobbs_end_not_greater(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "102.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"must not be less than" in resp.data
        with app.app_context():
            assert FlightEntry.query.count() == 0

    def test_post_rejects_missing_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"Date is required" in resp.data

    def test_post_rejects_negative_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "-1.0",
                "flight_time_counter_end": "1.0",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data


# ── Reservation pre-link (Phase 37d) ────────────────────────────────────────────


class TestReservationPreLink:
    def _make_reservation(
        self, app, aircraft_id, pilot_user_id, start, end, status="confirmed"
    ):
        from models import Reservation, ReservationStatus  # pyright: ignore[reportMissingImports]

        with app.app_context():
            r = Reservation(
                aircraft_id=aircraft_id,
                pilot_user_id=pilot_user_id,
                start_dt=start,
                end_dt=end,
                status=ReservationStatus(status),
            )
            db.session.add(r)
            db.session.commit()
            return r.id

    def test_flight_inside_window_links(self, app, client):
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        res_id = self._make_reservation(
            app,
            acid,
            uid,
            datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 20, 11, 0, tzinfo=timezone.utc),
        )
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-06-20",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "departure_time": "09:15",
                "crew_name_0": "Test Pilot",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.reservation_id == res_id

    def test_flight_outside_window_not_linked(self, app, client):
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        self._make_reservation(
            app,
            acid,
            uid,
            datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 20, 11, 0, tzinfo=timezone.utc),
        )
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-06-25",  # far outside the reservation window
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "departure_time": "09:15",
                "crew_name_0": "Test Pilot",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.reservation_id is None

    def test_flight_other_pilots_reservation_not_linked(self, app, client):
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        other_uid, _ = _create_user_and_tenant(app, "other_pilot@example.com")
        with app.app_context():
            from models import Role, TenantUser  # pyright: ignore[reportMissingImports]

            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _add_aircraft(app, tid)
        # Reservation belongs to the OTHER pilot, not the one logging the flight.
        self._make_reservation(
            app,
            acid,
            other_uid,
            datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 20, 11, 0, tzinfo=timezone.utc),
        )
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-06-20",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "departure_time": "09:15",
                "crew_name_0": "Test Pilot",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.reservation_id is None

    def test_pending_reservation_not_linked(self, app, client):
        """Only CONFIRMED reservations pre-link — a pending one does not."""
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        self._make_reservation(
            app,
            acid,
            uid,
            datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 20, 11, 0, tzinfo=timezone.utc),
            status="pending",
        )
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-06-20",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "departure_time": "09:15",
                "crew_name_0": "Test Pilot",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.reservation_id is None

    def test_get_form_shows_covering_reservation_notice(self, app, client):
        from datetime import datetime, timedelta, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        self._make_reservation(
            app,
            acid,
            uid,
            datetime.now(timezone.utc) - timedelta(minutes=30),
            datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert resp.status_code == 200
        assert b"will be linked to your reservation" in resp.data

    def test_get_form_no_notice_without_reservation(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert resp.status_code == 200
        assert b"will be linked to your reservation" not in resp.data


# ── Photo uploads ──────────────────────────────────────────────────────────────


class TestPhotoUpload:
    def test_upload_hobbs_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "flight_counter_photo": (BytesIO(b"fake image data"), "flight.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.flight_counter_photo is not None
            assert fe.flight_counter_photo.endswith(".jpg")
            assert os.path.isfile(
                os.path.join(app.config["UPLOAD_FOLDER"], fe.flight_counter_photo)
            )

    def test_upload_tach_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "engine_counter_photo": (BytesIO(b"fake tach image"), "engine.png"),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.engine_counter_photo is not None
            assert fe.engine_counter_photo.endswith(".png")

    def test_upload_ignores_invalid_extension(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "flight_counter_photo": (BytesIO(b"data"), "file.exe"),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.flight_counter_photo is None

    def test_edit_replaces_existing_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            old_name = "old_hobbs.jpg"
            fe.flight_counter_photo = old_name
            db.session.commit()
            # Create the old file so deletion can be tested
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], old_name)
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            with open(old_path, "wb") as f:
                f.write(b"old photo")
        _login(app, client)
        client.post(
            f"/flights/{fid}/edit",
            data={
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "flight_counter_photo": (BytesIO(b"new image"), "new_flight.jpg"),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            assert fe.flight_counter_photo != old_name
        assert not os.path.isfile(old_path)

    def test_delete_flight_removes_photos(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        photo_name = "todelete.jpg"
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            fe.flight_counter_photo = photo_name
            db.session.commit()
            photo_path = os.path.join(app.config["UPLOAD_FOLDER"], photo_name)
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            with open(photo_path, "wb") as f:
                f.write(b"photo")
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/{fid}/delete")
        assert not os.path.isfile(photo_path)

    def test_upload_fuel_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "fuel_photo": (BytesIO(b"fake fuel image"), "fuel.jpg"),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.fuel_photo is not None
            assert fe.fuel_photo.endswith(".jpg")
            assert os.path.isfile(
                os.path.join(app.config["UPLOAD_FOLDER"], fe.fuel_photo)
            )

    def test_delete_flight_tolerates_missing_photo_file(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            fe.flight_counter_photo = "nonexistent.jpg"
            db.session.commit()
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/flights/{fid}/delete", follow_redirects=False
        )
        assert resp.status_code == 302  # OSError swallowed, no crash


# ── Serve upload ──────────────────────────────────────────────────────────────


class TestServeUpload:
    def test_serve_returns_file(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        fname = "test_serve.jpg"
        fpath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        with open(fpath, "wb") as f:
            f.write(b"image content")
        with app.app_context():
            doc = Document(
                aircraft_id=acid,
                filename=fname,
                original_filename="test_serve.jpg",
                mime_type="image/jpeg",
            )
            db.session.add(doc)
            db.session.commit()
        with client.get(f"/uploads/{fname}") as resp:
            assert resp.status_code == 200
            assert resp.data == b"image content"


# ── Edit flight ────────────────────────────────────────────────────────────────


class TestEditFlight:
    def test_get_shows_prefilled_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(
            app, acid, dep="EBOS", arr="EBBR", hobbs_start=100.0, hobbs_end=101.5
        )
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
        assert b"EBBR" in resp.data

    def test_get_shows_track_download_links_when_gps_track_linked(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid, _ = _add_flight_with_track(app, acid)
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert resp.status_code == 200
        assert f"/flights/{fid}/track/image.png".encode() in resp.data
        assert f"/flights/{fid}/track/animation.gif".encode() in resp.data
        assert b"Download image" in resp.data
        assert b"Download GIF" in resp.data
        # Inline preview: an <img> pointing at the same PNG render endpoint.
        assert f'<img src="/flights/{fid}/track/image.png"'.encode() in resp.data

    def test_get_hides_preview_image_when_track_has_no_geojson(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid, _ = _add_flight_with_track(app, acid, geojson={})
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert resp.status_code == 200
        assert f'<img src="/flights/{fid}/track/image.png"'.encode() not in resp.data
        assert b"Download image" in resp.data

    def test_get_prefills_pilot_and_notes(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, pilot="J. Smith", notes="Test notes")
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert b"J. Smith" in resp.data
        assert b"Test notes" in resp.data

    def test_edit_form_pilot_time_blank_when_mirrored(self, app, client):
        """When the pilot-log time equals the aircraft-log time (the normal
        case), the override input should render blank, not the mirrored
        value — it hasn't been explicitly overridden."""
        import re
        from datetime import time
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 6, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                departure_time=time(9, 0),
                arrival_time=time(10, 30),
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    flight_id=fe.id,
                    date=date(2024, 6, 1),
                    departure_place="EBOS",
                    arrival_place="EBBR",
                    departure_time=time(9, 0),
                    arrival_time=time(10, 30),
                )
            )
            db.session.commit()
            fid = fe.id
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        match = re.search(
            r'id="pilot_departure_time"[^>]*value="([^"]*)"', resp.data.decode()
        )
        assert match is not None
        assert match.group(1) == ""

    def test_edit_form_pilot_time_shows_existing_override(self, app, client):
        """When the pilot-log time was previously set to something different
        from the aircraft-log time, the override input shows that value."""
        import re
        from datetime import time
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 6, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                departure_time=time(9, 0),
                arrival_time=time(10, 30),
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    flight_id=fe.id,
                    date=date(2024, 6, 1),
                    departure_place="EBOS",
                    arrival_place="EBBR",
                    departure_time=time(8, 45),
                    arrival_time=time(10, 30),
                )
            )
            db.session.commit()
            fid = fe.id
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        match = re.search(
            r'id="pilot_departure_time"[^>]*value="([^"]*)"', resp.data.decode()
        )
        assert match is not None
        assert match.group(1) == "08:45"

    def test_post_updates_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(
            app, acid, dep="EBOS", arr="EBBR", hobbs_start=100.0, hobbs_end=101.5
        )
        _login(app, client)
        resp = client.post(
            f"/flights/{fid}/edit",
            data={
                "date": "2024-06-02",
                "departure_icao": "ELLX",
                "arrival_icao": "EDDM",
                "flight_time_counter_start": "101.5",
                "flight_time_counter_end": "105.0",
                "crew_name_0": "Updated Pilot",
                "crew_role_0": "PIC",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            assert fe.departure_icao == "ELLX"
            assert float(fe.flight_time_counter_end) == 105.0
            assert fe.crew[0].name == "Updated Pilot"

    def test_edit_404_for_other_tenant_flight(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        other_fid = _add_flight(app, other_acid)
        _login(app, client)
        resp = client.get(f"/flights/{other_fid}/edit")
        assert resp.status_code == 404

    def test_edit_shows_linked_entry_banner_with_specific_urls(self, app, client):
        from models import FlightEntry, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 1, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.flush()
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe.id,
                date=date(2024, 1, 15),
                departure_place="EBOS",
                arrival_place="EBBR",
            )
            db.session.add(pe)
            db.session.commit()
            fid, peid = fe.id, pe.id

        resp = client.get(f"/flights/{fid}/edit")
        assert resp.status_code == 200
        assert f"/aircraft/{acid}/flights/{fid}".encode() in resp.data
        assert f"/pilot/logbook/{peid}/view".encode() in resp.data
        assert b"This flight has a linked pilot logbook entry" not in resp.data


# ── Delete flight ──────────────────────────────────────────────────────────────


class TestDeleteFlight:
    def test_delete_removes_entry(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/flights/{fid}/delete", follow_redirects=False
        )
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(FlightEntry, fid) is None

    def test_delete_404_for_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        fid = _add_flight(app, acid1)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid2}/flights/{fid}/delete")
        assert resp.status_code == 404


# ── Component logbook ─────────────────────────────────────────────────────────


class TestComponentLogbook:
    def test_engine_logbook_shows_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(
            app,
            acid,
            installed_at=date(2020, 1, 1),
            time_at_install=500.0,
            extras={"tbo_hours": 2000},
        )
        _add_flight(
            app,
            acid,
            dep="EBOS",
            arr="EBBR",
            hobbs_start=100.0,
            hobbs_end=101.5,
            flight_date=date(2024, 1, 15),
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
        assert b"501.5" in resp.data  # 500 + 1.5 = 501.5 comp hours

    def test_logbook_shows_tbo_remaining(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(
            app,
            acid,
            installed_at=date(2020, 1, 1),
            time_at_install=500.0,
            extras={"tbo_hours": 2000},
        )
        _add_flight(
            app, acid, hobbs_start=100.0, hobbs_end=101.5, flight_date=date(2024, 1, 15)
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"2000" in resp.data
        assert b"remaining" in resp.data

    def test_logbook_filters_by_install_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, installed_at=date(2024, 1, 1))
        _add_flight(
            app,
            acid,
            dep="EBOS",
            arr="EBBR",
            hobbs_start=100.0,
            hobbs_end=101.0,
            flight_date=date(2023, 12, 31),
        )  # before install
        _add_flight(
            app,
            acid,
            dep="ELLX",
            arr="EDDM",
            hobbs_start=101.0,
            hobbs_end=102.0,
            flight_date=date(2024, 2, 1),
        )  # after install
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"ELLX" in resp.data
        assert b"EBOS" not in resp.data

    def test_logbook_filters_by_removed_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(
            app, acid, installed_at=date(2023, 1, 1), removed_at=date(2024, 1, 1)
        )
        _add_flight(
            app,
            acid,
            dep="EBOS",
            arr="EBBR",
            hobbs_start=100.0,
            hobbs_end=101.0,
            flight_date=date(2023, 6, 1),
        )  # during install
        _add_flight(
            app,
            acid,
            dep="ELLX",
            arr="EDDM",
            hobbs_start=101.0,
            hobbs_end=102.0,
            flight_date=date(2024, 3, 1),
        )  # after removal
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"EBOS" in resp.data
        assert b"ELLX" not in resp.data

    def test_logbook_empty_state(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"No flights recorded" in resp.data

    def test_logbook_404_for_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        cid = _add_component(app, acid1)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid2}/components/{cid}/logbook")
        assert resp.status_code == 404

    def test_logbook_404_for_nonexistent_component(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/9999/logbook")
        assert resp.status_code == 404

    def test_propeller_logbook(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(
            app, acid, comp_type=ComponentType.PROPELLER, time_at_install=100.0
        )
        _add_flight(
            app, acid, hobbs_start=200.0, hobbs_end=201.0, flight_date=date(2024, 1, 15)
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"101.0" in resp.data  # 100 + 1.0 = 101.0 comp hours

    def test_logbook_tbo_overdue(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(
            app,
            acid,
            time_at_install=1999.0,
            installed_at=date(2020, 1, 1),
            extras={"tbo_hours": 2000},
        )
        _add_flight(
            app, acid, hobbs_start=100.0, hobbs_end=102.0, flight_date=date(2024, 1, 15)
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"Overdue" in resp.data

    def test_logbook_notes_shown(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid)
        _add_flight(
            app,
            acid,
            notes="Engine ran rough at low RPM",
            flight_date=date(2024, 1, 15),
        )
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"Engine ran rough" in resp.data


# ── Aircraft detail: recent flights ───────────────────────────────────────────


class TestDetailRecentFlights:
    def test_detail_shows_recent_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, dep="EBOS", arr="EBBR")
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data

    def test_detail_shows_empty_state_when_no_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert resp.status_code == 200
        assert b"No flights logged yet" in resp.data

    def test_detail_shows_at_most_3_recent_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        hs = 100.0
        for i in range(5):
            _add_flight(
                app,
                acid,
                hobbs_start=hs,
                hobbs_end=hs + 1.0,
                flight_date=date(2024, 1, i + 1),
            )
            hs += 1.0
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert resp.status_code == 200
        assert b"2024-01-05" in resp.data
        assert b"2024-01-04" in resp.data
        assert b"2024-01-03" in resp.data
        assert b"2024-01-01" not in resp.data

    def test_detail_shows_component_logbook_link(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_component(app, acid, comp_type=ComponentType.ENGINE)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert b"Logbook" in resp.data

    def test_detail_no_logbook_link_for_avionics(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            comp = Component(
                aircraft_id=acid,
                type=ComponentType.AVIONICS,
                make="Garmin",
                model="GTN 650",
            )
            db.session.add(comp)
            db.session.commit()
            comp_id = comp.id
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        # Component logbook URL must not appear for avionics
        assert f"/components/{comp_id}/logbook" not in resp.data.decode()


# ── Coverage gap: no TenantUser → 403 ────────────────────────────────────────


class TestFlightsNoTenantUser:
    def test_aborts_403_when_no_tenant_user(self, app, client):
        with app.app_context():
            tenant = Tenant(name="Other Hangar")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-TST", make="X", model="X"
            )
            db.session.add(ac)
            db.session.commit()
            acid = ac.id
        _login_orphan_user(app, client)
        response = client.get(f"/aircraft/{acid}/flights")
        assert response.status_code == 403


# ── Coverage gap: _save_flight validation ────────────────────────────────────


class TestSaveFlightValidation:
    def test_invalid_date_format_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "date": "not-a-date",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        assert resp.status_code == 200
        assert b"valid date" in resp.data

    def test_missing_departure_icao_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "date": "2024-06-01",
                "departure_icao": "",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        assert resp.status_code == 200
        assert b"Departure" in resp.data

    def test_missing_arrival_icao_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
            },
        )
        assert resp.status_code == 200
        assert b"Arrival" in resp.data

    def test_negative_hobbs_end_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "-1.0",
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data


# ── Phase 31: Other-aircraft flight logging ───────────────────────────────────


# ── Standalone other-aircraft route (/flights/new) ───────────────────────────


class TestStandaloneOtherAircraftRoute:
    """Tests for GET/POST /flights/new (no aircraft_id required)."""

    def test_get_shows_other_aircraft_form(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/new")
        assert resp.status_code == 200
        assert b"other_aircraft" in resp.data

    def test_get_prefills_pilot_name(self, app, client):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email="named@example.com",
                password_hash=_pw_hash.hash("x"),
                is_active=True,
                name="Alice Pilot",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT)
            )
            db.session.commit()
            uid = user.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        resp = client.get("/flights/new")
        assert resp.status_code == 200
        assert b"Alice Pilot" in resp.data

    def test_post_creates_logbook_entry(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        _create_user_and_tenant(app)
        uid = _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-27",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.2",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/pilot/logbook" in resp.headers["Location"]
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.departure_place == "EBNM"

    def test_get_redirects_when_not_logged_in(self, client):
        resp = client.get("/flights/new")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── Phase 31: Other-aircraft flight logging ───────────────────────────────────


class TestOtherAircraftFlight:
    """Tests for the 'other aircraft' path in new_flight / _save_other_aircraft_flight."""

    def _post(self, client, acid, data):
        return client.post(
            "/flights/new",
            data={"other_aircraft": "1", **data},
            follow_redirects=True,
        )

    def test_other_aircraft_creates_logbook_entry_not_flight_entry(self, app, client):
        from models import FlightEntry, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)

        resp = self._post(
            client,
            acid,
            {
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "other_ac_make_model": "Piper PA-28",
                "other_ac_reg": "OO-TST",
                "flight_time": "1.2",
            },
        )
        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 0
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.aircraft_type == "Piper PA-28"
            assert entry.aircraft_registration == "OO-TST"
            assert entry.departure_place == "EBNM"
            assert entry.arrival_place == "EBAW"
            assert entry.function_pic is not None
            assert entry.function_dual is None
            assert entry.flight_id is None

    def test_other_aircraft_dual_role_sets_function_dual(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)

        resp = self._post(
            client,
            acid,
            {
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "dual",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "flight_time": "0.8",
            },
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.function_dual is not None
            assert entry.function_pic is None

    def test_other_aircraft_missing_role_rejected(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)

        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "none",
            },
        )
        assert resp.status_code == 200
        assert b"required" in resp.data.lower()

    def test_other_aircraft_redirects_to_logbook(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)

        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 302
        assert "/pilot/logbook" in resp.headers["Location"]

    def test_other_aircraft_pic_name_set_to_pilot_name(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)

        resp = self._post(
            client,
            acid,
            {
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "John Doe",
                "pilot_role": "pic",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
            },
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.pic_name == "John Doe"

    def test_normal_new_flight_unchanged(self, app, client):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)

        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 1

    def test_other_aircraft_missing_date_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"Date is required" in resp.data

    def test_other_aircraft_invalid_date_format_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "not-a-date",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"valid date" in resp.data

    def test_other_aircraft_missing_departure_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"Departure" in resp.data

    def test_other_aircraft_missing_arrival_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"Arrival" in resp.data

    def test_other_aircraft_missing_crew_name_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"Pilot" in resp.data

    def test_no_aircraft_selected_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                # neither aircraft_id nor other_aircraft=1
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"select an aircraft" in resp.data.lower()

    def test_other_aircraft_missing_make_model_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"Aircraft type" in resp.data

    def test_other_aircraft_missing_registration_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"registration" in resp.data

    def test_other_aircraft_invalid_departure_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "departure_time": "not-a-time",
            },
        )
        assert resp.status_code == 200
        assert b"Departure time" in resp.data

    def test_other_aircraft_invalid_arrival_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "arrival_time": "99:99",
            },
        )
        assert resp.status_code == 200
        assert b"Arrival time" in resp.data

    def test_other_aircraft_negative_flight_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "date": "2026-05-26",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "-1.0",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data


# ── Coverage: _save_flight edge cases ────────────────────────────────────────


class TestSaveFlightEdgeCases:
    """Covers remaining edge-case paths in _save_flight."""

    def _base_data(self, acid, **kwargs):
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

    def test_invalid_departure_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, departure_time="not-a-time"),
        )
        assert resp.status_code == 200
        assert b"Departure time" in resp.data

    def test_invalid_arrival_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, arrival_time="99:99"),
        )
        assert resp.status_code == 200
        assert b"Arrival time" in resp.data

    def test_invalid_pilot_departure_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(
                acid, pilot_role="pic", pilot_departure_time="not-a-time"
            ),
        )
        assert resp.status_code == 200
        assert b"Pilot log departure time" in resp.data

    def test_invalid_pilot_arrival_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, pilot_role="pic", pilot_arrival_time="99:99"),
        )
        assert resp.status_code == 200
        assert b"Pilot log arrival time" in resp.data

    def test_pilot_log_times_default_to_aircraft_log_times(self, app, client):
        """Leaving the pilot-log time fields blank mirrors the aircraft log."""
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data=self._base_data(
                acid,
                pilot_role="pic",
                departure_time="09:00",
                arrival_time="10:30",
            ),
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            pe = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert str(fe.departure_time) == "09:00:00"
            assert str(pe.departure_time) == "09:00:00"
            assert str(fe.arrival_time) == "10:30:00"
            assert str(pe.arrival_time) == "10:30:00"

    def test_pilot_log_times_can_override_aircraft_log_times(self, app, client):
        """An explicit pilot-log time wins over the aircraft-log time."""
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data=self._base_data(
                acid,
                pilot_role="pic",
                departure_time="09:00",
                arrival_time="10:30",
                pilot_departure_time="08:45",
                pilot_arrival_time="10:45",
            ),
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            pe = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert str(fe.departure_time) == "09:00:00"
            assert str(pe.departure_time) == "08:45:00"
            assert str(fe.arrival_time) == "10:30:00"
            assert str(pe.arrival_time) == "10:45:00"

    def test_negative_flight_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "flight_time": "-1.0",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data

    def test_tach_only_derives_flight_time(self, app, client):
        """Aircraft with has_flight_counter=False uses engine counter diff as flight time."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.has_flight_counter = False
            ac.flight_counter_offset = 0.0
            db.session.commit()
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501.3",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert float(fe.flight_time) == 1.3

    def test_tach_only_subtracts_nonzero_flight_counter_offset(self, app, client):
        """A nonzero flight_counter_offset (e.g. taxi/warm-up time baked into the
        tach reading) must actually be subtracted, not just accepted as 0.0."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.has_flight_counter = False
            ac.flight_counter_offset = 0.4
            db.session.commit()
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501.3",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            # (501.3 - 500.0) - 0.4 = 0.9, not 1.3
            assert float(fe.flight_time) == 0.9

    def test_tach_only_floors_flight_time_at_zero(self, app, client):
        """When the counter offset exceeds the raw counter diff (e.g. a counter
        entered backwards, or an offset larger than the actual flight), flight
        time must floor at 0.0 rather than go negative."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.has_flight_counter = False
            ac.flight_counter_offset = 5.0
            db.session.commit()
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501.3",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert float(fe.flight_time) == 0.0

    def test_negative_passenger_count_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, passenger_count="-1"),
        )
        assert resp.status_code == 200
        assert b"Passenger" in resp.data

    def test_landing_count_derived_from_day_plus_night(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, landings_day="3", landings_night="1"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.landing_count == 4

    def test_landing_count_none_when_pilot_fields_absent(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.landing_count is None

    def test_negative_fuel_added_qty_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, fuel_event="before", fuel_added_qty="-5"),
        )
        assert resp.status_code == 200
        assert b"Fuel" in resp.data

    def test_negative_fuel_remaining_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data=self._base_data(acid, fuel_remaining_qty="-5"),
        )
        assert resp.status_code == 200
        assert b"Fuel" in resp.data

    def test_second_crew_member_saved(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            "/flights/new",
            data=self._base_data(
                acid, crew_name_1="Co-Pilot Jones", crew_role_1="COPILOT"
            ),
        )
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            crew = sorted(fe.crew, key=lambda c: c.sort_order)
            assert len(crew) == 2
            assert crew[1].name == "Co-Pilot Jones"


# ── Coverage: flight hour milestone ──────────────────────────────────────────


class TestFlightHourMilestone:
    def test_crossing_100h_milestone_flashes_message(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            fe_seed = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 1, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=99.0,
            )
            db.session.add(fe_seed)
            db.session.commit()
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time": "1.5",
                "crew_name_0": "Test Pilot",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"100" in resp.data


# ── Phase 31b: coverage for GPS, duplicate detection, detach/delete paths ─────


class TestPhase31bCoverage:
    """Fill gaps in flights/routes.py coverage introduced by Phase 31b."""

    def test_edit_nonexistent_flight_returns_404(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/999999/edit")
        assert resp.status_code == 404

    def test_invalid_pilot_role_normalised_to_none(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "not_a_valid_role",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert PilotLogbookEntry.query.filter_by(pilot_user_id=uid).count() == 0

    def test_optional_pilot_log_fields(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.5",
                "night_time": "0.5",
                "instrument_time": "abc",  # invalid → _parse_dec exception path
                "multi_pilot": "-0.1",  # negative → _parse_dec returns None
                "landings_day": "1",
                "landings_night": "0",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            pe = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pe is not None
            assert float(pe.night_time) == 0.5
            assert pe.instrument_time is None
            assert pe.multi_pilot is None

    def test_gps_hidden_fields_create_track_and_link(self, app, client):
        import json as _json

        from models import GpsTrack, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[4.0, 51.0], [4.1, 51.1]],
            },
            "properties": {},
        }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.5",
                "gps_filename": "track.gpx",
                "gps_block_off_utc": "2026-05-01T10:00:00+00:00",
                "gps_block_on_utc": "2026-05-01T11:30:00+00:00",
                "gps_geojson": _json.dumps(geojson),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            gt = GpsTrack.query.first()
            assert gt is not None
            assert gt.source_filename == "track.gpx"
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.gps_track_id == gt.id
            assert fe.block_off_utc is not None
            assert fe.block_on_utc is not None
            pe = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pe is not None
            assert pe.gps_track_id == gt.id

    def test_parse_gps_action_invalid_file_flashes_warning(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "action": "parse_gps",
                "aircraft_id": str(acid),
                "gps_file": (BytesIO(b"not valid gps data"), "track.gpx"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"parse" in resp.data.lower() or b"GPS" in resp.data

    def test_duplicate_detected_with_overlapping_block_times(self, app, client):
        import json as _json
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe_seed = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                block_off_utc=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                block_on_utc=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
            )
            db.session.add(fe_seed)
            db.session.commit()
        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "gps_block_off_utc": "2026-05-01T10:30:00+00:00",
                "gps_block_on_utc": "2026-05-01T11:30:00+00:00",
                "gps_geojson": _json.dumps(geojson),
            },
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.data or b"duplicate" in resp.data.lower()

    def test_duplicate_detected_by_date_dep_arr(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe_seed = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 2),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe_seed)
            db.session.commit()
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-02",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.data or b"duplicate" in resp.data.lower()

    def test_duplicate_pilot_log_shows_duplicate_ui(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            pe_seed = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2026, 5, 3),
                departure_place="EBNM",
                arrival_place="EBAW",
            )
            db.session.add(pe_seed)
            db.session.commit()
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-03",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
            },
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.data or b"duplicate" in resp.data.lower()

    def test_link_gps_links_track_to_existing_flight(self, app, client):
        import json as _json

        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe_seed = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 4),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe_seed)
            db.session.commit()
            feid = fe_seed.id
        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-04",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "duplicate_action": "link_gps",
                "gps_geojson": _json.dumps(geojson),
                "gps_filename": "track.gpx",
            },
        )
        assert resp.status_code == 302
        with app.app_context():
            gt = GpsTrack.query.first()
            assert gt is not None
            fe2 = db.session.get(FlightEntry, feid)
            assert fe2.gps_track_id == gt.id

    def test_link_gps_no_match_flashes_warning(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-05",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "duplicate_action": "link_gps",
                # No gps_geojson or gps_filename → triggers "no match" path
            },
        )
        assert resp.status_code == 302

    def test_edit_with_linked_pilot_log_updates_existing_entry(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 6),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.flush()
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe.id,
                date=date(2026, 5, 6),
                departure_place="EBOS",
                arrival_place="EBBR",
            )
            db.session.add(pe)
            db.session.commit()
            feid = fe.id
            peid = pe.id
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-06",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.5",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            # Same entry updated, not a duplicate created
            assert PilotLogbookEntry.query.filter_by(pilot_user_id=uid).count() == 1
            pe2 = db.session.get(PilotLogbookEntry, peid)
            assert pe2 is not None
            assert pe2.flight_id == feid

    def test_detach_pilot_log_unlinks_entry(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 7),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.flush()
            # departure_place/arrival_place intentionally omitted so the
            # duplicate-detection filter (filters by dep+arr) won't match
            # this entry before we reach the detach path.
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe.id,
                date=date(2026, 5, 7),
            )
            db.session.add(pe)
            db.session.commit()
            feid = fe.id
            peid = pe.id
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-07",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "none",
                "detach_pilot_log": "detach",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            pe2 = db.session.get(PilotLogbookEntry, peid)
            assert pe2 is not None
            assert pe2.flight_id is None

    def test_delete_pilot_log_removes_entry(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 8),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.flush()
            # departure_place/arrival_place omitted to avoid triggering
            # duplicate detection before we reach the delete path.
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe.id,
                date=date(2026, 5, 8),
            )
            db.session.add(pe)
            db.session.commit()
            feid = fe.id
            peid = pe.id
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-08",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "none",
                "detach_pilot_log": "delete",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, peid) is None

    def test_parse_gps_import_error_returns_warning(self, app, client):
        """Lines 166-167: ImportError inside _parse_gps_upload returns None."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with patch.dict(sys.modules, {"aircraft.gps_import": None}):
            resp = client.post(
                "/flights/new",
                data={
                    "action": "parse_gps",
                    "aircraft_id": str(acid),
                    "gps_file": (BytesIO(_gpx_bytes()), "track.gpx"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200

    def test_parse_gps_disallowed_extension_returns_warning(self, app, client):
        """Line 171: disallowed extension returns None from _parse_gps_upload."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "action": "parse_gps",
                "aircraft_id": str(acid),
                "gps_file": (BytesIO(b"some data"), "track.txt"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_parse_gps_no_segments_returns_warning(self, app, client):
        """Lines 175-176, 179-180: parse succeeds but detect_segments returns []."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "action": "parse_gps",
                "aircraft_id": str(acid),
                "gps_file": (
                    BytesIO(_gpx_bytes(speeds_ms=[0.0, 0.0, 0.0])),
                    "track.gpx",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_parse_gps_success_stores_prefill(self, app, client):
        """Lines 175-176, 181-182, 466-485: valid flight GPX pre-fills the form."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "action": "parse_gps",
                "aircraft_id": str(acid),
                "gps_file": (BytesIO(_gpx_bytes()), "flight.gpx"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert (
            b"GPS" in resp.data
            or b"pre-fill" in resp.data.lower()
            or b"parsed" in resp.data.lower()
        )

    def test_parse_gps_on_edit_redirects_to_edit_flight(self, app, client):
        """Line 491: action=parse_gps on the edit route redirects back to edit."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 14),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.commit()
            feid = fe.id
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "action": "parse_gps",
                "aircraft_id": str(acid),
                "gps_file": (BytesIO(b"not valid gps data"), "track.gpx"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        assert f"/flights/{feid}/edit" in resp.headers["Location"]

    def test_edit_excludes_self_from_block_time_duplicate_check(self, app, client):
        """Line 218: editing a flight with overlapping block times excludes itself."""
        import json as _json
        from datetime import datetime, timezone

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                block_off_utc=datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc),
                block_on_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
            )
            db.session.add(fe)
            db.session.commit()
            feid = fe.id
        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-15",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.0",
                "gps_block_off_utc": "2026-05-15T10:00:00+00:00",
                "gps_block_on_utc": "2026-05-15T11:00:00+00:00",
                "gps_geojson": _json.dumps(geojson),
            },
        )
        # Without exclude_flight_id the flight would match itself; with it, the edit saves.
        assert resp.status_code == 302

    def test_find_duplicate_excludes_pilot_entry_id(self, app, client):
        """Line 243: exclude_pilot_entry_id filters out the pilot log entry."""
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2026, 5, 16),
                departure_place="EBNM",
                arrival_place="EBAW",
            )
            db.session.add(pe)
            db.session.commit()
            peid = pe.id

        with app.app_context():
            from flights.routes import _find_duplicate_flight  # pyright: ignore[reportMissingImports]

            result = _find_duplicate_flight(
                aircraft_id=None,
                pilot_user_id=uid,
                date=date(2026, 5, 16),
                dep_icao="EBNM",
                arr_icao="EBAW",
                block_off=None,
                block_on=None,
            )
            assert result is not None
            assert result["type"] == "pilot"

            excluded = _find_duplicate_flight(
                aircraft_id=None,
                pilot_user_id=uid,
                date=date(2026, 5, 16),
                dep_icao="EBNM",
                arr_icao="EBAW",
                block_off=None,
                block_on=None,
                exclude_pilot_entry_id=peid,
            )
            assert excluded is None

    def test_edit_excludes_own_linked_pilot_entry_from_duplicate_check(
        self, app, client
    ):
        """Editing a FlightEntry that already has its own linked
        PilotLogbookEntry (the normal case for a flight logged via this form)
        must not flag that pilot entry as a duplicate of itself."""
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 20),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                flight_time=1.0,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    flight_id=fe.id,
                    date=date(2026, 5, 20),
                    departure_place="EBOS",
                    arrival_place="EBBR",
                )
            )
            db.session.commit()
            feid = fe.id
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-20",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "flight_time": "1.0",
            },
        )
        assert resp.status_code == 302

    def test_invalid_gps_hidden_fields_silently_ignored(self, app, client):
        """Lines 720-721, 725-726, 732-733: bad datetime/JSON in GPS hidden fields."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-17",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.0",
                "gps_block_off_utc": "not-a-datetime",
                "gps_block_on_utc": "also-not-a-datetime",
                "gps_geojson": "not-valid-json{{{",
                "gps_filename": "track.gpx",
            },
        )
        assert resp.status_code == 302

    def test_link_gps_updates_linked_pilot_log(self, app, client):
        """Line 785: link_gps on a FlightEntry also updates its linked pilot log entry."""
        import json as _json
        from models import GpsTrack, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 18),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.flush()
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                flight_id=fe.id,
                date=date(2026, 5, 18),
                departure_place="EBOS",
                arrival_place="EBBR",
            )
            db.session.add(pe)
            db.session.commit()
            feid = fe.id
            peid = pe.id
        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-18",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "duplicate_action": "link_gps",
                "gps_geojson": _json.dumps(geojson),
                "gps_filename": "track.gpx",
            },
        )
        assert resp.status_code == 302
        with app.app_context():
            gt = GpsTrack.query.first()
            assert gt is not None
            fe2 = db.session.get(FlightEntry, feid)
            assert fe2.gps_track_id == gt.id
            pe2 = db.session.get(PilotLogbookEntry, peid)
            assert pe2.gps_track_id == gt.id

    def test_link_gps_attaches_to_pilot_logbook_entry(self, app, client):
        """Lines 786-787: link_gps else-branch updates a PilotLogbookEntry directly."""
        import json as _json
        from models import GpsTrack, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2026, 5, 19),
                departure_place="EBNM",
                arrival_place="EBAW",
            )
            db.session.add(pe)
            db.session.commit()
            peid = pe.id
        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        resp = client.post(
            "/flights/new",
            data={
                "other_aircraft": "1",
                "other_ac_make_model": "Cessna C172",
                "other_ac_reg": "OO-TST",
                "date": "2026-05-19",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "duplicate_action": "link_gps",
                "gps_geojson": _json.dumps(geojson),
                "gps_filename": "track.gpx",
            },
        )
        assert resp.status_code == 302
        with app.app_context():
            gt = GpsTrack.query.first()
            assert gt is not None
            pe2 = db.session.get(PilotLogbookEntry, peid)
            assert pe2.gps_track_id == gt.id

    def test_edit_with_existing_gps_track_updates_it(self, app, client):
        """Lines 802-811: editing a flight with an existing GpsTrack updates it in place."""
        import json as _json
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            old_track = GpsTrack(
                source_filename="old.gpx",
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(old_track)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2026, 5, 20),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                gps_track_id=old_track.id,
            )
            db.session.add(fe)
            db.session.commit()
            feid = fe.id
            old_track_id = old_track.id
        new_geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[4.0, 51.0]]},
            "properties": {},
        }
        resp = client.post(
            f"/flights/{feid}/edit",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-20",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "pilot_role": "pic",
                "flight_time": "1.0",
                "gps_filename": "new.gpx",
                "gps_block_off_utc": "2026-05-20T09:00:00+00:00",
                "gps_block_on_utc": "2026-05-20T10:00:00+00:00",
                "gps_geojson": _json.dumps(new_geojson),
            },
        )
        assert resp.status_code == 302
        with app.app_context():
            gt = db.session.get(GpsTrack, old_track_id)
            assert gt is not None
            assert gt.source_filename == "new.gpx"
            assert gt.geojson is not None
            assert GpsTrack.query.count() == 1

    def test_parse_gps_api_no_file_returns_error(self, app, client):
        """Lines 460-469: /flights/parse-gps with no file returns JSON error."""
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/parse-gps", data={}, content_type="multipart/form-data"
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is False

    def test_parse_gps_api_invalid_file_returns_error(self, app, client):
        """Lines 470-479: /flights/parse-gps with unparseable file returns JSON error."""
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/parse-gps",
            data={"gps_file": (BytesIO(b"not gps data"), "track.gpx")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is False

    def test_parse_gps_api_valid_file_returns_prefill(self, app, client):
        """Lines 480+: /flights/parse-gps with a valid flight GPX returns pre-fill JSON."""
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/flights/parse-gps",
            data={"gps_file": (BytesIO(_gpx_bytes()), "flight.gpx")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["filename"] == "flight.gpx"
        assert "date" in data["data"]
        assert data["duplicate"] is None

    def test_parse_gps_api_returns_duplicate_when_pilot_log_matches(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        gpx = _gpx_bytes()
        # First parse to discover the date/route the GPX produces
        resp1 = client.post(
            "/flights/parse-gps",
            data={"gps_file": (BytesIO(gpx), "flight.gpx")},
            content_type="multipart/form-data",
        )
        d = resp1.get_json()["data"]
        with app.app_context():
            pe = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date.fromisoformat(d["date"]),
                departure_place=d["departure_icao"],
                arrival_place=d["arrival_icao"],
            )
            db.session.add(pe)
            db.session.commit()
        resp2 = client.post(
            "/flights/parse-gps",
            data={"gps_file": (BytesIO(gpx), "flight.gpx")},
            content_type="multipart/form-data",
        )
        result = resp2.get_json()
        assert result["success"] is True
        assert result["duplicate"] is not None
        assert result["duplicate"]["dep"] == d["departure_icao"]
        assert result["duplicate"]["arr"] == d["arrival_icao"]

    def test_parse_gps_api_does_not_leak_other_tenant_duplicate(self, app, client):
        """A user cannot probe another tenant's FlightEntry existence/id by
        submitting that tenant's aircraft_id on the parse-gps AJAX endpoint."""
        from datetime import datetime, timezone

        _create_user_and_tenant(app)
        _uid_b, tid_b = _create_user_and_tenant(app, "victim@example.com")
        other_ac_id = _add_aircraft(app, tid_b, "OO-VIC")
        with app.app_context():
            db.session.add(
                FlightEntry(
                    aircraft_id=other_ac_id,
                    date=date(2024, 6, 1),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    block_off_utc=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
                    block_on_utc=datetime(2024, 6, 1, 10, 3, tzinfo=timezone.utc),
                )
            )
            db.session.commit()

        _login(app, client, email="pilot@example.com")
        resp = client.post(
            "/flights/parse-gps",
            data={
                "gps_file": (BytesIO(_gpx_bytes()), "flight.gpx"),
                "aircraft_id": str(other_ac_id),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert resp.get_json()["duplicate"] is None

    def test_parse_gps_api_returns_suggested_aircraft_for_known_device(
        self, app, client
    ):
        """Lines 534-541: _suggested_aircraft_for_device returns aircraft_id."""
        from textwrap import dedent
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        garmin_csv = dedent("""\
            #airframe_info,system_id="TESTDEVABC",product=G1000
            units
            Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix
            2024-06-01,10:00:00,+00:00,51.0,4.5,100,0,3D
            2024-06-01,10:15:00,+00:00,51.3,4.5,1000,65,3D
            2024-06-01,10:30:00,+00:00,51.5,4.5,100,0,3D
        """).encode()
        with app.app_context():
            from models import FlightEntry  # pyright: ignore[reportMissingImports]

            gt = GpsTrack(device_id="TESTDEVABC")
            db.session.add(gt)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 1, 1),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                gps_track_id=gt.id,
            )
            db.session.add(fe)
            db.session.commit()
        resp = client.post(
            "/flights/parse-gps",
            data={"gps_file": (BytesIO(garmin_csv), "log_240601_100000_EBNM.csv")},
            content_type="multipart/form-data",
        )
        result = resp.get_json()
        assert result["success"] is True
        assert result["suggested_aircraft_id"] == acid

    def test_edit_flight_with_gps_device_id_updates_existing_track(self, app, client):
        """Line 941: updating a flight with gps_device_id sets it on existing GpsTrack."""
        import json
        from models import FlightEntry, GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with app.app_context():
            gt = GpsTrack(
                source_filename="old.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(gt)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=acid,
                date=date(2024, 6, 1),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                gps_track_id=gt.id,
            )
            db.session.add(fe)
            db.session.commit()
            fe_id = fe.id
            gt_id = gt.id
        client.post(
            f"/flights/{fe_id}/edit",
            data={
                "date": "2024-06-01",
                "departure_icao": "EBNM",
                "arrival_icao": "EBAW",
                "gps_filename": "old.gpx",
                "gps_device_id": "NEWDEVICE99",
                "gps_geojson": json.dumps(
                    {"type": "FeatureCollection", "features": []}
                ),
                "gps_block_off_utc": "",
                "gps_block_on_utc": "",
                "crew_name_0": "Pilot",
                "pilot_role": "none",
            },
        )
        with app.app_context():
            gt2 = db.session.get(GpsTrack, gt_id)
            assert gt2.device_id == "NEWDEVICE99"


class TestGpsReviewReturnFlow:
    """Cover flights/routes.py lines 1222-1232: gps_review_return hidden fields."""

    def test_redirects_to_review_and_marks_segment_confirmed(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "aircraft_id": acid,
                "confirmed_segments": {},
            }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "gps_review_return_aircraft_id": str(acid),
                "gps_review_return_seg_idx": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert f"/aircraft/{acid}/gps-import/review" in resp.headers["Location"]
        with client.session_transaction() as sess:
            confirmed = sess.get("gps_import", {}).get("confirmed_segments", {})
        assert "0" in confirmed

    def test_redirects_to_review_when_session_aircraft_mismatch(self, app, client):
        """return_ac_id in form but session aircraft_id differs → still redirects."""
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "aircraft_id": 99999,  # does not match acid
                "confirmed_segments": {},
            }
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2026-05-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
                "gps_review_return_aircraft_id": str(acid),
                "gps_review_return_seg_idx": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert f"/aircraft/{acid}/gps-import/review" in resp.headers["Location"]
        with client.session_transaction() as sess:
            confirmed = sess.get("gps_import", {}).get("confirmed_segments", {})
        assert "0" not in confirmed  # session was not updated


# ── Registration lookup endpoint ──────────────────────────────────────────────


class TestRegistrationLookup:
    def test_no_query_returns_null(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/registration-lookup")
        assert resp.status_code == 200
        assert resp.get_json()["result"] is None

    def test_unknown_registration_returns_null(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/registration-lookup?q=OO-ZZZ")
        assert resp.get_json()["result"] is None

    def test_own_history_returns_type(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    aircraft_registration="OO-AAA",
                    aircraft_type="ROBIN DR-401 155CDI",
                    aircraft_type_icao="DR40",
                )
            )
            db.session.commit()
        resp = client.get("/flights/registration-lookup?q=OO-AAA")
        result = resp.get_json()["result"]
        assert result is not None
        assert result["aircraft_type"] == "ROBIN DR-401 155CDI"
        assert result["aircraft_type_icao"] == "DR40"

    def test_normalised_matching(self, app, client):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    aircraft_registration="OO-AAA",
                    aircraft_type="CESSNA C172",
                    aircraft_type_icao="C172",
                )
            )
            db.session.commit()
        # lowercase and without dash should still match
        resp = client.get("/flights/registration-lookup?q=ooaaa")
        assert resp.get_json()["result"]["aircraft_type"] == "CESSNA C172"

    def test_tenant_fallback_when_no_own_history(self, app, client):
        from models import PilotLogbookEntry, TenantUser, Role  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            other = User(
                email="other2@example.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(other)
            db.session.flush()
            db.session.add(TenantUser(user_id=other.id, tenant_id=tid, role=Role.PILOT))
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=other.id,
                    date=date(2024, 6, 1),
                    aircraft_registration="OO-BBB",
                    aircraft_type="PIPER PA-28",
                    aircraft_type_icao="P28A",
                )
            )
            db.session.commit()
        resp = client.get("/flights/registration-lookup?q=OO-BBB")
        result = resp.get_json()["result"]
        assert result is not None
        assert result["aircraft_type"] == "PIPER PA-28"


# ── Single-flight track image and GIF ─────────────────────────────────────────

_SAMPLE_GEOJSON = {
    "type": "Feature",
    "geometry": {
        "type": "LineString",
        "coordinates": [[4.0 + i * 0.05, 51.0 + i * 0.02] for i in range(40)],
    },
    "properties": {},
}

_SPARSE_GEOJSON = {
    "type": "Feature",
    "geometry": {
        "type": "LineString",
        "coordinates": [[4.0, 51.0], [4.5, 51.3], [5.0, 51.0]],
    },
    "properties": {},
}


def _add_flight_with_track(app, aircraft_id, geojson=None):
    from models import GpsTrack  # pyright: ignore[reportMissingImports]

    with app.app_context():
        track = GpsTrack(
            source_filename="test.gpx",
            geojson=geojson if geojson is not None else _SAMPLE_GEOJSON,
        )
        db.session.add(track)
        db.session.flush()
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 6, 1),
            departure_icao="EBBR",
            arrival_icao="EBAW",
            gps_track_id=track.id,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id, track.id


class TestGenerateSingleTrackImage:
    def test_returns_png_bytes(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(
                _SAMPLE_GEOJSON, date="2024-06-01", dep="EBBR", arr="EBAW"
            )
        assert result[:4] == b"\x89PNG"

    def test_returns_png_for_sparse_track(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(_SPARSE_GEOJSON)
        assert result[:4] == b"\x89PNG"

    def test_returns_blank_png_for_no_geojson(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(None)
        assert result[:4] == b"\x89PNG"

    def test_returns_blank_png_for_single_point(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[4.0, 51.0]]},
            "properties": {},
        }
        with app.app_context():
            result = generate_single_track_image(geojson)
        assert result[:4] == b"\x89PNG"

    def test_plain_bg_when_tiles_unavailable(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("utils._make_tile_background", return_value=None):
            with app.app_context():
                result = generate_single_track_image(_SAMPLE_GEOJSON)
        assert result[:4] == b"\x89PNG"

    def test_portrait_orientation(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_single_track_image(
                _SAMPLE_GEOJSON, canvas_w=480, canvas_h=800
            )
        img = _Img.open(_io.BytesIO(result))
        assert img.size == (480, 800)

    def test_no_label_when_fields_empty(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(_SAMPLE_GEOJSON)
        assert result[:4] == b"\x89PNG"

    def test_returns_blank_png_when_projection_none(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("utils._build_gif_projection", return_value=None):
            with app.app_context():
                result = generate_single_track_image(_SAMPLE_GEOJSON)
        assert result[:4] == b"\x89PNG"

    def test_high_res_canvas_bounds_called(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(
                _SAMPLE_GEOJSON, high_res=True, canvas_w=1600, canvas_h=960
            )
        assert result[:4] == b"\x89PNG"

    def test_high_res_fallback_when_canvas_tiles_fail(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        call_count = [0]

        def _none_tiles(*args: object, **kwargs: object) -> None:
            call_count[0] += 1
            return None

        with patch("utils._make_tile_background", side_effect=_none_tiles):
            with app.app_context():
                result = generate_single_track_image(_SAMPLE_GEOJSON, high_res=True)
        assert result[:4] == b"\x89PNG"
        assert call_count[0] == 2  # canvas-extent call + track-bbox fallback

    def test_font_fallback_on_ioerror(self, app):
        from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_image(
                _SAMPLE_GEOJSON, date="2024-06-01", _font_path="/nonexistent/font.ttf"
            )
        assert result[:4] == b"\x89PNG"


class TestGenerateSingleTrackGif:
    def test_returns_gif_bytes(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_gif(
                _SAMPLE_GEOJSON, date="2024-06-01", dep="EBBR", arr="EBAW"
            )
        assert result[:3] == b"GIF"

    def test_sparse_track_falls_back_to_single_frame(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_single_track_gif(_SPARSE_GEOJSON)
        assert result[:3] == b"GIF"
        img = _Img.open(_io.BytesIO(result))
        assert not getattr(img, "is_animated", False)

    def test_none_geojson_returns_gif(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_gif(None)
        assert result[:3] == b"GIF"

    def test_animated_gif_has_multiple_frames(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_single_track_gif(_SAMPLE_GEOJSON)
        img = _Img.open(_io.BytesIO(result))
        assert getattr(img, "n_frames", 1) > 1

    def test_plain_bg_when_tiles_unavailable(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("utils._make_tile_background", return_value=None):
            with app.app_context():
                result = generate_single_track_gif(_SAMPLE_GEOJSON)
        assert result[:3] == b"GIF"

    def test_portrait_canvas(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_single_track_gif(
                _SAMPLE_GEOJSON, canvas_w=480, canvas_h=800
            )
        img = _Img.open(_io.BytesIO(result))
        assert img.size == (480, 800)

    def test_font_fallback_on_ioerror(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_gif(
                _SAMPLE_GEOJSON, date="2024-06-01", _font_path="/nonexistent/font.ttf"
            )
        assert result[:3] == b"GIF"

    def test_high_res_gif(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_single_track_gif(
                _SAMPLE_GEOJSON, high_res=True, canvas_w=1600, canvas_h=960
            )
        assert result[:3] == b"GIF"

    def test_high_res_fallback_when_canvas_tiles_fail(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        def _none_tiles(*args: object, **kwargs: object) -> None:
            return None

        with patch("utils._make_tile_background", side_effect=_none_tiles):
            with app.app_context():
                result = generate_single_track_gif(_SAMPLE_GEOJSON, high_res=True)
        assert result[:3] == b"GIF"

    def test_no_frames_returns_blank_gif(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("utils._build_gif_projection", return_value=None):
            with app.app_context():
                result = generate_single_track_gif(_SAMPLE_GEOJSON)
        assert result[:3] == b"GIF"

    def test_chunk_projection_none_is_skipped(self, app):
        from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        original = __import__("utils")._build_gif_projection
        call_count = [0]

        def _skip_first(*args: object, **kwargs: object) -> object:
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # first chunk projection fails → continue
            return original(*args, **kwargs)

        with patch("utils._build_gif_projection", side_effect=_skip_first):
            with app.app_context():
                result = generate_single_track_gif(_SAMPLE_GEOJSON)
        assert result[:3] == b"GIF"


class TestFlightTrackImageRoute:
    def test_returns_png_when_track_exists(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        resp = client.get(f"/flights/{flight_id}/track/image.png")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"
        assert resp.data[:4] == b"\x89PNG"

    def test_cache_headers_present(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        resp = client.get(f"/flights/{flight_id}/track/image.png")
        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("Cache-Control", "")
        assert resp.headers.get("ETag") is not None

    def test_default_variant_is_cached_after_first_render(self, app, client):
        from unittest.mock import patch
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        with patch("utils.generate_single_track_image") as mocked:
            mocked.return_value = b"\x89PNGfake"
            resp1 = client.get(f"/flights/{flight_id}/track/image.png")
            resp2 = client.get(f"/flights/{flight_id}/track/image.png")
        assert resp1.status_code == resp2.status_code == 200
        assert resp1.data == resp2.data == b"\x89PNGfake"
        assert mocked.call_count == 1  # second request served from the DB cache

        with app.app_context():
            track = db.session.get(GpsTrack, track_id)
            assert bytes(track.cached_png) == b"\x89PNGfake"

    def test_hires_variant_is_never_cached(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        client.get(f"/flights/{flight_id}/track/image.png?quality=hires")
        with app.app_context():
            from models import GpsTrack  # pyright: ignore[reportMissingImports]

            track = db.session.get(GpsTrack, track_id)
            assert track.cached_png is None

    def test_returns_404_when_no_track(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 6, 1),
                departure_icao="EBBR",
                arrival_icao="EBAW",
            )
            db.session.add(fe)
            db.session.commit()
            flight_id = fe.id

        resp = client.get(f"/flights/{flight_id}/track/image.png")
        assert resp.status_code == 404

    def test_returns_404_for_unknown_flight(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/99999/track/image.png")
        assert resp.status_code == 404

    def test_returns_404_for_other_tenant_flight(self, app, client):
        _, _ = _create_user_and_tenant(app)
        _, tid2 = _create_user_and_tenant(app, email="other@example.com")
        _login(app, client)
        ac_id2 = _add_aircraft(app, tid2, registration="OO-OTH")
        flight_id, _ = _add_flight_with_track(app, ac_id2)

        resp = client.get(f"/flights/{flight_id}/track/image.png")
        assert resp.status_code == 404

    def test_portrait_orientation(self, app, client):
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, _ = _add_flight_with_track(app, ac_id)

        resp = client.get(f"/flights/{flight_id}/track/image.png?orientation=portrait")
        assert resp.status_code == 200
        img = _Img.open(_io.BytesIO(resp.data))
        assert img.size == (480, 800)

    def test_requires_login(self, app, client):
        resp = client.get("/flights/1/track/image.png")
        assert resp.status_code in (302, 401)


class TestFlightTrackGifRoute:
    def test_returns_gif_when_track_exists(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, _ = _add_flight_with_track(app, ac_id)

        resp = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp.status_code == 200
        assert resp.content_type == "image/gif"
        assert resp.data[:3] == b"GIF"

    def test_cache_headers_present(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, _ = _add_flight_with_track(app, ac_id)

        resp = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("Cache-Control", "")
        assert resp.headers.get("ETag") is not None

    def test_default_variant_is_cached_after_first_render(self, app, client):
        from unittest.mock import patch
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        with patch("utils.generate_single_track_gif") as mocked:
            mocked.return_value = b"GIFfake"
            resp1 = client.get(f"/flights/{flight_id}/track/animation.gif")
            resp2 = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp1.status_code == resp2.status_code == 200
        assert resp1.data == resp2.data == b"GIFfake"
        assert mocked.call_count == 1  # second request served from the DB cache

        with app.app_context():
            track = db.session.get(GpsTrack, track_id)
            assert bytes(track.cached_gif) == b"GIFfake"

    def test_hires_variant_is_never_cached(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, track_id = _add_flight_with_track(app, ac_id)

        client.get(f"/flights/{flight_id}/track/animation.gif?quality=hires")
        with app.app_context():
            from models import GpsTrack  # pyright: ignore[reportMissingImports]

            track = db.session.get(GpsTrack, track_id)
            assert track.cached_gif is None

    def test_returns_404_when_no_track(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 6, 1),
                departure_icao="EBBR",
                arrival_icao="EBAW",
            )
            db.session.add(fe)
            db.session.commit()
            flight_id = fe.id

        resp = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp.status_code == 404

    def test_returns_404_for_unknown_flight(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/flights/99999/track/animation.gif")
        assert resp.status_code == 404

    def test_returns_404_for_other_tenant_flight(self, app, client):
        _, _ = _create_user_and_tenant(app)
        _, tid2 = _create_user_and_tenant(app, email="other@example.com")
        _login(app, client)
        ac_id2 = _add_aircraft(app, tid2, registration="OO-OTH")
        flight_id, _ = _add_flight_with_track(app, ac_id2)

        resp = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp.status_code == 404

    def test_sparse_track_still_returns_gif(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, _ = _add_flight_with_track(app, ac_id, geojson=_SPARSE_GEOJSON)

        resp = client.get(f"/flights/{flight_id}/track/animation.gif")
        assert resp.status_code == 200
        assert resp.data[:3] == b"GIF"

    def test_portrait_orientation(self, app, client):
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        ac_id = _add_aircraft(app, tid)
        flight_id, _ = _add_flight_with_track(app, ac_id)

        resp = client.get(
            f"/flights/{flight_id}/track/animation.gif?orientation=portrait"
        )
        assert resp.status_code == 200
        img = _Img.open(_io.BytesIO(resp.data))
        assert img.size == (480, 800)

    def test_requires_login(self, app, client):
        resp = client.get("/flights/1/track/animation.gif")
        assert resp.status_code in (302, 401)
