"""Tests for the offline logbook server API: snapshot/CSRF (38a) and sync (38b)."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flask_wtf.csrf import validate_csrf  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    FlightCrew,
    FlightEntry,
    PilotLogbookEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from offline.serialize import canonical_pilot_entry  # pyright: ignore[reportMissingImports]


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


def _add_pilot_entry(app, pilot_user_id, flight_id=None, **kwargs):
    defaults = dict(
        date=date(2024, 1, 15),
        aircraft_type="Cessna 172S",
        aircraft_registration="OO-PNH",
        departure_place="EBOS",
        arrival_place="EBBR",
        pic_name="Alice",
        landings_day=1,
        function_pic=Decimal("1.3"),
    )
    defaults.update(kwargs)
    with app.app_context():
        pe = PilotLogbookEntry(
            pilot_user_id=pilot_user_id, flight_id=flight_id, **defaults
        )
        db.session.add(pe)
        db.session.commit()
        return pe.id


def _add_second_pilot(app, tenant_id, email="other@example.com"):
    with app.app_context():
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant_id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id


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


# ── Sync API (38b) ───────────────────────────────────────────────────────────


def _fields(app, client, ac_id, fe_id):
    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    entry = next(e for e in resp.get_json()["entries"] if e["id"] == fe_id)
    return dict(entry["fields"])


def _sync(client, fe_id, fields, base, force_duplicate=False):
    return client.post(
        f"/api/offline/flights/{fe_id}/sync",
        json={"fields": fields, "base": base, "force_duplicate": force_duplicate},
    )


def test_sync_clean_change_applied(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="original")
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["notes"] = "updated notes"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["entry"]["notes"] == "updated notes"
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.notes == "updated notes"


def test_sync_no_conflict_when_server_unchanged(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["nature_of_flight"] = "Training"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["nature_of_flight"] == "Training"


def test_sync_no_conflict_when_server_changed_to_same_value(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.nature_of_flight = "Training"
        db.session.commit()

    fields = dict(base)
    fields["nature_of_flight"] = "Training"  # user picked the same value

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["nature_of_flight"] == "Training"


def test_sync_conflict_when_server_changed_differently(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.nature_of_flight = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["nature_of_flight"] = "Local value"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["status"] == "conflict"
    assert data["conflicts"] == [
        {
            "field": "nature_of_flight",
            "base": base["nature_of_flight"],
            "local": "Local value",
            "server": "Server value",
        }
    ]
    assert data["entry"]["nature_of_flight"] == "Server value"
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.nature_of_flight == "Server value"  # nothing applied


def test_sync_no_conflict_when_user_didnt_touch_drifted_field(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="original")
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.notes = "server drifted this"  # user never touched notes
        db.session.commit()

    fields = dict(base)
    fields["nature_of_flight"] = "Training"  # only field the user changed

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["entry"]["nature_of_flight"] == "Training"
    assert data["entry"]["notes"] == "server drifted this"


def test_sync_multi_field_one_conflict_blocks_all(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="original")

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.nature_of_flight = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["nature_of_flight"] = "Local value"  # conflicting
    fields["notes"] = "clean change"  # not conflicting, but must not apply either

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 409
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.notes == "original"  # the clean change was not applied either


def test_sync_validation_error_counter_end_less_than_start(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["flight_time_counter_start"] = "100.0"
    fields["flight_time_counter_end"] = "50.0"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "invalid"
    assert any("counter" in e.lower() for e in data["errors"])
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.flight_time_counter_start != 100.0


def test_sync_validation_error_negative_landing_count(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["landing_count"] = "-1"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "invalid"
    assert any("landing" in e.lower() for e in data["errors"])


def test_sync_duplicate_guard_on_date_change(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_flight(
        app, ac_id, date=date(2024, 3, 1), departure_icao="EBOS", arrival_icao="EBBR"
    )
    fe_id = _add_flight(app, ac_id, date=date(2024, 1, 15))
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["date"] = "2024-03-01"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "duplicate"

    resp2 = _sync(client, fe_id, fields, base, force_duplicate=True)
    assert resp2.status_code == 200
    assert resp2.get_json()["entry"]["date"] == "2024-03-01"


def test_sync_crew_replacement(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["crew_name_0"] = "Charlie"
    fields["crew_role_0"] = "PIC"
    fields["crew_name_1"] = "Dana"
    fields["crew_role_1"] = "COPILOT"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        crew = (
            FlightCrew.query.filter_by(flight_id=fe_id)
            .order_by(FlightCrew.sort_order)
            .all()
        )
        assert [c.name for c in crew] == ["Charlie", "Dana"]


def test_sync_milestone_hook_called(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["flight_time"] = "1.5"

    with patch("offline.routes._check_flight_hour_milestone") as mock_milestone:
        resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    mock_milestone.assert_called_once()


def test_sync_wrong_tenant_404(app, client):
    _create_user_and_tenant(app, email="a@example.com")
    _, tid_b = _create_user_and_tenant(app, email="b@example.com")
    ac_id = _add_aircraft(app, tid_b, registration="OO-OTHER")
    fe_id = _add_flight(app, ac_id)
    _login(app, client, email="a@example.com")

    resp = _sync(client, fe_id, {}, {})
    assert resp.status_code == 404


def test_sync_missing_flight_404(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = _sync(client, 999999, {}, {})
    assert resp.status_code == 404


def test_sync_anonymous_401_json(app, client):
    resp = client.post("/api/offline/flights/1/sync", json={})
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


def test_sync_malformed_body_not_json_400(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)

    resp = client.post(
        f"/api/offline/flights/{fe_id}/sync",
        data="not json",
        content_type="text/plain",
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_sync_malformed_body_missing_keys_400(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    base = _fields(app, client, ac_id, fe_id)

    resp = client.post(
        f"/api/offline/flights/{fe_id}/sync",
        json={"fields": {"date": base["date"]}, "base": base},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_sync_malformed_body_unknown_field_400(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["bogus_field"] = "x"

    resp = client.post(
        f"/api/offline/flights/{fe_id}/sync",
        json={"fields": fields, "base": base},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_sync_malformed_body_non_string_value_400(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["passenger_count"] = 2  # should be a canonical string, not an int

    resp = client.post(
        f"/api/offline/flights/{fe_id}/sync",
        json={"fields": fields, "base": base},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def _add_viewer_user(app, tenant_id, email="viewer@example.com"):
    with app.app_context():
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant_id, role=Role.VIEWER)
        )
        db.session.commit()
        return user.id


def test_sync_requires_pilot_access(app, client):
    _, tid = _create_user_and_tenant(app)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")

    resp = _sync(client, fe_id, {}, {})
    assert resp.status_code == 403


# ── Workbench page (38d) ─────────────────────────────────────────────────────


def test_workbench_returns_200_for_pilot(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)

    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert resp.status_code == 200
    assert b"oh-workbench-root" in resp.data


def test_workbench_requires_pilot_access(app, client):
    _, tid = _create_user_and_tenant(app)
    ac_id = _add_aircraft(app, tid)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")

    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert resp.status_code == 403


def test_workbench_wrong_tenant_404(app, client):
    _create_user_and_tenant(app, email="a@example.com")
    _, tid_b = _create_user_and_tenant(app, email="b@example.com")
    ac_id = _add_aircraft(app, tid_b, registration="OO-OTHER")
    _login(app, client, email="a@example.com")

    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert resp.status_code == 404


def test_workbench_missing_aircraft_404(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/aircraft/999999/logbook/offline")
    assert resp.status_code == 404


def test_workbench_anonymous_redirects_to_login(app, client):
    resp = client.get("/aircraft/1/logbook/offline")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_workbench_has_data_oh_aircraft_id(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)

    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert f'data-oh-aircraft-id="{ac_id}"'.encode() in resp.data


def test_workbench_has_row_template_and_i18n_bridge(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)

    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert b'<template id="oh-wb-row">' in resp.data
    assert b'id="oh-wb-i18n"' in resp.data
    assert b'type="application/json"' in resp.data


def test_workbench_template_has_no_inline_script_nonce():
    """Child templates may never carry <script nonce> — only base.html and
    share/public.html may (see AGENTS.md); inline scripts are silently
    dropped after an hx-boost navigation."""
    from pathlib import Path

    content = (
        Path(__file__).parent.parent
        / "app"
        / "templates"
        / "offline"
        / "workbench.html"
    ).read_text()
    assert "<script nonce" not in content


# ── Offline-changes page (38e) ────────────────────────────────────────────────


def test_changes_returns_200_for_logged_in_user(app, client):
    _create_user_and_tenant(app)
    _login(app, client)

    resp = client.get("/offline/changes")
    assert resp.status_code == 200
    assert b"oh-changes-root" in resp.data


def test_changes_does_not_require_pilot_access(app, client):
    """Viewing pending changes is harmless — only the sync endpoint itself
    (already pilot-gated) can actually apply them."""
    _, tid = _create_user_and_tenant(app)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")

    resp = client.get("/offline/changes")
    assert resp.status_code == 200


def test_changes_anonymous_redirects_to_login(app, client):
    resp = client.get("/offline/changes")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_changes_has_i18n_bridge_and_no_inline_script(app, client):
    _create_user_and_tenant(app)
    _login(app, client)

    resp = client.get("/offline/changes")
    assert b'id="oh-ch-i18n"' in resp.data
    assert b'type="application/json"' in resp.data


def test_changes_template_has_no_inline_script_nonce():
    from pathlib import Path

    content = (
        Path(__file__).parent.parent / "app" / "templates" / "offline" / "changes.html"
    ).read_text()
    assert "<script nonce" not in content


def test_base_html_queue_badge_links_to_changes(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/offline/changes")
    assert b'id="oh-pwa-queue-badge" href="/offline/changes"' in resp.data


# ── Canonical pilot serializer (38h) ─────────────────────────────────────────


def test_canonical_pilot_entry_full_fields(app):
    uid, _ = _create_user_and_tenant(app)
    pe_id = _add_pilot_entry(
        app,
        uid,
        aircraft_type_icao="C172",
        departure_time=time(9, 0),
        arrival_time=time(10, 15),
        night_time=Decimal("0.5"),
        instrument_time=Decimal("0.2"),
        landings_night=1,
        remarks="  Some notes  ",
        entry_type="flight",
    )
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        fields = canonical_pilot_entry(pe)
    assert fields == {
        "date": "2024-01-15",
        "aircraft_type": "Cessna 172S",
        "aircraft_type_icao": "C172",
        "aircraft_registration": "OO-PNH",
        "departure_place": "EBOS",
        "departure_time": "09:00",
        "arrival_place": "EBBR",
        "arrival_time": "10:15",
        "pic_name": "Alice",
        "night_time": "0.5",
        "instrument_time": "0.2",
        "landings_day": "1",
        "landings_night": "1",
        "single_pilot_se": "",
        "single_pilot_me": "",
        "multi_pilot": "",
        "function_pic": "1.3",
        "function_copilot": "",
        "function_dual": "",
        "function_instructor": "",
        "remarks": "Some notes",
        "entry_type": "flight",
        "fstd_type": "",
        "fstd_duration": "",
    }


def test_canonical_pilot_entry_fstd_session(app):
    uid, _ = _create_user_and_tenant(app)
    pe_id = _add_pilot_entry(
        app,
        uid,
        aircraft_type=None,
        aircraft_registration=None,
        departure_place=None,
        arrival_place=None,
        landings_day=None,
        function_pic=None,
        entry_type="fstd",
        fstd_type="FNPT2",
        fstd_duration=Decimal("1.5"),
    )
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        fields = canonical_pilot_entry(pe)
    assert fields["entry_type"] == "fstd"
    assert fields["fstd_type"] == "FNPT2"
    assert fields["fstd_duration"] == "1.5"
    assert fields["aircraft_type"] == ""
    assert fields["aircraft_registration"] == ""
    assert fields["departure_place"] == ""
    assert fields["departure_time"] == ""
    assert fields["landings_day"] == ""
    assert fields["function_pic"] == ""
    assert fields["pic_name"] == "Alice"  # PIC name is not FSTD-nulled


# ── Linked-entry snapshot extension (38h) ────────────────────────────────────


def _linked_entry(client, ac_id, fe_id):
    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    return next(e for e in resp.get_json()["entries"] if e["id"] == fe_id)


def test_snapshot_pilot_key_present_for_linked_entry(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(
        app,
        ac_id,
        departure_time=time(9, 0),
        arrival_time=time(10, 0),
        flight_time=Decimal("1.0"),
    )
    _add_pilot_entry(
        app,
        uid,
        flight_id=fe_id,
        departure_time=time(9, 0),
        arrival_time=time(10, 0),
        pic_name="Alice",
        landings_day=1,
        landings_night=0,
        single_pilot_se=Decimal("1.0"),
        function_pic=Decimal("1.0"),
        remarks=None,
    )

    entry = _linked_entry(client, ac_id, fe_id)
    assert "pilot" in entry
    assert entry["pilot"]["fields"] == {
        "night_time": "",
        "instrument_time": "",
        "landings_day": "1",
        "landings_night": "0",
        "multi_pilot": "",
        "pic_name": "Alice",
        "departure_time": "",  # mirrors the flight's time
        "arrival_time": "",  # mirrors the flight's time
    }
    assert entry["pilot"]["derived"]["aircraft_type"] == "Cessna 172S"
    assert entry["pilot"]["derived"]["single_pilot_se"] == "1.0"
    assert "night_time" not in entry["pilot"]["derived"]
    assert "departure_time" not in entry["pilot"]["derived"]


def test_snapshot_pilot_key_absent_without_linked_entry(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)

    entry = _linked_entry(client, ac_id, fe_id)
    assert "pilot" not in entry


def test_snapshot_pilot_key_absent_for_other_users_linked_entry(app, client):
    uid, tid = _create_user_and_tenant(app)
    other_uid = _add_second_pilot(app, tid)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_pilot_entry(app, other_uid, flight_id=fe_id)

    entry = _linked_entry(client, ac_id, fe_id)
    assert "pilot" not in entry


# ── Linked-entry sync extension (38h) ────────────────────────────────────────


def _sync_linked(client, fe_id, fields, base, pilot=None, force_duplicate=False):
    body = {"fields": fields, "base": base, "force_duplicate": force_duplicate}
    if pilot is not None:
        body["pilot"] = pilot
    return client.post(f"/api/offline/flights/{fe_id}/sync", json=body)


def test_sync_linked_pilot_happy_path(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(
        app,
        ac_id,
        departure_time=time(9, 0),
        arrival_time=time(10, 0),
        flight_time=Decimal("1.0"),
    )
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app,
        uid,
        flight_id=fe_id,
        departure_time=time(9, 0),
        arrival_time=time(10, 0),
        landings_day=1,
        landings_night=0,
        function_pic=Decimal("1.0"),
    )

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    pilot_fields = dict(pilot_base)
    pilot_fields["night_time"] = "0.3"

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pilot"]["fields"]["night_time"] == "0.3"
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert float(pe.night_time) == 0.3


def test_sync_linked_pilot_only_conflict(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, landings_day=1)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        pe.landings_day = 9
        db.session.commit()
    pilot_fields = dict(pilot_base)
    pilot_fields["landings_day"] = "3"

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["status"] == "conflict"
    assert data["conflicts"] == []
    assert data["pilot_conflicts"] == [
        {"field": "landings_day", "base": "1", "local": "3", "server": "9"}
    ]
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.landings_day == 9  # nothing applied


def test_sync_linked_flight_only_conflict_blocks_pilot_too(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="orig")
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, landings_day=1)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.notes = "server changed"
        db.session.commit()
    fields = dict(base)
    fields["notes"] = "local changed"
    pilot_fields = dict(pilot_base)
    pilot_fields["landings_day"] = "3"  # clean pilot change, must not apply either

    resp = _sync_linked(
        client, fe_id, fields, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 409
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.landings_day == 1
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.notes == "server changed"


def test_sync_linked_both_conflicting_nothing_applied(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="orig")
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, landings_day=1)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        fe.notes = "server flight value"
        pe = db.session.get(PilotLogbookEntry, pe_id)
        pe.landings_day = 9
        db.session.commit()
    fields = dict(base)
    fields["notes"] = "local flight value"
    pilot_fields = dict(pilot_base)
    pilot_fields["landings_day"] = "3"

    resp = _sync_linked(
        client, fe_id, fields, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert len(data["conflicts"]) == 1
    assert len(data["pilot_conflicts"]) == 1
    with app.app_context():
        fe = db.session.get(FlightEntry, fe_id)
        assert fe.notes == "server flight value"
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.landings_day == 9


def test_sync_linked_pilot_missing_when_entry_deleted(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]

    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        db.session.delete(pe)
        db.session.commit()

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_base, "base": pilot_base}
    )
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "pilot_missing"


def test_sync_linked_pilot_payload_naming_derived_field_400(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_pilot_entry(app, uid, flight_id=fe_id)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    pilot_fields = dict(pilot_base)
    pilot_fields["aircraft_type"] = "Hacked"  # derived field, not editable offline

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_sync_linked_pilot_body_not_a_dict_400(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_pilot_entry(app, uid, flight_id=fe_id)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]

    resp = client.post(
        f"/api/offline/flights/{fe_id}/sync",
        json={"fields": base, "base": base, "pilot": "not-a-dict"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_sync_linked_pilot_validation_errors_every_field(app, client):
    """Every parse_linked_pilot_fields error branch, in one request."""
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    _add_pilot_entry(app, uid, flight_id=fe_id, landings_day=1)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    pilot_fields = dict(pilot_base)
    pilot_fields["night_time"] = "-1"
    pilot_fields["instrument_time"] = "-1"
    pilot_fields["landings_day"] = "-1"
    pilot_fields["landings_night"] = "-1"
    pilot_fields["multi_pilot"] = "-1"
    pilot_fields["departure_time"] = "bad"
    pilot_fields["arrival_time"] = "bad"

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "invalid"
    assert len(data["errors"]) == 7


def test_sync_linked_flight_date_change_propagates_to_pilot_date(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, date=date(2024, 1, 15))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, date=date(2024, 1, 15))

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    fields = dict(base)
    fields["date"] = "2024-02-01"

    resp = _sync_linked(client, fe_id, fields, base)  # no pilot payload
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.date == date(2024, 2, 1)


def test_sync_linked_flight_time_change_propagates_to_function_pic(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, flight_time=Decimal("1.0"))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app, uid, flight_id=fe_id, function_pic=Decimal("1.0"), function_dual=None
    )

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    fields = dict(base)
    fields["flight_time"] = "2.5"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert float(pe.function_pic) == 2.5
        assert pe.function_dual is None


def test_sync_linked_dual_role_recovered_from_function_dual(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, flight_time=Decimal("1.0"))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app, uid, flight_id=fe_id, function_pic=None, function_dual=Decimal("1.0")
    )

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    fields = dict(base)
    fields["flight_time"] = "2.0"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert float(pe.function_dual) == 2.0
        assert pe.function_pic is None


def test_sync_linked_neither_role_leaves_function_columns_null(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, flight_time=Decimal("1.0"))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app, uid, flight_id=fe_id, function_pic=None, function_dual=None
    )

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    fields = dict(base)
    fields["flight_time"] = "2.0"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.function_pic is None
        assert pe.function_dual is None


def test_sync_linked_notes_propagates_to_remarks(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="orig")
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, remarks="orig")

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    fields = dict(base)
    fields["notes"] = "updated"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.remarks == "updated"


def test_sync_linked_mirror_time_tracks_updated_flight_time(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, departure_time=time(9, 0))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app, uid, flight_id=fe_id, departure_time=time(9, 0)
    )  # mirrors

    entry = _linked_entry(client, ac_id, fe_id)
    assert entry["pilot"]["fields"]["departure_time"] == ""
    base = entry["fields"]
    fields = dict(base)
    fields["departure_time"] = "09:30"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.departure_time == time(9, 30)


def test_sync_linked_override_survives_flight_time_change(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, departure_time=time(9, 0))
    _add_crew(app, fe_id, "Alice", "PIC", 0)
    pe_id = _add_pilot_entry(
        app, uid, flight_id=fe_id, departure_time=time(8, 45)
    )  # override

    entry = _linked_entry(client, ac_id, fe_id)
    assert entry["pilot"]["fields"]["departure_time"] == "08:45"
    base = entry["fields"]
    fields = dict(base)
    fields["departure_time"] = "09:30"

    resp = _sync_linked(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.departure_time == time(8, 45)  # override preserved


def test_sync_linked_override_equal_to_flight_time_canonicalizes_to_mirror(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, departure_time=time(9, 0))
    pe_id = _add_pilot_entry(app, uid, flight_id=fe_id, departure_time=time(8, 45))
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    entry = _linked_entry(client, ac_id, fe_id)
    base = entry["fields"]
    pilot_base = entry["pilot"]["fields"]
    assert pilot_base["departure_time"] == "08:45"

    pilot_fields = dict(pilot_base)
    pilot_fields["departure_time"] = "09:00"  # override set equal to flight's time

    resp = _sync_linked(
        client, fe_id, base, base, pilot={"fields": pilot_fields, "base": pilot_base}
    )
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, pe_id)
        assert pe.departure_time == time(9, 0)

    entry2 = _linked_entry(client, ac_id, fe_id)
    assert entry2["pilot"]["fields"]["departure_time"] == ""


# ── Standalone pilot logbook endpoints (38h) ─────────────────────────────────


def test_pilot_snapshot_excludes_linked_entries(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    _add_pilot_entry(app, uid, flight_id=fe_id)
    standalone_id = _add_pilot_entry(app, uid, flight_id=None)

    resp = client.get("/api/offline/pilot/logbook")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.get_json()["entries"]]
    assert ids == [standalone_id]


def test_pilot_snapshot_sorted_by_date_then_id(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    later = _add_pilot_entry(app, uid, date=date(2024, 2, 1))
    earlier = _add_pilot_entry(app, uid, date=date(2024, 1, 1))

    resp = client.get("/api/offline/pilot/logbook")
    ids = [e["id"] for e in resp.get_json()["entries"]]
    assert ids == [earlier, later]


def test_pilot_snapshot_anonymous_401_json(app, client):
    resp = client.get("/api/offline/pilot/logbook")
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


def test_pilot_snapshot_requires_pilot_access(app, client):
    _, tid = _create_user_and_tenant(app)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")
    resp = client.get("/api/offline/pilot/logbook")
    assert resp.status_code == 403


def _pilot_fields(client, entry_id):
    resp = client.get("/api/offline/pilot/logbook")
    entry = next(e for e in resp.get_json()["entries"] if e["id"] == entry_id)
    return dict(entry["fields"])


def _sync_pilot(client, entry_id, fields, base):
    return client.post(
        f"/api/offline/pilot/logbook/{entry_id}/sync",
        json={"fields": fields, "base": base},
    )


def test_pilot_sync_clean_change_applied(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid, remarks="original")

    base = _pilot_fields(client, eid)
    fields = dict(base)
    fields["remarks"] = "updated"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["entry"]["remarks"] == "updated"
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        assert pe.remarks == "updated"


def test_pilot_sync_no_conflict_when_server_unchanged(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    base = _pilot_fields(client, eid)
    fields = dict(base)
    fields["remarks"] = "Training"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["remarks"] == "Training"


def test_pilot_sync_no_conflict_when_server_changed_to_same_value(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    base = _pilot_fields(client, eid)
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        pe.remarks = "Training"
        db.session.commit()

    fields = dict(base)
    fields["remarks"] = "Training"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["remarks"] == "Training"


def test_pilot_sync_conflict_when_server_changed_differently(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    base = _pilot_fields(client, eid)
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        pe.remarks = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["remarks"] = "Local value"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["status"] == "conflict"
    assert data["conflicts"] == [
        {
            "field": "remarks",
            "base": base["remarks"],
            "local": "Local value",
            "server": "Server value",
        }
    ]
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        assert pe.remarks == "Server value"


def test_pilot_sync_multi_field_one_conflict_blocks_all(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid, remarks="original")

    base = _pilot_fields(client, eid)
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        pe.pic_name = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["pic_name"] = "Local value"
    fields["remarks"] = "clean change"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 409
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        assert pe.remarks == "original"


def test_pilot_sync_validation_error_negative_landings(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    base = _pilot_fields(client, eid)
    fields = dict(base)
    fields["landings_day"] = "-1"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_pilot_sync_fstd_toggle_applies(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    base = _pilot_fields(client, eid)
    fields = dict(base)
    fields["entry_type"] = "fstd"
    fields["fstd_type"] = "FNPT2"
    fields["fstd_duration"] = "1.5"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        pe = db.session.get(PilotLogbookEntry, eid)
        assert pe.entry_type == "fstd"
        assert pe.aircraft_type is None
        assert float(pe.fstd_duration) == 1.5


def test_pilot_sync_other_users_entry_404(app, client):
    _, tid = _create_user_and_tenant(app, email="a@example.com")
    uid_b = _add_second_pilot(app, tid, email="b@example.com")
    eid = _add_pilot_entry(app, uid_b)
    _login(app, client, email="a@example.com")

    resp = _sync_pilot(client, eid, {}, {})
    assert resp.status_code == 404


def test_pilot_sync_linked_entry_hit_on_standalone_endpoint_404(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id)
    eid = _add_pilot_entry(app, uid, flight_id=fe_id)

    resp = _sync_pilot(client, eid, {}, {})
    assert resp.status_code == 404


def test_pilot_sync_missing_entry_404(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = _sync_pilot(client, 999999, {}, {})
    assert resp.status_code == 404


def test_pilot_sync_anonymous_401_json(app, client):
    resp = client.post("/api/offline/pilot/logbook/1/sync", json={})
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


def test_pilot_sync_requires_pilot_access(app, client):
    uid, tid = _create_user_and_tenant(app)
    eid = _add_pilot_entry(app, uid)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")

    resp = _sync_pilot(client, eid, {}, {})
    assert resp.status_code == 403


def test_pilot_sync_malformed_body_missing_keys_400(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)
    base = _pilot_fields(client, eid)

    resp = client.post(
        f"/api/offline/pilot/logbook/{eid}/sync",
        json={"fields": {"date": base["date"]}, "base": base},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


def test_pilot_sync_malformed_body_not_json_400(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid)

    resp = client.post(
        f"/api/offline/pilot/logbook/{eid}/sync",
        data="not json",
        content_type="text/plain",
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "invalid"


# ── Standalone pilot workbench page (38i) ────────────────────────────────────


def test_pilot_workbench_returns_200(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/pilot/logbook/offline")
    assert resp.status_code == 200
    assert b"oh-pilot-workbench-root" in resp.data


def test_pilot_workbench_requires_pilot_access(app, client):
    _, tid = _create_user_and_tenant(app)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")
    resp = client.get("/pilot/logbook/offline")
    assert resp.status_code == 403


def test_pilot_workbench_anonymous_redirects_to_login(app, client):
    resp = client.get("/pilot/logbook/offline")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_pilot_workbench_has_row_template_and_i18n_bridge(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/pilot/logbook/offline")
    assert b'<template id="oh-pwb-row">' in resp.data
    assert b'id="oh-pwb-i18n"' in resp.data
    assert b'type="application/json"' in resp.data


def test_pilot_workbench_has_data_oh_pilot_logbook(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/pilot/logbook/offline")
    assert b'data-oh-pilot-logbook="1"' in resp.data
