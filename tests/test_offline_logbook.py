"""Tests for Phase 38a — offline logbook server read side (snapshot + CSRF APIs)."""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flask_wtf.csrf import validate_csrf  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    FlightCrew,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com", password="testpassword123"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(email=email, password_hash=_pw_hash.hash(password), is_active=True)
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


def _add_aircraft(app, tenant_id, registration="OO-PNH", archived=False):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            archived_at=datetime(2025, 1, 1, tzinfo=timezone.utc) if archived else None,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id, **kwargs):
    defaults = {
        "date": date(2024, 1, 15),
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
    }
    defaults.update(kwargs)
    with app.app_context():
        fe = FlightEntry(aircraft_id=aircraft_id, **defaults)
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _add_crew(app, flight_id, name, role, sort_order):
    with app.app_context():
        db.session.add(
            FlightCrew(flight_id=flight_id, name=name, role=role, sort_order=sort_order)
        )
        db.session.commit()


# ── Snapshot API ─────────────────────────────────────────────────────────────


def test_snapshot_fully_populated_entry(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(
        app,
        ac_id,
        departure_time=time(10, 30),
        arrival_time=time(11, 45),
        flight_time=Decimal("1.3"),
        flight_time_counter_start=Decimal("1424.5"),
        flight_time_counter_end=Decimal("1425.8"),
        engine_time_counter_start=Decimal("2200.0"),
        engine_time_counter_end=Decimal("2201.3"),
        fuel_added_qty=Decimal("45.50"),
        fuel_added_unit="L",
        fuel_remaining_qty=Decimal("30.25"),
        fuel_event="before",
        oil_added_l=Decimal("0.50"),
        passenger_count=2,
        landing_count=3,
        nature_of_flight="  Training  ",
        notes="  Some notes  ",
    )
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    _add_crew(app, fe_id, "Bob", "COPILOT", 1)

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["aircraft"]["id"] == ac_id
    assert data["aircraft"]["registration"] == "OO-PNH"
    assert data["aircraft"]["has_flight_counter"] is True
    assert "snapshot_taken_at" in data
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["id"] == fe_id
    assert entry["fields"] == {
        "date": "2024-01-15",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "departure_time": "10:30",
        "arrival_time": "11:45",
        "flight_time": "1.3",
        "flight_time_counter_start": "1424.5",
        "flight_time_counter_end": "1425.8",
        "engine_time_counter_start": "2200.0",
        "engine_time_counter_end": "2201.3",
        "fuel_added_qty": "45.50",
        "fuel_remaining_qty": "30.25",
        "oil_added_l": "0.50",
        "passenger_count": "2",
        "landing_count": "3",
        "nature_of_flight": "Training",
        "notes": "Some notes",
        "fuel_added_unit": "L",
        "fuel_event": "before",
        "crew_name_0": "Alice",
        "crew_role_0": "PIC",
        "crew_name_1": "Bob",
        "crew_role_1": "COPILOT",
    }
    assert entry["meta"]["has_flight_counter_photo"] is False
    assert entry["meta"]["has_engine_counter_photo"] is False
    assert entry["meta"]["has_fuel_photo"] is False
    assert entry["meta"]["has_gps_track"] is False
    assert entry["meta"]["source"] is None
    assert entry["meta"]["created_at"] is not None


def test_snapshot_all_nulls_entry(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_flight(app, ac_id)

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    fields = resp.get_json()["entries"][0]["fields"]
    for key in (
        "departure_time",
        "arrival_time",
        "flight_time",
        "flight_time_counter_start",
        "flight_time_counter_end",
        "engine_time_counter_start",
        "engine_time_counter_end",
        "fuel_added_qty",
        "fuel_remaining_qty",
        "oil_added_l",
        "passenger_count",
        "landing_count",
        "nature_of_flight",
        "notes",
        "fuel_added_unit",
        "fuel_event",
        "crew_name_0",
        "crew_role_0",
        "crew_name_1",
        "crew_role_1",
    ):
        assert fields[key] == "", f"{key} should canonicalize to empty string"
    assert fields["date"] == "2024-01-15"
    assert fields["departure_icao"] == "EBOS"
    assert fields["arrival_icao"] == "EBBR"


def test_snapshot_single_crew_slot(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    fields = resp.get_json()["entries"][0]["fields"]
    assert fields["crew_name_0"] == "Alice"
    assert fields["crew_role_0"] == "PIC"
    assert fields["crew_name_1"] == ""
    assert fields["crew_role_1"] == ""


def test_snapshot_sorted_by_date_then_id(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_later = _add_flight(app, ac_id, date=date(2024, 2, 1))
    fe_earlier = _add_flight(app, ac_id, date=date(2024, 1, 1))
    fe_same_day_first = _add_flight(app, ac_id, date=date(2024, 1, 15))
    fe_same_day_second = _add_flight(app, ac_id, date=date(2024, 1, 15))

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    ids = [e["id"] for e in resp.get_json()["entries"]]
    assert ids == [fe_earlier, fe_same_day_first, fe_same_day_second, fe_later]


def test_snapshot_decimal_precision(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_flight(
        app,
        ac_id,
        flight_time_counter_start=Decimal("1424.50"),
        fuel_added_qty=Decimal("45.5"),
    )

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    fields = resp.get_json()["entries"][0]["fields"]
    assert fields["flight_time_counter_start"] == "1424.5"
    assert fields["fuel_added_qty"] == "45.50"


def test_snapshot_archived_aircraft_included(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid, archived=True)
    _add_flight(app, ac_id)

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    assert resp.status_code == 200
    assert len(resp.get_json()["entries"]) == 1


def test_snapshot_other_tenant_aircraft_404(app, client):
    _create_user_and_tenant(app, email="a@example.com")
    _, tid_b = _create_user_and_tenant(app, email="b@example.com")
    _login(app, client, email="a@example.com")
    ac_id = _add_aircraft(app, tid_b, registration="OO-OTHER")

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    assert resp.status_code == 404


def test_snapshot_missing_aircraft_404(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/api/offline/aircraft/999999/logbook")
    assert resp.status_code == 404


def test_snapshot_orphan_user_403(app, client):
    """A user with no TenantUser row (broken account state) gets 403, not a 500."""
    _, tid = _create_user_and_tenant(app)
    ac_id = _add_aircraft(app, tid)
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

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    assert resp.status_code == 403


def test_snapshot_anonymous_401_json(app, client):
    resp = client.get("/api/offline/aircraft/1/logbook")
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


# ── CSRF API ─────────────────────────────────────────────────────────────────


def test_csrf_endpoint_returns_valid_token(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/api/offline/csrf")
    assert resp.status_code == 200
    token = resp.get_json()["csrf_token"]
    assert isinstance(token, str) and token
    with client.session_transaction() as sess:
        stored = sess.get("csrf_token")
    assert stored is not None
    with app.test_request_context():
        from flask import session as _session

        _session["csrf_token"] = stored
        validate_csrf(token)  # raises on failure


def test_csrf_endpoint_anonymous_401_json(app, client):
    resp = client.get("/api/offline/csrf")
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}
