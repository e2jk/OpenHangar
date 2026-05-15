"""
Tests for Phase 13: Fleet Maintenance Overview page.
"""

import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    FlightEntry,
    MaintenanceTrigger,
    Role,
    Snag,
    Tenant,
    TenantUser,
    TriggerType,
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
            password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
            is_active=True,
        )
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


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_trigger(
    app,
    aircraft_id,
    name="Annual",
    trigger_type=TriggerType.CALENDAR,
    due_date=None,
    due_engine_hours=None,
    interval_hours=None,
    due_hobbs=None,
):
    # Support legacy kwarg
    if due_hobbs is not None:
        due_engine_hours = due_hobbs
    with app.app_context():
        t = MaintenanceTrigger(
            aircraft_id=aircraft_id,
            name=name,
            trigger_type=trigger_type,
            due_date=due_date or (date.today() + timedelta(days=60)),
            due_engine_hours=due_engine_hours,
            interval_hours=interval_hours,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


def _add_snag(app, aircraft_id, title="Door seal", is_grounding=False):
    with app.app_context():
        s = Snag(aircraft_id=aircraft_id, title=title, is_grounding=is_grounding)
        db.session.add(s)
        db.session.commit()
        return s.id


def _login_orphan(app, client):
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


# ── Access control ────────────────────────────────────────────────────────────


class TestFleetOverviewAccess:
    def test_redirects_when_not_logged_in(self, client):
        resp = client.get("/maintenance")
        assert resp.status_code == 302

    def test_403_when_no_tenant(self, app, client):
        _login_orphan(app, client)
        resp = client.get("/maintenance")
        assert resp.status_code == 403

    def test_renders_for_logged_in_user(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/maintenance")
        assert resp.status_code == 200
        assert b"Maintenance" in resp.data


# ── By-type view ──────────────────────────────────────────────────────────────


class TestByTypeView:
    def test_default_view_is_by_type(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance")
        assert b"By type" in resp.data
        assert b"Grounding Snags" in resp.data

    def test_shows_grounding_snag(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, title="Gear door unsafe", is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"Gear door unsafe" in resp.data
        assert b"Grounding" in resp.data

    def test_shows_open_snag(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, title="Cabin noise", is_grounding=False)
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"Cabin noise" in resp.data

    def test_shows_overdue_trigger(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app, ac_id, name="Oil change", due_date=date.today() - timedelta(days=5)
        )
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"Oil change" in resp.data
        assert b"Overdue" in resp.data

    def test_shows_due_soon_trigger(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app,
            ac_id,
            name="Transponder check",
            due_date=date.today() + timedelta(days=15),
        )
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"Transponder check" in resp.data
        assert b"Due soon" in resp.data

    def test_shows_ok_trigger(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app,
            ac_id,
            name="Annual inspection",
            due_date=date.today() + timedelta(days=200),
        )
        # Need an alert so the all-clear doesn't hide the table
        _add_trigger(
            app, ac_id, name="Oil change", due_date=date.today() - timedelta(days=1)
        )
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"Annual inspection" in resp.data
        assert b"OK" in resp.data

    def test_shows_hobbs_remaining_for_hours_trigger(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app,
            ac_id,
            name="50h oil",
            trigger_type=TriggerType.HOURS,
            due_date=None,
            due_hobbs=300.0,
            interval_hours=50.0,
        )
        # Need an alert to force the table to render
        _add_trigger(
            app, ac_id, name="Overdue item", due_date=date.today() - timedelta(days=1)
        )
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"50h oil" in resp.data

    def test_shows_all_clear_when_nothing_to_report(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"All clear" in resp.data

    def test_shows_aircraft_registration_as_link(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-TST")
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"OO-TST" in resp.data

    def test_only_shows_own_tenant_data(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac2 = _add_aircraft(app, t2, "OO-OTHER")
        _add_snag(app, ac2, title="Other tenant snag", is_grounding=True)
        _login(app, client, "a@example.com")
        resp = client.get("/maintenance?view=by-type")
        assert b"Other tenant snag" not in resp.data

    def test_action_links_present(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, is_grounding=True)
        _add_trigger(
            app, ac_id, name="Annual", due_date=date.today() - timedelta(days=1)
        )
        _login(app, client)
        resp = client.get("/maintenance?view=by-type")
        assert b"All snags" in resp.data
        assert b"All items" in resp.data


# ── Chronological view ────────────────────────────────────────────────────────


class TestChronologicalView:
    def test_chronological_view_renders(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert resp.status_code == 200
        assert b"Chronological" in resp.data

    def test_shows_grounding_snag_in_chron(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, title="Gear unsafe", is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"Gear unsafe" in resp.data
        assert b"Grounding" in resp.data

    def test_shows_open_snag_in_chron(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, title="Wind noise", is_grounding=False)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"Wind noise" in resp.data
        assert b"Open snag" in resp.data

    def test_shows_overdue_trigger_in_chron(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app, ac_id, name="Transponder", due_date=date.today() - timedelta(days=10)
        )
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"Transponder" in resp.data

    def test_ok_triggers_not_shown_in_chron(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_trigger(
            app,
            ac_id,
            name="Annual far away",
            due_date=date.today() + timedelta(days=300),
        )
        _add_snag(app, ac_id, title="Chron snag", is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"Annual far away" not in resp.data
        assert b"Chron snag" in resp.data

    def test_all_clear_chron_when_no_alerts(self, app, client):
        _, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"All clear" in resp.data

    def test_chron_view_link_back_to_by_type(self, app, client):
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"Switch to by-type view" in resp.data

    def test_chron_view_hours_based_overdue_trigger_included(self, app, client):
        """Hours-based overdue trigger uses _far_dt so it sorts after dated items."""
        _, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        # Give the aircraft 100 hobbs so the trigger with due_hobbs=50 is overdue
        with app.app_context():
            db.session.add(
                FlightEntry(
                    aircraft_id=ac_id,
                    date=date.today(),
                    departure_icao="EBOS",
                    arrival_icao="EBOS",
                    flight_time_counter_start=0.0,
                    flight_time_counter_end=100.0,
                    engine_time_counter_start=0.0,
                    engine_time_counter_end=100.0,
                )
            )
            db.session.commit()
        _add_trigger(
            app,
            ac_id,
            name="50h oil change overdue",
            trigger_type=TriggerType.HOURS,
            due_date=None,
            due_hobbs=50.0,
            interval_hours=50.0,
        )
        _login(app, client)
        resp = client.get("/maintenance?view=chronological")
        assert b"50h oil change overdue" in resp.data
