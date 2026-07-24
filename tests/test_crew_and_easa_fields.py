"""
Tests for Phase 16: crew identity fields on Flight, EASA fields, counter
pre-fill, flight_time derivation.
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from datetime import date

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    CrewRole,
    Flight,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
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


def _add_aircraft(app, tenant_id, tach_only=False):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration="OO-TST",
            make="Cessna",
            model="172S",
            has_flight_counter=not tach_only,
            flight_counter_offset=0.3,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(
    app,
    aircraft_id,
    hs=100.0,
    he=101.5,
    ts=None,
    te=None,
    pilot="J. Smith",
    nature=None,
):
    with app.app_context():
        fe = Flight(
            aircraft_id=aircraft_id,
            date=date(2024, 1, 15),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=hs,
            flight_time_counter_end=he,
            engine_time_counter_start=ts,
            engine_time_counter_end=te,
            nature_of_flight=nature,
            pic_name=pilot,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _post_flight(client, acid, extra=None):
    data = {
        "aircraft_id": str(acid),
        "date": "2024-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.5",
        "crew_name_0": "J. Smith",
        "crew_role_0": "PIC",
    }
    if extra:
        data.update(extra)
    return client.post("/flights/new", data=data, follow_redirects=True)


# ── Crew identity fields ───────────────────────────────────────────────────────


class TestFlightCrewModel:
    def test_crew_created_with_flight(self, app):
        _, tid = _create_user_and_tenant(app)
        fid = _add_flight(app, _add_aircraft(app, tid))
        with app.app_context():
            fe = db.session.get(Flight, fid)
            assert fe.pic_name == "J. Smith"

    def test_flight_delete_removes_crew_identity_too(self, app):
        # Unified model: crew identity lives on the same row as the flight —
        # deleting the flight is deleting the crew data, there's nothing
        # left to cascade to separately.
        _, tid = _create_user_and_tenant(app)
        fid = _add_flight(app, _add_aircraft(app, tid))
        with app.app_context():
            fe = db.session.get(Flight, fid)
            db.session.delete(fe)
            db.session.commit()
            assert db.session.get(Flight, fid) is None

    def test_second_crew_role_values(self, app):
        _, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            non_pic_roles = [r for r in CrewRole.ALL if r != CrewRole.PIC]
            for i, role in enumerate(non_pic_roles):
                fe = Flight(
                    aircraft_id=acid,
                    date=date(2024, 1, 1 + i),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    pic_name="J. Smith",
                    second_crew_name=f"P{i}",
                    second_crew_role=role,
                )
                db.session.add(fe)
            db.session.commit()
            stored_roles = {
                fe.second_crew_role
                for fe in Flight.query.filter_by(aircraft_id=acid).all()
            }
            assert stored_roles == set(non_pic_roles)

    def test_flight_allows_zero_crew(self, app):
        _, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            fe = Flight(
                aircraft_id=acid,
                date=date(2024, 1, 1),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.commit()
            assert fe.pic_name is None
            assert fe.second_crew_name is None


# ── Counter pre-fill ──────────────────────────────────────────────────────────


class TestCounterPreFill:
    def test_new_flight_prefills_flight_counter_start(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, hs=100.0, he=102.0)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert b"102.0" in resp.data

    def test_new_flight_prefills_engine_counter_start(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, ts=500.0, te=501.3)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert b"501.3" in resp.data

    def test_new_flight_no_prefill_without_prior_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert resp.status_code == 200


# ── Flight time derivation ────────────────────────────────────────────────────


class TestFlightTimeDerivation:
    def test_flight_time_from_counter_diff(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(
            client,
            acid,
            {"flight_time_counter_start": "100.0", "flight_time_counter_end": "101.5"},
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert float(fe.flight_time) == 1.5

    def test_flight_time_manual_override_wins(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(
            client,
            acid,
            {
                "flight_time_counter_start": "100.0",
                "flight_time_counter_end": "101.5",
                "flight_time": "2.0",
            },
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert float(fe.flight_time) == 2.0

    def test_flight_time_engine_offset_for_tach_only(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, tach_only=True)
        _login(app, client)
        _post_flight(
            client,
            acid,
            {
                "flight_time_counter_start": "",
                "flight_time_counter_end": "",
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501.5",
            },
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert float(fe.flight_time) == 1.2  # 1.5 - 0.3 offset

    def test_flight_time_null_when_no_counters(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(
            client,
            acid,
            {
                "flight_time_counter_start": "",
                "flight_time_counter_end": "",
            },
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.flight_time is None


# ── Nature of flight ──────────────────────────────────────────────────────────


class TestNatureSuggestions:
    def test_nature_suggestions_in_new_flight_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert b"Cross-country" in resp.data

    def test_previously_used_nature_appears_in_suggestions(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, nature="Aerobatics")
        _login(app, client)
        resp = client.get(f"/flights/new?aircraft_id={acid}")
        assert b"Aerobatics" in resp.data

    def test_nature_saved_on_post(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(client, acid, {"nature_of_flight": "Training"})
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.nature_of_flight == "Training"


# ── New fields ────────────────────────────────────────────────────────────────


class TestNewFields:
    def test_departure_arrival_time_saved(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(client, acid, {"departure_time": "09:30", "arrival_time": "11:00"})
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.departure_time is not None
            assert fe.departure_time.hour == 9
            assert fe.arrival_time.hour == 11

    def test_invalid_departure_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _post_flight(client, acid, {"departure_time": "not-a-time"})
        assert b"valid UTC time" in resp.data

    def test_invalid_arrival_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _post_flight(client, acid, {"arrival_time": "99:99"})
        assert b"valid UTC time" in resp.data

    def test_negative_flight_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _post_flight(client, acid, {"flight_time": "-1.0"})
        assert b"non-negative" in resp.data

    def test_passenger_count_saved(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(client, acid, {"passenger_count": "3"})
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.passenger_count == 3

    def test_negative_passenger_count_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _post_flight(client, acid, {"passenger_count": "-1"})
        assert b"non-negative" in resp.data

    def test_landing_count_derived_from_pilot_fields(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(client, acid, {"landings_day": "3", "landings_night": "1"})
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.landing_count == 4

    def test_form_renders_new_fields_on_edit(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid, nature="Ferry flight")
        with app.app_context():
            fe = db.session.get(Flight, fid)
            fe.passenger_count = 2
            fe.landing_count = 3
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert b"Ferry flight" in resp.data
        assert b"2" in resp.data
        assert b"3" in resp.data


# ── Two crew members ──────────────────────────────────────────────────────────


class TestTwoCrewMembers:
    def test_two_crew_saved(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(
            client, acid, {"crew_name_1": "M. Dupont", "crew_role_1": "COPILOT"}
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.pic_name == "J. Smith"
            assert fe.second_crew_name == "M. Dupont"
            assert fe.second_crew_role == "COPILOT"

    def test_one_crew_when_second_blank(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        _post_flight(client, acid)
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=acid).first()
            assert fe.pic_name == "J. Smith"
            assert fe.second_crew_name is None

    def test_crew_name_required(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = _post_flight(client, acid, {"crew_name_0": "", "crew_role_0": "PIC"})
        assert resp.status_code == 200
        assert b"required" in resp.data
