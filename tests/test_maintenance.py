"""
Tests for Phase 4: Maintenance tracking routes and status calculation.
"""

from datetime import date, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    ComponentType,
    Flight,
    HoursBasis,
    MaintenanceRecord,
    MaintenanceTrigger,
    Role,
    Tenant,
    TenantUser,
    TriggerType,
    User,
    db,
)


def _login_orphan_user(app, client):
    """Create a User with no TenantUser and inject into session."""
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
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
    return uid


def _add_aircraft(app, tenant_id, registration="OO-PNH"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_calendar_trigger(
    app, aircraft_id, name="Annual", due_date=None, interval_days=365
):
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


def _add_hours_trigger(
    app,
    aircraft_id,
    name="Oil change",
    due_engine_hours=200.0,
    interval_hours=50.0,
    due_hobbs=None,
):
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


def _add_flight(app, aircraft_id, flight_date, engine_time=2.0, counter_end=None):
    """counter_end also sets engine_time_counter_start/end (counter_end -
    engine_time) so Aircraft.total_engine_hours (max counter_end across
    flights) reflects this flight too — it doesn't look at engine_time."""
    with app.app_context():
        f = Flight(
            aircraft_id=aircraft_id,
            date=flight_date,
            departure_icao="EBOS",
            arrival_icao="EBBR",
            engine_time=engine_time,
            engine_time_counter_end=counter_end,
            engine_time_counter_start=(
                counter_end - engine_time if counter_end is not None else None
            ),
        )
        db.session.add(f)
        db.session.commit()
        return f.id


def _add_component(app, aircraft_id, comp_type=ComponentType.ENGINE, make="Lycoming"):
    with app.app_context():
        c = Component(aircraft_id=aircraft_id, type=comp_type, make=make, model="O-360")
        db.session.add(c)
        db.session.commit()
        return c.id


def _add_landings_trigger(
    app,
    aircraft_id,
    name="Landing gear check",
    due_landings=1000,
    interval_landings=200,
):
    with app.app_context():
        t = MaintenanceTrigger(
            aircraft_id=aircraft_id,
            name=name,
            trigger_type=TriggerType.LANDINGS,
            due_landings=due_landings,
            interval_landings=interval_landings,
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
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
                interval_hours=50.0,
            )
            assert t.status(current_engine_hours=190.0) == "ok"

    def test_hours_due_soon_within_warn_threshold(self, app):
        with app.app_context():
            # 10% of 50h = 5h; remaining 4h < 5h → due_soon
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
                interval_hours=50.0,
            )
            assert t.status(current_engine_hours=196.5) == "due_soon"

    def test_hours_overdue_when_past_due(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=201.0) == "overdue"

    def test_hours_ok_when_no_hobbs_provided(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=None) == "ok"

    def test_landings_ok_when_enough_remaining(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_landings=1000,
                interval_landings=200,
            )
            assert t.status(current_landings=900) == "ok"

    def test_landings_due_soon_within_warn_threshold(self, app):
        with app.app_context():
            # 10% of 200 = 20; remaining 15 < 20 → due_soon
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_landings=1000,
                interval_landings=200,
            )
            assert t.status(current_landings=985) == "due_soon"

    def test_landings_overdue_when_past_due(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_landings=1000,
            )
            assert t.status(current_landings=1001) == "overdue"

    def test_landings_ok_when_no_landings_provided(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_landings=1000,
            )
            assert t.status(current_landings=None) == "ok"

    def test_hours_uses_flight_hours_when_basis_is_flight(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
                hours_basis=HoursBasis.FLIGHT,
            )
            # Overdue on flight hours, plenty of engine hours remaining —
            # confirms the FLIGHT basis picks current_flight_hours, not
            # current_engine_hours.
            assert (
                t.status(current_engine_hours=50.0, current_flight_hours=201.0)
                == "overdue"
            )
            assert (
                t.status(current_engine_hours=201.0, current_flight_hours=50.0) == "ok"
            )

    def test_hours_explicit_warn_hours_overrides_interval_formula(self, app):
        with app.app_context():
            # interval_hours*0.1 would be 5h (not due_soon at 6h remaining),
            # but an explicit warn_hours=8 makes it due_soon instead.
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
                interval_hours=50.0,
                warn_hours=8.0,
            )
            assert t.status(current_engine_hours=194.0) == "due_soon"

    def test_landings_explicit_warn_landings_overrides_interval_formula(self, app):
        with app.app_context():
            # interval_landings*0.1 would be 20 (not due_soon at 30
            # remaining), but an explicit warn_landings=35 makes it due_soon.
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_landings=1000,
                interval_landings=200,
                warn_landings=35,
            )
            assert t.status(current_landings=970) == "due_soon"

    def test_calendar_explicit_warn_days_overrides_default(self, app):
        with app.app_context():
            # Default 30-day window would not flag this, but an explicit
            # warn_days=45 does.
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=40),
                warn_days=45,
            )
            assert t.status() == "due_soon"


class TestTriggerStatusCombined:
    """Phase 40: a trigger with more than one due-field group populated at
    once ("due at whichever comes first", e.g. an AMP task quoted as
    "100FH / 12MO") evaluates every populated group independently and
    returns the worst status."""

    def test_both_ok_is_ok(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=100),
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=100.0) == "ok"

    def test_calendar_overdue_wins_over_hours_ok(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() - timedelta(days=1),
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=10.0) == "overdue"

    def test_hours_overdue_wins_over_calendar_ok(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_date=date.today() + timedelta(days=100),
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=201.0) == "overdue"

    def test_calendar_due_soon_wins_over_hours_ok(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.CALENDAR,
                due_date=date.today() + timedelta(days=10),
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=10.0) == "due_soon"

    def test_hours_due_soon_wins_over_calendar_ok(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_date=date.today() + timedelta(days=100),
                due_engine_hours=200.0,
                interval_hours=50.0,
            )
            assert t.status(current_engine_hours=196.5) == "due_soon"

    def test_landings_and_calendar_combined_overdue(self, app):
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.LANDINGS,
                due_date=date.today() + timedelta(days=100),
                due_landings=1000,
            )
            assert t.status(current_landings=1001) == "overdue"

    def test_single_group_populated_matches_pre_combined_behaviour(self, app):
        """A trigger with only one field group populated (the common,
        non-combined case) behaves exactly as before — a regression guard
        for the status() rewrite."""
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=1,
                name="x",
                trigger_type=TriggerType.HOURS,
                due_engine_hours=200.0,
            )
            assert t.status(current_engine_hours=None) == "ok"


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
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_calendar_trigger(app, acid, name="Annual check")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"Annual check" in r.data

    def test_list_empty_state(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
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

    def test_list_groups_by_component(self, app, client):
        """Phase 40: a component-scoped trigger appears under its own
        component section; an unscoped trigger appears under 'Airframe /
        general'."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, comp_type=ComponentType.ENGINE)
        with app.app_context():
            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=acid,
                    component_id=cid,
                    name="Engine 100 hr inspection",
                    trigger_type=TriggerType.HOURS,
                    due_engine_hours=100.0,
                )
            )
            db.session.commit()
        _add_calendar_trigger(app, acid, name="Airframe annual")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"Airframe / general" in r.data
        assert b"Engine 100 hr inspection" in r.data
        assert b"Airframe annual" in r.data

    def test_list_shows_needs_review_badge(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=acid,
                    name="PENDING shop input",
                    trigger_type=TriggerType.CALENDAR,
                    needs_review=True,
                )
            )
            db.session.commit()
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert b"Needs review" in r.data

    def test_list_shows_category_action_reference_metadata(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=acid,
                    name="AD compliance",
                    trigger_type=TriggerType.CALENDAR,
                    due_date=date.today() + timedelta(days=200),
                    category="Maintenance due to repetitive ADs",
                    action="INSPECTION",
                    reference="AD 2023-0048",
                )
            )
            db.session.commit()
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert b"INSPECTION" in r.data
        assert b"AD 2023-0048" in r.data


# ── Projected due date (backlog: due-date projection from utilization trend) ──


class TestTriggerListProjectedDueDate:
    def test_shows_estimate_with_enough_flight_history(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_hours_trigger(app, acid, due_engine_hours=200.0)
        today = date.today()
        # 4 flights spanning 30 days, well within the 90-day window and past
        # the minimum-flights/minimum-span guard in due_date_projection.py.
        # Oldest first so counter_end increases forward in time, matching
        # Aircraft.total_engine_hours (the max counter_end across flights).
        counter = 100.0
        for days_ago in (30, 20, 10, 0):
            counter += 2.0
            _add_flight(
                app,
                acid,
                today - timedelta(days=days_ago),
                engine_time=2.0,
                counter_end=counter,
            )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"(est.)" in r.data

    def test_no_estimate_without_enough_flight_history(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_hours_trigger(app, acid, due_engine_hours=200.0)
        # Only one flight in the window — the "flown twice in 90 days
        # produces a meaningless trend" guard must suppress the estimate.
        _add_flight(app, acid, date.today())
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"(est.)" not in r.data

    def test_no_estimate_for_calendar_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance")
        assert r.status_code == 200
        assert b"(est.)" not in r.data


# ── Add trigger ───────────────────────────────────────────────────────────────


class TestAddTrigger:
    def test_get_shows_form(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/new")
        assert r.status_code == 200
        assert b"Add Maintenance Item" in r.data

    def test_post_creates_calendar_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "interval_days": "365",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t is not None
            assert t.name == "Annual"
            assert t.trigger_type == "calendar"
            assert t.interval_days == 365

    def test_post_creates_hours_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "250.0",
                "interval_hours": "50",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert float(t.due_engine_hours) == 250.0
            assert float(t.interval_hours) == 50.0

    def test_post_creates_trigger_scoped_to_component(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Engine 100 hr inspection",
                "trigger_type": "hours",
                "due_engine_hours": "100",
                "component_id": str(cid),
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.component_id == cid

    def test_post_no_component_selected_is_unscoped(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "component_id": "",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.component_id is None

    def test_post_rejects_component_from_another_aircraft(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        other_acid = _add_aircraft(app, tid, registration="OO-OTH")
        other_cid = _add_component(app, other_acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "component_id": str(other_cid),
            },
        )
        assert r.status_code == 200
        assert b"Component selection is invalid" in r.data

    def test_post_rejects_missing_name(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
            },
        )
        assert r.status_code == 200
        assert b"Name is required" in r.data

    def test_post_rejects_calendar_without_due_date(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "",
            },
        )
        assert r.status_code == 200
        assert b"Due date is required" in r.data

    def test_post_rejects_hours_without_due_hobbs(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "",
            },
        )
        assert r.status_code == 200
        assert b"Due engine hours is required" in r.data

    def test_post_creates_landings_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "1000",
                "interval_landings": "200",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.due_landings == 1000
            assert t.interval_landings == 200

    def test_post_rejects_landings_without_due_landings(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "",
            },
        )
        assert r.status_code == 200
        assert b"Due landings is required" in r.data


# ── Edit trigger ──────────────────────────────────────────────────────────────


class TestEditTrigger:
    def test_get_prefills_form(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, name="Annual", due_date=date(2027, 1, 1)
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/{trid}/edit")
        assert r.status_code == 200
        assert b"Annual" in r.data

    def test_post_updates_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, name="Annual")
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/edit",
            data={
                "name": "Annual (updated)",
                "trigger_type": "calendar",
                "due_date": "2028-06-01",
                "interval_days": "365",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.name == "Annual (updated)"
            assert t.due_date == date(2028, 6, 1)

    def test_edit_404_for_wrong_aircraft(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        trid = _add_calendar_trigger(app, acid1)
        _login(app, client)
        r = client.get(f"/aircraft/{acid2}/maintenance/{trid}/edit")
        assert r.status_code == 404


# ── Delete trigger ────────────────────────────────────────────────────────────


class TestDeleteTrigger:
    def test_delete_removes_trigger(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/delete", follow_redirects=False
        )
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(MaintenanceTrigger, trid) is None

    def test_delete_404_for_wrong_aircraft(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        trid = _add_calendar_trigger(app, acid1)
        _login(app, client)
        r = client.post(f"/aircraft/{acid2}/maintenance/{trid}/delete")
        assert r.status_code == 404

    def test_deleting_component_unscopes_trigger_instead_of_deleting_it(
        self, app, client
    ):
        """Phase 40: Component.id FK is ondelete=SET NULL — removing a
        component keeps the trigger's history, just unscopes it."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid)
        trid = _add_calendar_trigger(app, acid)
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            t.component_id = cid
            db.session.commit()
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/components/{cid}/delete")
        assert r.status_code in (302, 303)
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t is not None
            assert t.component_id is None


# ── Service trigger ───────────────────────────────────────────────────────────


class TestServiceTrigger:
    def test_get_shows_service_form(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid, name="Annual")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/{trid}/service")
        assert r.status_code == 200
        assert b"Mark as serviced" in r.data

    def test_post_creates_record(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "",
                "notes": "Done at workshop",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.performed_at == date(2026, 4, 1)
            assert rec.notes == "Done at workshop"

    def test_calendar_trigger_advances_due_date(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, due_date=date(2026, 1, 1), interval_days=365
        )
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "",
            },
        )
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.due_date == date(2027, 4, 1)

    def test_hours_trigger_advances_due_hobbs(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid, due_hobbs=200.0, interval_hours=50.0)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "198.5",
            },
        )
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert float(t.due_engine_hours) == 248.5

    def test_hours_trigger_requires_hobbs(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "",
            },
        )
        assert r.status_code == 200
        assert b"Hobbs at service is required" in r.data

    def test_landings_trigger_advances_due_landings(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_landings_trigger(
            app, acid, due_landings=1000, interval_landings=200
        )
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "landings_at_service": "985",
            },
        )
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.due_landings == 1185

    def test_landings_trigger_requires_landings(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_landings_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "landings_at_service": "",
            },
        )
        assert r.status_code == 200
        assert b"Landings at service is required" in r.data

    def test_service_requires_date(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "",
            },
        )
        assert r.status_code == 200
        assert b"Service date is required" in r.data

    def test_combined_trigger_advances_both_groups_from_one_service(self, app, client):
        """Phase 40: a combined-interval trigger (both due_date and
        due_engine_hours populated) advances both from a single service
        record, using the readings both fields collect."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=acid,
                name="100FH / 12MO inspection",
                trigger_type=TriggerType.CALENDAR,
                due_date=date(2026, 1, 1),
                interval_days=365,
                due_engine_hours=200.0,
                interval_hours=100.0,
            )
            db.session.add(t)
            db.session.commit()
            trid = t.id
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "150.0",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = db.session.get(MaintenanceTrigger, trid)
            assert t.due_date == date(2027, 4, 1)
            assert float(t.due_engine_hours) == 250.0

    def test_combined_trigger_service_form_shows_both_fields_required(
        self, app, client
    ):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            t = MaintenanceTrigger(
                aircraft_id=acid,
                name="combined",
                trigger_type=TriggerType.HOURS,
                due_date=date(2026, 1, 1),
                due_engine_hours=200.0,
                due_landings=1000,
            )
            db.session.add(t)
            db.session.commit()
            trid = t.id
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={"performed_at": "2026-04-01"},
        )
        assert r.status_code == 200
        assert b"Hobbs at service is required" in r.data
        assert b"Landings at service is required" in r.data


# ── Coverage gap: no TenantUser → 403 ────────────────────────────────────────


class TestMaintenanceNoTenantUser:
    def test_aborts_403_when_no_tenant_user(self, app, client):
        # _get_aircraft_or_404 only calls _tenant_id() when the aircraft exists;
        # create one under a separate tenant so the 404 short-circuit is not hit.
        with app.app_context():
            tenant = Tenant(name="Other Hangar")
            db.session.add(tenant)
            db.session.flush()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-TST", make="X", model="X"
            )
            db.session.add(ac)
            db.session.commit()
            acid = ac.id
        _login_orphan_user(app, client)
        response = client.get(f"/aircraft/{acid}/maintenance")
        assert response.status_code == 403


# ── Coverage gap: _save_trigger validation ────────────────────────────────────


class TestSaveTriggerValidation:
    def test_invalid_trigger_type_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Test",
                "trigger_type": "invalid",
            },
        )
        assert r.status_code == 200
        assert b"calendar" in r.data or b"Trigger type" in r.data

    def test_calendar_bad_due_date_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "not-a-date",
            },
        )
        assert r.status_code == 200
        assert b"valid date" in r.data

    def test_calendar_invalid_interval_days_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "interval_days": "0",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_hours_negative_due_hobbs_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "-5",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_hours_invalid_interval_hours_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "200.0",
                "interval_hours": "0",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_landings_negative_due_landings_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "-5",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_landings_invalid_interval_landings_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "1000",
                "interval_landings": "0",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_calendar_invalid_warn_days_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "warn_days": "not-a-number",
            },
        )
        assert r.status_code == 200
        assert b"Warning lead time" in r.data

    def test_calendar_warn_days_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Annual",
                "trigger_type": "calendar",
                "due_date": "2027-01-01",
                "warn_days": "45",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.warn_days == 45

    def test_hours_invalid_warn_hours_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "200.0",
                "warn_hours": "not-a-number",
            },
        )
        assert r.status_code == 200
        assert b"Warning lead time" in r.data

    def test_hours_warn_hours_and_basis_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Airframe life limit",
                "trigger_type": "hours",
                "due_engine_hours": "3000",
                "warn_hours": "8.0",
                "hours_basis": "flight",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert float(t.warn_hours) == 8.0
            assert t.hours_basis == "flight"

    def test_hours_basis_defaults_to_engine_when_missing(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Oil change",
                "trigger_type": "hours",
                "due_engine_hours": "200.0",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.hours_basis == "engine"

    def test_landings_invalid_warn_landings_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "1000",
                "warn_landings": "not-a-number",
            },
        )
        assert r.status_code == 200
        assert b"Warning lead time" in r.data

    def test_landings_warn_landings_saved(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/new",
            data={
                "name": "Landing gear check",
                "trigger_type": "landings",
                "due_landings": "1000",
                "warn_landings": "35",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            t = MaintenanceTrigger.query.filter_by(aircraft_id=acid).first()
            assert t.warn_landings == 35


# ── Coverage gap: service_trigger validation ──────────────────────────────────


class TestServiceTriggerValidation:
    def test_bad_service_date_format_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "not-a-date",
                "hobbs_at_service": "",
            },
        )
        assert r.status_code == 200
        assert b"valid date" in r.data

    def test_hours_trigger_negative_hobbs_at_service_shows_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_hours_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "-5",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_calendar_trigger_accepts_optional_hobbs(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, interval_days=365, due_date=date(2026, 1, 1)
        )
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "198.5",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert float(rec.hobbs_at_service) == 198.5

    def test_calendar_trigger_ignores_non_numeric_hobbs(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, interval_days=365, due_date=date(2026, 1, 1)
        )
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "hobbs_at_service": "not-a-number",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.hobbs_at_service is None

    def test_landings_trigger_negative_landings_at_service_shows_error(
        self, app, client
    ):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_landings_trigger(app, acid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "landings_at_service": "-5",
            },
        )
        assert r.status_code == 200
        assert b"positive" in r.data

    def test_calendar_trigger_accepts_optional_landings(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, interval_days=365, due_date=date(2026, 1, 1)
        )
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "landings_at_service": "42",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.landings_at_service == 42

    def test_calendar_trigger_ignores_non_numeric_landings(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        trid = _add_calendar_trigger(
            app, acid, interval_days=365, due_date=date(2026, 1, 1)
        )
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/{trid}/service",
            data={
                "performed_at": "2026-04-01",
                "landings_at_service": "not-a-number",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            rec = MaintenanceRecord.query.filter_by(trigger_id=trid).first()
            assert rec is not None
            assert rec.landings_at_service is None
