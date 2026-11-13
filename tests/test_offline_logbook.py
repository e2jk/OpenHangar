"""Read-only offline logbooks: snapshot APIs, pages, and the removed editing.

Offline editing was removed: nothing can queue or replay a write. While
online, opening a logbook stores a snapshot in the browser; the offline
logbook pages render it, read-only, with or without a connection.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest
from models import (
    Aircraft,
    Flight,
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
            archived_at=datetime(2025, 1, 1, tzinfo=UTC) if archived else None,
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
    defaults = {
        "date": date(2024, 1, 15),
        "pic_name": "Alice",
        "landings_day": 1,
        "function_pic": Decimal("1.3"),
    }
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


# ── Aircraft snapshot API ──────────────────────────────────────────────────────


def test_aircraft_snapshot_rows_are_display_strings(app, client):
    _uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(
        app,
        ac_id,
        departure_icao=" ebos ",
        departure_time=time(10, 30),
        arrival_time=time(11, 45),
        flight_time=Decimal("1.30"),
        flight_time_counter_start=Decimal("1424.50"),
        flight_time_counter_end=Decimal("1425.8"),
        engine_time_counter_start=Decimal(2200),
        engine_time_counter_end=Decimal("2201.3"),
        passenger_count=2,
        landing_count=3,
        notes="  Some notes  ",
    )
    _add_crew(app, fe_id, "Alice", "PIC", 0)

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["aircraft"] == {"id": ac_id, "registration": "OO-PNH"}
    assert "snapshot_taken_at" in data
    assert data["entries"] == [
        {
            "id": fe_id,
            "fields": {
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
                "landing_count": "3",
                "passenger_count": "2",
                "pic_name": "Alice",
                "notes": "Some notes",
            },
        }
    ]


def test_aircraft_snapshot_empty_values_are_empty_strings(app, client):
    _uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_flight(app, ac_id, departure_icao=None, arrival_icao=None)

    fields = client.get(f"/api/offline/aircraft/{ac_id}/logbook").get_json()["entries"][
        0
    ]["fields"]
    assert fields["date"] == "2024-01-15"
    assert {value for key, value in fields.items() if key != "date"} == {""}


def test_aircraft_snapshot_is_the_whole_logbook_in_date_order(app, client):
    _uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid, archived=True)
    later = _add_flight(app, ac_id, date=date(2024, 2, 1))
    earlier = _add_flight(app, ac_id, date=date(2024, 1, 1))
    same_day_first = _add_flight(app, ac_id, date=date(2024, 1, 15))
    same_day_second = _add_flight(app, ac_id, date=date(2024, 1, 15))

    resp = client.get(f"/api/offline/aircraft/{ac_id}/logbook")
    ids = [e["id"] for e in resp.get_json()["entries"]]
    assert ids == [earlier, same_day_first, same_day_second, later]


def test_aircraft_snapshot_other_tenant_aircraft_404(app, client):
    _create_user_and_tenant(app, email="a@example.com")
    _, tid_b = _create_user_and_tenant(app, email="b@example.com")
    _login(app, client, email="a@example.com")
    ac_id = _add_aircraft(app, tid_b, registration="OO-OTHER")
    assert client.get(f"/api/offline/aircraft/{ac_id}/logbook").status_code == 404


def test_aircraft_snapshot_missing_aircraft_404(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    assert client.get("/api/offline/aircraft/999999/logbook").status_code == 404


def test_aircraft_snapshot_anonymous_401_json(client):
    resp = client.get("/api/offline/aircraft/1/logbook")
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


# ── Pilot snapshot API ─────────────────────────────────────────────────────────


def test_pilot_snapshot_is_the_whole_logbook(app, client):
    """Fleet flights and standalone entries alike, as in the online logbook."""
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fleet = _add_flight(app, ac_id, pic_user_id=uid, date=date(2024, 3, 1))
    later = _add_pilot_entry(app, uid, date=date(2024, 2, 1))
    earlier = _add_pilot_entry(app, uid, date=date(2024, 1, 1))
    other = _add_second_pilot(app, tid)
    _add_pilot_entry(app, other)

    entries = client.get("/api/offline/pilot/logbook").get_json()["entries"]
    assert [e["id"] for e in entries] == [earlier, later, fleet]
    fleet_row = entries[2]["fields"]
    assert fleet_row["aircraft_type"] == "Cessna 172S"
    assert fleet_row["registration"] == "OO-PNH"


def test_pilot_snapshot_row_fields(app, client):
    uid, _ = _create_user_and_tenant(app)
    _login(app, client)
    entry_id = _add_pilot_entry(
        app,
        uid,
        departure_time=time(9, 5),
        arrival_time=time(10, 20),
        landings_night=2,
        single_pilot_se=Decimal("1.25"),
        night_time=Decimal("0.4"),
        instrument_time=Decimal("0.2"),
        remarks="Cross-country",
    )
    with app.app_context():
        db.session.get(Flight, entry_id).pic_remarks = "My note"
        db.session.commit()

    fields = client.get("/api/offline/pilot/logbook").get_json()["entries"][0]["fields"]
    assert fields == {
        "date": "2024-01-15",
        "entry_type": "flight",
        "fstd_type": "",
        "aircraft_type": "Cessna 172S",
        "registration": "OO-PNH",
        "departure_place": "EBOS",
        "departure_time": "09:05",
        "arrival_place": "EBBR",
        "arrival_time": "10:20",
        "pic_name": "Alice",
        "landings_day": "1",
        "landings_night": "2",
        "total_time": "1.2",
        "fstd_duration": "",
        "night_time": "0.4",
        "instrument_time": "0.2",
        "remarks": "Cross-country · My note",
        "function_pic": "1.3",
        "function_copilot": "",
        "function_dual": "",
        "function_instructor": "",
    }


def test_pilot_snapshot_hides_the_other_pilots_part_of_a_shared_flight(app, client):
    uid, tid = _create_user_and_tenant(app)
    other = _add_second_pilot(app, tid)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    _add_flight(
        app,
        ac_id,
        pic_user_id=other,
        second_crew_user_id=uid,
        function_pic=Decimal("1.5"),
        function_dual=Decimal("1.5"),
        pic_remarks="PIC's private remark",
        second_crew_remarks="Student's remark",
    )
    fields = client.get("/api/offline/pilot/logbook").get_json()["entries"][0]["fields"]
    assert fields["function_pic"] == ""
    assert fields["function_dual"] == "1.5"
    assert fields["remarks"] == "Student's remark"


def test_pilot_snapshot_anonymous_401_json(client):
    resp = client.get("/api/offline/pilot/logbook")
    assert resp.status_code == 401
    assert resp.get_json() == {"status": "auth"}


def test_pilot_snapshot_requires_pilot_access(app, client):
    _, tid = _create_user_and_tenant(app)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")
    assert client.get("/api/offline/pilot/logbook").status_code == 403


# ── Identity check used by the service worker ─────────────────────────────────


def test_identity_check_returns_identity_headers(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/api/offline/identity")
    assert resp.status_code == 204
    assert "X-OH-Offline-Owner" in resp.headers


def test_identity_check_anonymous_401_json(client):
    resp = client.get("/api/offline/identity")
    assert resp.status_code == 401
    assert resp.headers["X-OH-Offline-Owner"] == ""


# ── Read-only offline pages ────────────────────────────────────────────────────


def test_aircraft_offline_page_is_read_only(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    resp = client.get(f"/aircraft/{ac_id}/logbook/offline")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'data-oh-offline-logbook="aircraft"' in html
    assert f'data-oh-aircraft-id="{ac_id}"' in html
    assert 'id="oh-offline-row"' in html
    assert 'id="oh-offline-logbook-i18n"' in html
    page = html[html.index('id="oh-offline-logbook"') :]
    assert "<input" not in page and "<select" not in page and "<textarea" not in page


def test_pilot_offline_page_is_read_only(app, client):
    _create_user_and_tenant(app)
    _login(app, client)
    resp = client.get("/pilot/logbook/offline")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'data-oh-offline-logbook="pilot"' in html
    assert 'data-oh-pilot-logbook="1"' in html
    page = html[html.index('id="oh-offline-logbook"') :]
    assert "<input" not in page and "<select" not in page and "<textarea" not in page


@pytest.mark.parametrize(
    "path", ["/aircraft/{ac}/logbook/offline", "/pilot/logbook/offline"]
)
def test_offline_pages_require_pilot_access(app, client, path):
    _, tid = _create_user_and_tenant(app)
    ac_id = _add_aircraft(app, tid)
    _add_viewer_user(app, tid)
    _login(app, client, email="viewer@example.com")
    assert client.get(path.format(ac=ac_id)).status_code == 403


@pytest.mark.parametrize(
    "path", ["/aircraft/{ac}/logbook/offline", "/pilot/logbook/offline"]
)
def test_offline_pages_redirect_anonymous_to_login(app, client, path):
    _, tid = _create_user_and_tenant(app)
    ac_id = _add_aircraft(app, tid)
    resp = client.get(path.format(ac=ac_id))
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_aircraft_offline_page_other_tenant_404(app, client):
    _create_user_and_tenant(app, email="a@example.com")
    _, tid_b = _create_user_and_tenant(app, email="b@example.com")
    _login(app, client, email="a@example.com")
    ac_id = _add_aircraft(app, tid_b, registration="OO-OTHER")
    assert client.get(f"/aircraft/{ac_id}/logbook/offline").status_code == 404


def test_offline_templates_have_no_inline_script():
    from pathlib import Path

    for name in (
        "aircraft_logbook.html",
        "pilot_logbook.html",
        "_snapshot_notice.html",
    ):
        text = (Path("app/templates/offline") / name).read_text()
        assert "<script nonce" not in text


def test_logbooks_link_to_their_offline_page(app, client):
    _, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    flights = client.get(f"/aircraft/{ac_id}/flights").data.decode()
    assert "/aircraft/OO-PNH/logbook/offline" in flights
    assert "Offline logbook" in flights
    assert "/pilot/logbook/offline" in client.get("/pilot/logbook").data.decode()


# ── Offline editing is gone ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/offline/flights/{fe}/sync"),
        ("post", "/api/offline/pilot/logbook/{pe}/sync"),
        ("get", "/api/offline/csrf"),
        ("get", "/offline/changes"),
        ("get", "/api/check-flight-duplicate"),
    ],
)
def test_offline_editing_endpoints_are_gone(app, client, method, path):
    uid, tid = _create_user_and_tenant(app)
    _login(app, client)
    ac_id = _add_aircraft(app, tid)
    fe_id = _add_flight(app, ac_id, pic_user_id=uid)
    pe_id = _add_pilot_entry(app, uid)
    resp = getattr(client, method)(
        path.format(fe=fe_id, pe=pe_id), json={"fields": {}, "base": {}}
    )
    assert resp.status_code in (404, 405)
    with app.app_context():
        assert db.session.get(Flight, fe_id).notes is None
