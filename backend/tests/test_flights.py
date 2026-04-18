"""
Tests for Phase 3: Flight logging routes (CRUD + auth guard + validation).
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date

from models import Aircraft, FlightEntry, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


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


def _add_flight(app, aircraft_id, dep="EBOS", arr="EBBR",
                hobbs_start=100.0, hobbs_end=101.5,
                flight_date=None):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date(2024, 1, 15),
            departure_icao=dep,
            arrival_icao=arr,
            hobbs_start=hobbs_start,
            hobbs_end=hobbs_end,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


# ── Auth guard ────────────────────────────────────────────────────────────────

class TestAuthGuard:
    def test_flight_list_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    def test_new_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights/new")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    def test_edit_flight_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1/flights/1/edit")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]

    def test_delete_flight_redirects_when_not_logged_in(self, client):
        response = client.post("/aircraft/1/flights/1/delete")
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


# ── Flight list ────────────────────────────────────────────────────────────────

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
            "hobbs_start": "100.0",
            "hobbs_end": "101.5",
        }, follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            fe = FlightEntry.query.filter_by(aircraft_id=acid).first()
            assert fe is not None
            assert fe.departure_icao == "EBOS"
            assert fe.arrival_icao == "EBBR"
            assert float(fe.hobbs_start) == 100.0
            assert float(fe.hobbs_end) == 101.5

    def test_post_uppercases_icao(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(f"/aircraft/{acid}/flights/new", data={
            "date": "2024-06-01",
            "departure_icao": "ebos",
            "arrival_icao": "ebbr",
            "hobbs_start": "100.0",
            "hobbs_end": "101.5",
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
            "hobbs_start": "102.0",
            "hobbs_end": "101.5",
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
            "hobbs_start": "100.0",
            "hobbs_end": "101.5",
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
            "hobbs_start": "-1.0",
            "hobbs_end": "1.0",
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data


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
            "hobbs_start": "101.5",
            "hobbs_end": "105.0",
        }, follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            assert fe.departure_icao == "ELLX"
            assert float(fe.hobbs_end) == 105.0

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
        # Most recent 3 dates should appear; earliest 2 should not
        assert b"2024-01-05" in resp.data
        assert b"2024-01-04" in resp.data
        assert b"2024-01-03" in resp.data
        assert b"2024-01-01" not in resp.data
