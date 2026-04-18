"""
Tests for Phase 5: Real Dashboard — stat cards, status badges, panel data.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, FlightEntry, MaintenanceTrigger, Role, Tenant,
    TenantUser, TriggerType, User, db,
)
from utils import compute_aircraft_statuses  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup(app, email="pilot@example.com"):
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


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(tenant_id=tenant_id, registration=registration,
                      make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id, hobbs_start=100.0, hobbs_end=101.5,
                flight_date=None):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=flight_date or date.today(),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            hobbs_start=hobbs_start,
            hobbs_end=hobbs_end,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _add_trigger(app, aircraft_id, trigger_type=TriggerType.CALENDAR,
                 due_date=None, due_hobbs=None, interval_hours=None):
    with app.app_context():
        t = MaintenanceTrigger(
            aircraft_id=aircraft_id,
            name="Test trigger",
            trigger_type=trigger_type,
            due_date=due_date,
            due_hobbs=due_hobbs,
            interval_hours=interval_hours,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


# ── Unit: compute_aircraft_statuses ──────────────────────────────────────────

class TestComputeAircraftStatuses:
    def test_no_triggers_returns_ok(self, app):
        with app.app_context():
            ac = Aircraft(id=1, tenant_id=1, registration="OO-X",
                          make="X", model="X")
            result = compute_aircraft_statuses([ac], [], {1: 100.0})
            assert result[1] == "ok"

    def test_all_ok_returns_ok(self, app):
        with app.app_context():
            ac = Aircraft(id=1, tenant_id=1, registration="OO-X",
                          make="X", model="X")
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=60),
            )
            result = compute_aircraft_statuses([ac], [t], {1: 100.0})
            assert result[1] == "ok"

    def test_one_due_soon_returns_due_soon(self, app):
        with app.app_context():
            ac = Aircraft(id=1, tenant_id=1, registration="OO-X",
                          make="X", model="X")
            t = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=10),
            )
            result = compute_aircraft_statuses([ac], [t], {1: 100.0})
            assert result[1] == "due_soon"

    def test_overdue_beats_due_soon(self, app):
        with app.app_context():
            ac = Aircraft(id=1, tenant_id=1, registration="OO-X",
                          make="X", model="X")
            t_overdue = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() - timedelta(days=1),
            )
            t_due_soon = MaintenanceTrigger(
                aircraft_id=1, name="y",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=10),
            )
            result = compute_aircraft_statuses([ac], [t_overdue, t_due_soon], {})
            assert result[1] == "overdue"

    def test_multiple_aircraft_independent(self, app):
        with app.app_context():
            ac1 = Aircraft(id=1, tenant_id=1, registration="OO-A",
                           make="X", model="X")
            ac2 = Aircraft(id=2, tenant_id=1, registration="OO-B",
                           make="X", model="X")
            t_overdue = MaintenanceTrigger(
                aircraft_id=1, name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() - timedelta(days=1),
            )
            t_ok = MaintenanceTrigger(
                aircraft_id=2, name="y",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=90),
            )
            result = compute_aircraft_statuses([ac1, ac2], [t_overdue, t_ok], {})
            assert result[1] == "overdue"
            assert result[2] == "ok"


# ── Dashboard route ───────────────────────────────────────────────────────────

class TestDashboardStats:
    def test_hours_this_month_counts_current_month_only(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        # Flight this month
        _add_flight(app, acid, hobbs_start=100.0, hobbs_end=102.0,
                    flight_date=date.today())
        # Flight last month — should NOT count
        last_month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        _add_flight(app, acid, hobbs_start=98.0, hobbs_end=100.0,
                    flight_date=last_month)
        _login(app, client)
        r = client.get("/")
        assert b"2.0" in r.data   # only this month's 2.0 h

    def test_flights_this_month_shows_count(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid, flight_date=date.today())
        _add_flight(app, acid, hobbs_start=102.0, hobbs_end=103.5,
                    flight_date=date.today())
        _login(app, client)
        r = client.get("/")
        assert b"Flights this month" in r.data

    def test_hours_this_month_zero_when_no_flights(self, app, client):
        uid, tid = _setup(app)
        _add_aircraft(app, tid)
        _login(app, client)
        r = client.get("/")
        assert b"0.0" in r.data

    def test_maintenance_alerts_count(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        # One overdue trigger
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() - timedelta(days=1))
        _login(app, client)
        r = client.get("/")
        assert b"Maintenance alerts" in r.data


class TestDashboardStatusBadges:
    def test_fleet_shows_ok_badge(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() + timedelta(days=90))
        _login(app, client)
        r = client.get("/")
        assert b"ac-status-ok" in r.data

    def test_fleet_shows_overdue_badge(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() - timedelta(days=1))
        _login(app, client)
        r = client.get("/")
        assert b"ac-status-overdue" in r.data

    def test_fleet_shows_due_soon_badge(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() + timedelta(days=10))
        _login(app, client)
        r = client.get("/")
        assert b"ac-status-warn" in r.data


class TestDashboardPanels:
    def test_recent_flights_panel_shows_data(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_flight(app, acid)
        _login(app, client)
        r = client.get("/")
        assert b"EBOS" in r.data
        assert b"EBBR" in r.data

    def test_urgent_maintenance_panel_shows_overdue(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() - timedelta(days=5))
        _login(app, client)
        r = client.get("/")
        assert b"Overdue" in r.data

    def test_urgent_maintenance_empty_when_all_ok(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() + timedelta(days=90))
        _login(app, client)
        r = client.get("/")
        assert b"No maintenance alerts" in r.data


# ── Aircraft list status badges ───────────────────────────────────────────────

class TestAircraftListStatusBadges:
    def test_list_shows_ok_badge(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() + timedelta(days=90))
        _login(app, client)
        r = client.get("/aircraft/")
        assert b"ac-status-ok" in r.data

    def test_list_shows_overdue_badge(self, app, client):
        uid, tid = _setup(app)
        acid = _add_aircraft(app, tid)
        _add_trigger(app, acid, trigger_type=TriggerType.CALENDAR,
                     due_date=date.today() - timedelta(days=1))
        _login(app, client)
        r = client.get("/aircraft/")
        assert b"ac-status-overdue" in r.data
