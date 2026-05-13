"""
Tests for Phase 4: Maintenance tracking routes and status calculation.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, MaintenanceRecord, MaintenanceTrigger, Role, Tenant,
    TenantUser, TriggerType, User, db,
)


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

def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"testpassword123", bcrypt.gensalt()).decode(),
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


def _add_calendar_trigger(app, aircraft_id, name="Annual", due_date=None,
                           interval_days=365):
    with app.app_context():
        t = MaintenanceTrigger(
            aircraft_id=aircraft_id,
            name=name,
            trigger_type=TriggerType.CALENDAR,
            due_date=due_date or (date.today() + timedelta(days=60)),
            interval_days=interval_days,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


def _add_hours_trigger(app, aircraft_id, name="Oil change",
                       due_engine_hours=200.0, interval_hours=50.0,
                       due_hobbs=None):
    # Support legacy kwarg
    if due_hobbs is not None:
        due_engine_hours = due_hobbs
    with app.app_context():
        t = MaintenanceTrigger(
            aircraft_id=aircraft_id,
            name=name,
            trigger_type=TriggerType.HOURS,
            due_engine_hours=due_engine_hours,
            interval_hours=interval_hours,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


# ── Model: status() ───────────────────────────────────────────────────────────

class TestTriggerStatus:
    def test_calendar_ok_when_more_than_30_days(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=31),
            )
            assert t.status() == "ok"

    def test_calendar_due_soon_within_30_days(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=15),
            )
            assert t.status() == "due_soon"

    def test_calendar_overdue_when_past(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() - timedelta(days=1),
            )
            assert t.status() == "overdue"

    def test_hours_ok_when_enough_remaining(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0, interval_hours=50.0,
            )
            assert t.status(current_hobbs=190.0) == "ok"

    def test_hours_due_soon_within_warn_threshold(self, app):
        with app.app_context():
            # 10% of 50h = 5h; remaining 4h < 5h → due_soon
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0, interval_hours=50.0,
            )
            assert t.status(current_hobbs=196.5) == "due_soon"

    def test_hours_overdue_when_past_due(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
            )
            assert t.status(current_hobbs=201.0) == "overdue"

    def test_hours_ok_when_no_hobbs_provided(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
            )
            assert t.status(current_hobbs=None) == "ok"


# ── Auth guard ────────────────────────────────────────────────────────────────

class TestAuthGuard:
    def test_list_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_new_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance/new")
        assert r.status_code == 302

    def test_edit_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance/1/edit")
        assert r.status_code == 302

    def test_delete_redirects_when_not_logged_in(self, client):
        r = client.post("/aircraft/1/maintenance/1/delete")
        assert r.status_code == 302

    def test_service_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance/1/service")
        assert r.status_code == 302


# ── Trigger list ──────────────────────────────────────────────────────────────

class TestTriggerList:
    def test_list_shows_triggers(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_calendar_trigger(app, acid, name="Annual check")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"Annual check" in r.data

    def test_list_empty_state(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"No maintenance items" in r.data

    def test_list_404_for_other_tenant(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.get(f"/aircraft/{other_acid}/maintenance")
        assert r.status_code == 404


# ── Add trigger ───────────────────────────────────────────────────────────────

class TestAddTrigger:
    def test_get_shows_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/new")
        assert r.status_code == 200
        assert b"Add Maintenance Item" in r.data

    def test_post_creates_calendar_trigger(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Annual",
            "trigger_type": "calendar",
            "due_date": "2027-01-01",
            "interval_days": "365",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t is not None
            assert t.name == "Annual"
            assert t.trigger_type == "calendar"
            assert t.interval_days == 365

    def test_post_creates_hours_trigger(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": "250.0",
            "interval_hours": "50",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert float(t.due_engine_hours) == 250.0
            assert float(t.interval_hours) == 50.0

    def test_post_rejects_missing_name(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "",
            "trigger_type": "calendar",
            "due_date": "2027-01-01",
        })
        assert r.status_code == 200
        assert b"Name is required" in r.data

    def test_post_rejects_calendar_without_due_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Annual",
            "trigger_type": "calendar",
            "due_date": "",
        })
        assert r.status_code == 200
        assert b"Due date is required" in r.data

    def test_post_rejects_hours_without_due_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": "",
        })
        assert r.status_code == 200
        assert b"Due engine hours is required" in r.data


# ── Edit trigger ──────────────────────────────────────────────────────────────

class TestEditTrigger:
    def test_get_prefills_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, name="Annual",
                                     due_date=date(2027, 1, 1))
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/{trid}/edit")
        assert r.status_code == 200
        assert b"Annual" in r.data

    def test_post_updates_trigger(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, name="Annual")
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/edit", data={
            "name": "Annual (updated)",
            "trigger_type": "calendar",
            "due_date": "2028-06-01",
            "interval_days": "365",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.name == "Annual (updated)"
            assert t.due_date == date(2028, 6, 1)

    def test_edit_404_for_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        trid = _add_calendar_trigger(app, acid1)
        _login(app, client)
        r = client.get(f"/aircraft/{acid2}/maintenance/{trid}/edit")
        assert r.status_code == 404


# ── Delete trigger ────────────────────────────────────────────────────────────

class TestDeleteTrigger:
    def test_delete_removes_trigger(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/delete",
                        follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(MaintenanceTrigger, trid) is None

    def test_delete_404_for_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        trid = _add_calendar_trigger(app, acid1)
        _login(app, client)
        r = client.post(f"/aircraft/{acid2}/maintenance/{trid}/delete")
        assert r.status_code == 404


# ── Service trigger ───────────────────────────────────────────────────────────

class TestServiceTrigger:
    def test_get_shows_service_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, name="Annual")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/{trid}/service")
        assert r.status_code == 200
        assert b"Mark as Serviced" in r.data

    def test_post_creates_record(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "",
            "notes": "Done at workshop",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.performed_at == date(2026, 4, 1)
            assert rec.notes == "Done at workshop"

    def test_calendar_trigger_advances_due_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid,
                                     due_date=date(2026, 1, 1), interval_days=365)
        _login(app, client)
        client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "",
        })
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.due_date == date(2027, 4, 1)

    def test_hours_trigger_advances_due_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid, due_hobbs=200.0, interval_hours=50.0)
        _login(app, client)
        client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "198.5",
        })
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert float(t.due_engine_hours) == 248.5

    def test_hours_trigger_requires_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "",
        })
        assert r.status_code == 200
        assert b"Hobbs at service is required" in r.data

    def test_service_requires_date(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "",
        })
        assert r.status_code == 200
        assert b"Service date is required" in r.data


# ── Coverage gap: no TenantUser → 403 ────────────────────────────────────────

class TestMaintenanceNoTenantUser:
    def test_aborts_403_when_no_tenant_user(self, app, client):
        # _get_aircraft_or_404 only calls _tenant_id() when the aircraft exists;
        # create one under a separate tenant so the 404 short-circuit is not hit.
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
        response = client.get(f"/aircraft/{acid}/maintenance")
        assert response.status_code == 403


# ── Coverage gap: _save_trigger validation ────────────────────────────────────

class TestSaveTriggerValidation:
    def test_invalid_trigger_type_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Test",
            "trigger_type": "invalid",
        })
        assert r.status_code == 200
        assert b"calendar" in r.data or b"Trigger type" in r.data

    def test_calendar_bad_due_date_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Annual",
            "trigger_type": "calendar",
            "due_date": "not-a-date",
        })
        assert r.status_code == 200
        assert b"valid date" in r.data

    def test_calendar_invalid_interval_days_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Annual",
            "trigger_type": "calendar",
            "due_date": "2027-01-01",
            "interval_days": "0",
        })
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_hours_negative_due_hobbs_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": "-5",
        })
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_hours_invalid_interval_hours_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/new", data={
            "name": "Oil change",
            "trigger_type": "hours",
            "due_engine_hours": "200.0",
            "interval_hours": "0",
        })
        assert r.status_code == 200
        assert b"positive" in r.data


# ── Coverage gap: service_trigger validation ──────────────────────────────────

class TestServiceTriggerValidation:
    def test_bad_service_date_format_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "not-a-date",
            "hobbs_at_service": "",
        })
        assert r.status_code == 200
        assert b"valid date" in r.data

    def test_hours_trigger_negative_hobbs_at_service_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "-5",
        })
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_calendar_trigger_accepts_optional_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, interval_days=365,
                                     due_date=date(2026, 1, 1))
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "198.5",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert float(rec.hobbs_at_service) == 198.5

    def test_calendar_trigger_ignores_non_numeric_hobbs(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, interval_days=365,
                                     due_date=date(2026, 1, 1))
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/{trid}/service", data={
            "performed_at": "2026-04-01",
            "hobbs_at_service": "not-a-number",
        }, follow_redirects=False)
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.hobbs_at_service is None
