"""Tests for the offline logbook server API: snapshot/CSRF (38a) and sync (38b)."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from flask_wtf.csrf import validate_csrf  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Flight,
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
        fe = Flight(aircraft_id=aircraft_id, **defaults)
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _add_crew(app, flight_id, name, role, sort_order):
    """Unified model: crew identity lives directly on the Flight row, not a
    separate FlightCrew table — sort_order 0 is the implicit PIC slot
    (pic_name; role is always "PIC" so it's not stored separately), sort_order
    1 is the second_crew_* slot (name + role)."""
    with app.app_context():
        fe = db.session.get(Flight, flight_id)
        if sort_order == 0:
            fe.pic_name = name
        else:
            fe.second_crew_name = name
            fe.second_crew_role = role
        db.session.commit()


# Wire-style kwarg names (pre-refactor PilotLogbookEntry columns) that test
# call sites throughout this file still pass to `_add_pilot_entry`.
_PILOT_ENTRY_RENAME = {
    "aircraft_type": "other_aircraft_type",
    "aircraft_type_icao": "other_aircraft_type_icao",
    "aircraft_registration": "other_aircraft_registration",
    "departure_place": "departure_icao",
    "arrival_place": "arrival_icao",
    "remarks": "notes",
}


def _add_pilot_entry(app, pilot_user_id, aircraft_id=None, **kwargs):
    """Create a Flight row occupied by `pilot_user_id` as PIC.

    Standalone by default (aircraft_id=None, matching the old
    PilotLogbookEntry). Pass aircraft_id to create a row linked to a managed
    aircraft instead — the unified model has only one row per flight, so a
    "linked" pilot entry is no longer a second row joined via flight_id,
    just this same row with aircraft_id set (use `_add_flight` with
    pic_user_id=... directly for that case instead of this helper).
    """
    kwargs = {_PILOT_ENTRY_RENAME.get(k, k): v for k, v in kwargs.items()}
    defaults = dict(
        date=date(2024, 1, 15),
        pic_name="Alice",
        landings_day=1,
        function_pic=Decimal("1.3"),
    )
    if aircraft_id is None:
        defaults.update(
            other_aircraft_type="Cessna 172S",
            other_aircraft_registration="OO-PNH",
            departure_icao="EBOS",
            arrival_icao="EBBR",
        )
    defaults.update(kwargs)
    with app.app_context():
        pe = Flight(pic_user_id=pilot_user_id, aircraft_id=aircraft_id, **defaults)
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
        # EASA figures — unified model: these live flat on the same Flight
        # row as the airframe-log fields, not nested under a separate
        # "pilot" key.
        night_time=Decimal("0.4"),
        instrument_time=Decimal("0.2"),
        landings_day=2,
        landings_night=1,
        single_pilot_se=Decimal("1.3"),
        single_pilot_me=None,
        multi_pilot=None,
        function_pic=Decimal("1.3"),
        function_copilot=None,
        function_dual=None,
        function_instructor=None,
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
        "crew_name_1": "Bob",
        "crew_role_1": "COPILOT",
        "night_time": "0.4",
        "instrument_time": "0.2",
        "landings_day": "2",
        "landings_night": "1",
        "single_pilot_se": "1.3",
        "single_pilot_me": "",
        "multi_pilot": "",
        "function_pic": "1.3",
        "function_copilot": "",
        "function_dual": "",
        "function_instructor": "",
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
        "crew_name_1",
        "crew_role_1",
        "night_time",
        "instrument_time",
        "landings_day",
        "landings_night",
        "single_pilot_se",
        "single_pilot_me",
        "multi_pilot",
        "function_pic",
        "function_copilot",
        "function_dual",
        "function_instructor",
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
        fe = db.session.get(Flight, fe_id)
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
        fe = db.session.get(Flight, fe_id)
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
        fe = db.session.get(Flight, fe_id)
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
        fe = db.session.get(Flight, fe_id)
        assert fe.nature_of_flight == "Server value"  # nothing applied


def test_sync_no_conflict_when_user_didnt_touch_drifted_field(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, notes="original")
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
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
        fe = db.session.get(Flight, fe_id)
        fe.nature_of_flight = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["nature_of_flight"] = "Local value"  # conflicting
    fields["notes"] = "clean change"  # not conflicting, but must not apply either

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 409
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
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
        fe = db.session.get(Flight, fe_id)
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
    fields["crew_name_1"] = "Dana"
    fields["crew_role_1"] = "COPILOT"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
        assert fe.pic_name == "Charlie"
        assert fe.second_crew_name == "Dana"
        assert fe.second_crew_role == "COPILOT"


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
        pe = db.session.get(Flight, pe_id)
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
        pe = db.session.get(Flight, pe_id)
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


# ── EASA figures on a linked entry (38h) ─────────────────────────────────────
#
# Unified model: the old nested "pilot" sub-object (a second PilotLogbookEntry
# row joined via flight_id, with its own EASA columns, mirror-time logic for
# departure/arrival time, and a separate pilot/pilot_conflicts/pilot_missing
# sync channel) is gone. A linked entry is one Flight row — EASA figures
# (night_time, landings_day, function_pic, ...) are just more entries in
# FLIGHT_EDITABLE_FIELDS on the same row, so they snapshot, sync, and
# conflict exactly like any other flight field (see the generic sync tests
# above). Everything below that tested the old two-row split — pilot-only
# conflicts reported separately from flight conflicts, "pilot_missing" on
# deleting the second row, derived-field rejection in a separate pilot
# payload, mirror-vs-override departure/arrival time tracking the flight's
# own time, function_pic/function_dual re-derivation from a flight_time
# change — is structurally impossible now (there's only one row, one set of
# EASA columns, no second payload channel) and has been removed rather than
# adapted; the two tests below cover the surviving, genuinely-EASA-specific
# behaviour: the fields are visible flat and they participate in the same
# conflict scan as everything else.


def _linked_entry(client, ac_id, fe_id):
    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    return next(e for e in resp.get_json()["entries"] if e["id"] == fe_id)


def test_snapshot_easa_fields_flat_on_linked_entry(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(
        app,
        ac_id,
        pic_user_id=uid,
        departure_time=time(9, 0),
        arrival_time=time(10, 0),
        flight_time=Decimal("1.0"),
        landings_day=1,
        landings_night=0,
        single_pilot_se=Decimal("1.0"),
        function_pic=Decimal("1.0"),
    )

    entry = _linked_entry(client, ac_id, fe_id)
    assert "pilot" not in entry  # no more separate nested sub-object
    assert entry["fields"]["landings_day"] == "1"
    assert entry["fields"]["landings_night"] == "0"
    assert entry["fields"]["single_pilot_se"] == "1.0"
    assert entry["fields"]["function_pic"] == "1.0"


def test_sync_linked_easa_field_applies(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, pic_user_id=uid, night_time=None)
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["night_time"] = "0.3"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["night_time"] == "0.3"
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
        assert float(fe.night_time) == 0.3


def test_sync_linked_easa_field_conflict_blocks_like_any_other_field(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, pic_user_id=uid, landings_day=1, notes="orig")

    base = _fields(app, client, ac_id, fe_id)
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
        fe.landings_day = 9  # server-side change, conflicts with the local edit below
        db.session.commit()
    fields = dict(base)
    fields["landings_day"] = "3"  # conflicting
    fields["notes"] = "clean change"  # not conflicting, must not apply either

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["status"] == "conflict"
    assert data["conflicts"] == [
        {"field": "landings_day", "base": "1", "local": "3", "server": "9"}
    ]
    assert "pilot_conflicts" not in data
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
        assert fe.landings_day == 9  # nothing applied
        assert fe.notes == "orig"  # the clean change was not applied either


def test_sync_unparseable_easa_decimal_stored_as_none(app, client):
    """A malformed EASA decimal field (can't happen through the real UI,
    but nothing stops a hand-crafted sync body) is silently dropped to None
    by _parse_easa_decimal's except branch rather than 500ing."""
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, pic_user_id=uid, night_time=Decimal("0.5"))
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    base = _fields(app, client, ac_id, fe_id)
    fields = dict(base)
    fields["night_time"] = "not-a-number"

    resp = _sync(client, fe_id, fields, base)
    assert resp.status_code == 200
    assert resp.get_json()["entry"]["night_time"] == ""
    with app.app_context():
        fe = db.session.get(Flight, fe_id)
        assert fe.night_time is None


def test_sync_404_for_standalone_flight_owned_by_another_pilot(app, client):
    """_get_flight_or_404's identity-scoped branch: a standalone (aircraft_id
    NULL) Flight has no tenant to check, so only its own pic/second-crew
    occupant may sync it."""
    _create_user_and_tenant(app)
    other_uid, _ = _create_user_and_tenant(app, email="other@example.com")
    with app.app_context():
        fe = Flight(
            date=date(2024, 1, 15),
            other_aircraft_type="PA28",
            other_aircraft_registration="OO-OTH",
            pic_user_id=other_uid,
            pic_name="Other Pilot",
        )
        db.session.add(fe)
        db.session.commit()
        fe_id = fe.id
    _login(app, client)

    resp = _sync(client, fe_id, {}, {})
    assert resp.status_code == 404


# ── Standalone pilot logbook endpoints (38h) ─────────────────────────────────


def test_pilot_snapshot_excludes_linked_entries(app, client):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_pilot_entry(app, uid, aircraft_id=ac_id)
    standalone_id = _add_pilot_entry(app, uid, aircraft_id=None)

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
        pe = db.session.get(Flight, eid)
        assert pe.notes == "updated"


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
        pe = db.session.get(Flight, eid)
        pe.notes = "Training"
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
        pe = db.session.get(Flight, eid)
        pe.notes = "Server value"
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
        pe = db.session.get(Flight, eid)
        assert pe.notes == "Server value"


def test_pilot_sync_multi_field_one_conflict_blocks_all(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    eid = _add_pilot_entry(app, uid, remarks="original")

    base = _pilot_fields(client, eid)
    with app.app_context():
        pe = db.session.get(Flight, eid)
        pe.pic_name = "Server value"
        db.session.commit()

    fields = dict(base)
    fields["pic_name"] = "Local value"
    fields["remarks"] = "clean change"

    resp = _sync_pilot(client, eid, fields, base)
    assert resp.status_code == 409
    with app.app_context():
        pe = db.session.get(Flight, eid)
        assert pe.notes == "original"


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
        pe = db.session.get(Flight, eid)
        assert pe.entry_type == "fstd"
        assert pe.other_aircraft_type is None
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
    eid = _add_pilot_entry(app, uid, aircraft_id=ac_id)

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
