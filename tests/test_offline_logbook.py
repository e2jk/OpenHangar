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
