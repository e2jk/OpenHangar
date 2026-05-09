"""
Tests for Phase 17: PilotProfile model, PilotLogbookEntry model, pilot logbook routes.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, time

import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, FlightCrew, FlightEntry, PilotLogbookEntry, PilotProfile,
    Role, Tenant, TenantUser, User, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration="OO-TST",
            make="Cessna", model="172S",
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 1, 15),
            departure_icao="EBOS", arrival_icao="EBBR",
            flight_time_counter_start=100.0, flight_time_counter_end=101.5,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(FlightCrew(flight_id=fe.id, name="J. Smith", role="PIC", sort_order=0))
        db.session.commit()
        return fe.id


def _add_logbook_entry(app, user_id, flight_id=None, **kwargs):
    defaults = dict(
        date=date(2024, 3, 1),
        aircraft_type="C172S",
        aircraft_registration="OO-TST",
        departure_place="EBOS",
        arrival_place="EBBR",
        single_pilot_se=1.5,
        landings_day=1,
        function_pic=1.5,
    )
    defaults.update(kwargs)
    with app.app_context():
        entry = PilotLogbookEntry(
            pilot_user_id=user_id,
            flight_id=flight_id,
            **defaults,
        )
        db.session.add(entry)
        db.session.commit()
        return entry.id


def _post_entry(client, extra=None):
    data = {
        "date": "2024-06-01",
        "aircraft_type": "C172S",
        "aircraft_registration": "OO-TST",
        "departure_place": "EBOS",
        "arrival_place": "EBBR",
        "single_pilot_se": "1.5",
        "landings_day": "1",
        "function_pic": "1.5",
    }
    if extra:
        data.update(extra)
    return client.post("/pilot/logbook/new", data=data, follow_redirects=True)


# ── PilotProfile model ────────────────────────────────────────────────────────

class TestPilotProfileModel:
    def test_create_profile(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            p = PilotProfile(
                user_id=uid,
                license_number="BE.PPL.A.12345",
                medical_expiry=date(2027, 6, 1),
                sep_expiry=date(2026, 9, 30),
            )
            db.session.add(p)
            db.session.commit()
            stored = PilotProfile.query.filter_by(user_id=uid).first()
            assert stored.license_number == "BE.PPL.A.12345"
            assert stored.medical_expiry == date(2027, 6, 1)
            assert stored.sep_expiry == date(2026, 9, 30)

    def test_profile_nullable_fields(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            p = PilotProfile(user_id=uid)
            db.session.add(p)
            db.session.commit()
            stored = PilotProfile.query.filter_by(user_id=uid).first()
            assert stored.license_number is None
            assert stored.medical_expiry is None
            assert stored.sep_expiry is None

    def test_profile_unique_per_user(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid))
            db.session.commit()
            db.session.add(PilotProfile(user_id=uid))
            import sqlalchemy.exc
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.commit()


# ── PilotLogbookEntry model ───────────────────────────────────────────────────

class TestPilotLogbookEntryModel:
    def test_total_flight_time_se_only(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid, single_pilot_se=1.5, single_pilot_me=None, multi_pilot=None)
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time == 1.5

    def test_total_flight_time_sum_all(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid, single_pilot_se=1.0, single_pilot_me=0.5, multi_pilot=0.8)
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time == 2.3

    def test_total_flight_time_none_when_no_columns(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid, single_pilot_se=None, single_pilot_me=None, multi_pilot=None)
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time is None

    def test_flight_entry_deletion_sets_null(self, app):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            db.session.delete(fe)
            db.session.commit()
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry is not None
            assert entry.flight_id is None

    def test_multiple_entries_for_same_pilot(self, app):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1))
        _add_logbook_entry(app, uid, date=date(2024, 2, 1))
        with app.app_context():
            entries = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).all()
            assert len(entries) == 2


# ── Logbook route: list & totals ──────────────────────────────────────────────

class TestLogbookRoutes:
    def test_logbook_requires_login(self, app, client):
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 302

    def test_logbook_empty(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 200
        assert b"No logbook entries" in resp.data

    def test_logbook_shows_entries(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, departure_place="EBOS", arrival_place="EBBR")
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert b"EBOS" in resp.data
        assert b"EBBR" in resp.data

    def test_logbook_shows_totals(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, single_pilot_se=1.5)
        _add_logbook_entry(app, uid, single_pilot_se=2.0)
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert b"Totals" in resp.data
        assert b"3.5" in resp.data

    def test_logbook_only_shows_own_entries(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        _add_logbook_entry(app, uid1, departure_place="EHAM")
        _login(app, client, email="b@x.com")
        resp = client.get("/pilot/logbook")
        assert b"EHAM" not in resp.data

    def test_logbook_default_order_is_antichronological(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1), departure_place="EARLY")
        _add_logbook_entry(app, uid, date=date(2024, 6, 1), departure_place="LATER")
        _login(app, client)
        resp = client.get("/pilot/logbook")
        pos_early = resp.data.find(b"EARLY")
        pos_later = resp.data.find(b"LATER")
        assert pos_later < pos_early  # most recent appears first in HTML

    def test_logbook_asc_order_toggle(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1), departure_place="EARLY")
        _add_logbook_entry(app, uid, date=date(2024, 6, 1), departure_place="LATER")
        _login(app, client)
        resp = client.get("/pilot/logbook?order=asc")
        pos_early = resp.data.find(b"EARLY")
        pos_later = resp.data.find(b"LATER")
        assert pos_early < pos_later  # oldest appears first in HTML

    def test_logbook_totals_cover_all_entries_not_just_page(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        # Create 55 entries (more than one page of 50)
        for i in range(55):
            _add_logbook_entry(app, uid, single_pilot_se=1.0,
                               function_pic=1.0, single_pilot_me=None, multi_pilot=None)
        _login(app, client)
        resp = client.get("/pilot/logbook")
        # Total should be 55.0, not 50.0 (which would be a page-only sum)
        assert b"55" in resp.data


# ── New / edit / delete entry routes ─────────────────────────────────────────

class TestEntryRoutes:
    def test_new_entry_get(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/logbook/new")
        assert resp.status_code == 200
        assert b"New Logbook Entry" in resp.data

    def test_new_entry_saved(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        _post_entry(client)
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.aircraft_type == "C172S"
            assert float(entry.single_pilot_se) == 1.5

    def test_new_entry_date_required(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"date": ""})
        assert resp.status_code == 422
        assert b"required" in resp.data.lower()

    def test_new_entry_negative_time_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"single_pilot_se": "-1.0"})
        assert b"non-negative" in resp.data

    def test_new_entry_negative_landings_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"landings_day": "-1"})
        assert b"non-negative" in resp.data

    def test_edit_entry_get(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        resp = client.get(f"/pilot/logbook/{eid}/edit")
        assert resp.status_code == 200
        assert b"Edit Logbook Entry" in resp.data

    def test_edit_entry_saved(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        client.post(f"/pilot/logbook/{eid}/edit", data={
            "date": "2024-07-01",
            "aircraft_type": "PA44",
            "aircraft_registration": "OO-ABC",
            "departure_place": "EHRD",
            "arrival_place": "EBBR",
            "single_pilot_me": "1.2",
            "landings_day": "1",
            "function_pic": "1.2",
        }, follow_redirects=True)
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.aircraft_type == "PA44"
            assert float(entry.single_pilot_me) == 1.2

    def test_edit_entry_wrong_user_returns_404(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        eid = _add_logbook_entry(app, uid1)
        _login(app, client, email="b@x.com")
        resp = client.get(f"/pilot/logbook/{eid}/edit")
        assert resp.status_code == 404

    def test_delete_entry(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        client.post(f"/pilot/logbook/{eid}/delete", follow_redirects=True)
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, eid) is None

    def test_delete_entry_wrong_user_returns_404(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        eid = _add_logbook_entry(app, uid1)
        _login(app, client, email="b@x.com")
        resp = client.post(f"/pilot/logbook/{eid}/delete")
        assert resp.status_code == 404


# ── Profile routes ────────────────────────────────────────────────────────────

class TestProfileRoutes:
    def test_profile_get_creates_empty_profile(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/profile")
        assert resp.status_code == 200
        assert b"Pilot Profile" in resp.data

    def test_profile_save(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        client.post("/pilot/profile", data={
            "license_number": "BE.PPL.A.99999",
            "medical_expiry": "2027-06-01",
            "sep_expiry": "2026-09-30",
        }, follow_redirects=True)
        with app.app_context():
            p = PilotProfile.query.filter_by(user_id=uid).first()
            assert p.license_number == "BE.PPL.A.99999"
            assert p.medical_expiry == date(2027, 6, 1)
            assert p.sep_expiry == date(2026, 9, 30)

    def test_profile_invalid_date_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post("/pilot/profile", data={
            "medical_expiry": "not-a-date",
        }, follow_redirects=True)
        assert b"valid date" in resp.data

    def test_profile_invalid_sep_expiry_shows_error(self, app, client):
        # covers line 110: errors.append for sep_expiry parse failure
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post("/pilot/profile", data={
            "sep_expiry": "not-a-date",
        }, follow_redirects=True)
        assert b"valid date" in resp.data

    def test_profile_requires_login(self, app, client):
        resp = client.get("/pilot/profile")
        assert resp.status_code == 302


# ── Validation edge cases (parser branches) ───────────────────────────────────

class TestParserValidation:
    def test_valid_dep_arr_time_saved(self, app, client):
        # covers _parse_time happy path (lines 41-44)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        _post_entry(client, {"departure_time": "09:00", "arrival_time": "10:30"})
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry.departure_time is not None
            assert entry.departure_time.hour == 9
            assert entry.arrival_time.hour == 10

    def test_invalid_departure_time_shows_error(self, app, client):
        # covers _parse_time except path (lines 45-46) and line 272
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"departure_time": "notatime"})
        assert resp.status_code == 422
        assert b"valid HH:MM" in resp.data

    def test_invalid_arrival_time_shows_error(self, app, client):
        # covers line 275
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"arrival_time": "99:99"})
        assert resp.status_code == 422
        assert b"valid HH:MM" in resp.data

    def test_invalid_date_string_shows_error(self, app, client):
        # covers line 266: invalid (non-empty) date
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"date": "not-a-date"})
        assert resp.status_code == 422
        assert b"valid date" in resp.data

    def test_non_numeric_decimal_field_shows_error(self, app, client):
        # covers _parse_decimal except path (lines 60-61)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"single_pilot_se": "abc"})
        assert resp.status_code == 422
        assert b"must be a number" in resp.data

    def test_non_numeric_int_field_shows_error(self, app, client):
        # covers _parse_int except path (lines 73-74)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"landings_day": "abc"})
        assert resp.status_code == 422
        assert b"must be a whole number" in resp.data

    def test_edit_entry_validation_error(self, app, client):
        # covers lines 226-228: edit POST with validation error re-renders form
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        resp = client.post(f"/pilot/logbook/{eid}/edit", data={"date": "bad"})
        assert resp.status_code == 422
        assert b"valid date" in resp.data
