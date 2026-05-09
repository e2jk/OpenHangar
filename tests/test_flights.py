"""
Tests for Phase 3 + Phase 7: Flight logging routes (CRUD + auth guard + validation
+ pilot/notes/tach/photos + component logbooks).
"""
import os
from datetime import date
from io import BytesIO

import bcrypt  # pyright: ignore[reportMissingImports]

from models import Aircraft, Component, ComponentType, FlightEntry, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


def _login_orphan_user(app, client):
    """Create a User with no TenantUser and inject into session."""
    with app.app_context():
        user = User(
            email="orphan@example.com",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
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
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()

        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
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
        ac = Aircraft(tenant_id=tenant_id, registration=registration,
                      make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(app, aircraft_id, comp_type=None, installed_at=None, removed_at=None,
                   time_at_install=0.0, extras=None):
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


def _add_flight(app, aircraft_id, dep="EBOS", arr="EBBR",
                flight_time_counter_start=100.0, flight_time_counter_end=101.5,
                hobbs_start=None, hobbs_end=None,
                flight_date=None, pilot=None, notes=None,
                engine_time_counter_start=None, engine_time_counter_end=None,
                tach_start=None, tach_end=None):
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
            flight_time_counter_start=flight_time_counter_start,
            flight_time_counter_end=flight_time_counter_end,
            pilot=pilot,
            notes=notes,
            engine_time_counter_start=engine_time_counter_start,
            engine_time_counter_end=engine_time_counter_end,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


# ── Auth guard ────────────────────────────────────────────────────────────────

class TestAuthGuard:
    def test_flight_list_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_new_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights/new")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_edit_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights/1/edit")
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

    def test_list_shows_tach(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, tach_start=500.0, tach_end=501.3)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights")
        assert b"500.0" in resp.data
        assert b"501.3" in resp.data

    def test_list_404_for_other_tenant_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        resp = client.get(f"/aircraft/{other_acid}/flights")
        assert resp.status_code == 404


# ── Log flight ─────────────────────────────────────────────────────────────────

class TestLogFlight:
    def test_get_shows_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights/new")
        assert resp.status_code == 200
        assert b"Log Flight" in resp.data

    def test_get_prefills_hobbs_from_existing_flights(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=102.0)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights/new")
        assert b"102.0" in resp.data

    def test_post_creates_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
        }, follow_redirects=False)
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
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "J. Smith",
            "notes": "Test flight",
        })
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.pilot == "J. Smith"
            assert fe.notes == "Test flight"

    def test_post_saves_tach(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "engine_time_counter_start": "500.0",
            "engine_time_counter_end": "501.3",
            "pilot": "Test Pilot",
        })
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert float(fe.engine_time_counter_start) == 500.0
            assert float(fe.engine_time_counter_end) == 501.3

    def test_post_rejects_tach_end_not_greater(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "engine_time_counter_start": "502.0",
            "engine_time_counter_end": "501.0",
        })
        assert resp.status_code == 200
        assert b"Engine counter end" in resp.data

    def test_post_rejects_negative_tach(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "engine_time_counter_start": "-1.0",
            "engine_time_counter_end": "1.0",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_rejects_negative_tach_end(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "engine_time_counter_start": "500.0",
            "engine_time_counter_end": "-1.0",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_rejects_invalid_tach_end(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "engine_time_counter_end": "notanumber",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data

    def test_post_uppercases_icao(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "ebos",
            "arrival_icao": "ebbr",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
        })
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.departure_icao == "EBOS"
            assert fe.arrival_icao == "EBBR"

    def test_post_rejects_hobbs_end_not_greater(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "102.0",
            "flight_time_counter_end": "101.5",
        })
        assert resp.status_code == 200
        assert b"greater than" in resp.data
        with app.app_context():
            assert FlightEntry.query.count() == 0

    def test_post_rejects_missing_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
        })
        assert resp.status_code == 200
        assert b"Date is required" in resp.data

    def test_post_rejects_negative_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "-1.0",
            "flight_time_counter_end": "1.0",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data


# ── Photo uploads ──────────────────────────────────────────────────────────────

class TestPhotoUpload:
    def test_upload_hobbs_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
            "flight_counter_photo": (BytesIO(b"fake image data"), "flight.jpg"),
        }, content_type="multipart/form-data", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.flight_counter_photo is not None
            assert fe.flight_counter_photo.endswith(".jpg")
            assert os.path.isfile(os.path.join(app.config["UPLOAD_FOLDER"], fe.flight_counter_photo))

    def test_upload_tach_photo(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
            "engine_counter_photo": (BytesIO(b"fake tach image"), "engine.png"),
        }, content_type="multipart/form-data")
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe.engine_counter_photo is not None
            assert fe.engine_counter_photo.endswith(".png")

    def test_upload_ignores_invalid_extension(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
            "flight_counter_photo": (BytesIO(b"data"), "file.exe"),
        }, content_type="multipart/form-data")
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
        client.post(f"/aircraft/{acid}/flights/{fid}/edit", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
            "pilot": "Test Pilot",
            "flight_counter_photo": (BytesIO(b"new image"), "new_flight.jpg"),
        }, content_type="multipart/form-data")
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

    def test_delete_flight_tolerates_missing_photo_file(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            fe.flight_counter_photo = "nonexistent.jpg"
            db.session.commit()
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/{fid}/delete", follow_redirects=False)
        assert resp.status_code == 302  # OSError swallowed, no crash


# ── Serve upload ──────────────────────────────────────────────────────────────

class TestServeUpload:
    def test_serve_returns_file(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        fname = "test_serve.jpg"
        fpath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        with open(fpath, "wb") as f:
            f.write(b"image content")
        resp = client.get(f"/uploads/{fname}")
        assert resp.status_code == 200
        assert resp.data == b"image content"


# ── Edit flight ────────────────────────────────────────────────────────────────

class TestEditFlight:
    def test_get_shows_prefilled_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, dep="EBOS", arr="EBBR",
                          hobbs_start=100.0, hobbs_end=101.5)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights/{fid}/edit")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
        assert b"EBBR" in resp.data

    def test_get_prefills_pilot_and_notes(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, pilot="J. Smith", notes="Test notes")
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/flights/{fid}/edit")
        assert b"J. Smith" in resp.data
        assert b"Test notes" in resp.data

    def test_post_updates_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, dep="EBOS", arr="EBBR",
                          hobbs_start=100.0, hobbs_end=101.5)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/{fid}/edit", data={
            "date": "2024-06-02",
            "departure_icao": "ELLX",
            "arrival_icao": "EDDM",
            "flight_time_counter_start": "101.5",
            "flight_time_counter_end": "105.0",
            "pilot": "Updated Pilot",
        }, follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            assert fe.departure_icao == "ELLX"
            assert float(fe.flight_time_counter_end) == 105.0
            assert fe.pilot == "Updated Pilot"

    def test_edit_404_for_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        fid = _add_flight(app, acid1)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid2}/flights/{fid}/edit")
        assert resp.status_code == 404


# ── Delete flight ──────────────────────────────────────────────────────────────

class TestDeleteFlight:
    def test_delete_removes_entry(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/{fid}/delete",
                           follow_redirects=False)
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
        cid = _add_component(app, acid, installed_at=date(2020, 1, 1),
                             time_at_install=500.0, extras={"tbo_hours": 2000})
        _add_flight(app, acid, dep="EBOS", arr="EBBR",
                    hobbs_start=100.0, hobbs_end=101.5,
                    flight_date=date(2024, 1, 15))
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data
        assert b"501.5" in resp.data  # 500 + 1.5 = 501.5 comp hours

    def test_logbook_shows_tbo_remaining(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, installed_at=date(2020, 1, 1),
                             time_at_install=500.0, extras={"tbo_hours": 2000})
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=101.5,
                    flight_date=date(2024, 1, 15))
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"2000" in resp.data
        assert b"remaining" in resp.data

    def test_logbook_filters_by_install_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, installed_at=date(2024, 1, 1))
        _add_flight(app, acid, dep="EBOS", arr="EBBR",
                    hobbs_start=100.0, hobbs_end=101.0,
                    flight_date=date(2023, 12, 31))  # before install
        _add_flight(app, acid, dep="ELLX", arr="EDDM",
                    hobbs_start=101.0, hobbs_end=102.0,
                    flight_date=date(2024, 2, 1))    # after install
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"ELLX" in resp.data
        assert b"EBOS" not in resp.data

    def test_logbook_filters_by_removed_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid,
                             installed_at=date(2023, 1, 1),
                             removed_at=date(2024, 1, 1))
        _add_flight(app, acid, dep="EBOS", arr="EBBR",
                    hobbs_start=100.0, hobbs_end=101.0,
                    flight_date=date(2023, 6, 1))    # during install
        _add_flight(app, acid, dep="ELLX", arr="EDDM",
                    hobbs_start=101.0, hobbs_end=102.0,
                    flight_date=date(2024, 3, 1))    # after removal
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
        cid = _add_component(app, acid, comp_type=ComponentType.PROPELLER,
                             time_at_install=100.0)
        _add_flight(app, acid, hobbs_start=200.0, hobbs_end=201.0,
                    flight_date=date(2024, 1, 15))
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert resp.status_code == 200
        assert b"101.0" in resp.data  # 100 + 1.0 = 101.0 comp hours

    def test_logbook_tbo_overdue(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, time_at_install=1999.0,
                             installed_at=date(2020, 1, 1),
                             extras={"tbo_hours": 2000})
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=102.0,
                    flight_date=date(2024, 1, 15))
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/components/{cid}/logbook")
        assert b"Overdue" in resp.data

    def test_logbook_notes_shown(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid)
        _add_flight(app, acid, notes="Engine ran rough at low RPM",
                    flight_date=date(2024, 1, 15))
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
            _add_flight(app, acid, hobbs_start=hs, hobbs_end=hs + 1.0,
                        flight_date=date(2024, 1, i + 1))
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
                aircraft_id=acid, type=ComponentType.AVIONICS,
                make="Garmin", model="GTN 650",
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
            ac = Aircraft(tenant_id=tenant.id, registration="OO-TST",
                          make="X", model="X")
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
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "not-a-date",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
        })
        assert resp.status_code == 200
        assert b"valid date" in resp.data

    def test_missing_departure_icao_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
        })
        assert resp.status_code == 200
        assert b"Departure" in resp.data

    def test_missing_arrival_icao_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.5",
        })
        assert resp.status_code == 200
        assert b"Arrival" in resp.data

    def test_negative_hobbs_end_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "EBOS",
            "arrival_icao": "EBBR",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "-1.0",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data
