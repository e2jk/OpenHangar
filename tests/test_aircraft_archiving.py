"""
Tests for archiving/retiring an aircraft without deleting its history:
  - archive/unarchive routes (role-gated, idempotent)
  - archived aircraft hidden from the active fleet list, behind a toggle
  - detail page and fleet logbook history remain readable
  - excluded from the new-flight dropdown/POST, reservations, and the
    daily notification passes
"""

from datetime import date, timedelta
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    FlightEntry,
    MaintenanceTrigger,
    NotificationType,
    Role,
    Tenant,
    TenantUser,
    TriggerType,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="owner@example.com", role=Role.OWNER):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
            is_pilot=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-ARC", archived=False):
    from datetime import datetime, timezone

    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            archived_at=datetime.now(timezone.utc) if archived else None,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 6, 1),
            departure_icao="EBOS",
            arrival_icao="EBBR",
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


class TestArchiveRoutes:
    def test_archive_sets_timestamp(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/archive", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Aircraft, acid).archived_at is not None

    def test_archive_idempotent(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, archived=True)
        _login(app, client)
        with app.app_context():
            before = db.session.get(Aircraft, acid).archived_at
        resp = client.post(f"/aircraft/{acid}/archive", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Aircraft, acid).archived_at == before

    def test_unarchive_clears_timestamp(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, archived=True)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/unarchive", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Aircraft, acid).archived_at is None

    def test_unarchive_noop_when_active(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(f"/aircraft/{acid}/unarchive", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Aircraft, acid).archived_at is None

    def test_archive_forbidden_for_non_owner(self, app, client):
        _uid, tid = _create_user_and_tenant(
            app, email="pilot@example.com", role=Role.PILOT
        )
        acid = _add_aircraft(app, tid)
        _login(app, client, email="pilot@example.com")
        resp = client.post(f"/aircraft/{acid}/archive")
        assert resp.status_code == 403


class TestArchivedVisibility:
    def test_hidden_from_fleet_list_by_default(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-ACT")
        _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        _login(app, client)
        resp = client.get("/aircraft/?list=1")
        assert b"OO-ACT" in resp.data
        assert b"OO-OLD" not in resp.data
        # The toggle advertises the archived aircraft
        assert b"archived=1" in resp.data

    def test_shown_with_archived_toggle(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-ACT")
        _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        _login(app, client)
        resp = client.get("/aircraft/?list=1&archived=1")
        assert b"OO-ACT" in resp.data
        assert b"OO-OLD" in resp.data
        assert b"Archived" in resp.data

    def test_detail_page_remains_readable(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, archived=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}")
        assert resp.status_code == 200
        assert b"ARCHIVED" in resp.data
        assert b"/unarchive" in resp.data

    def test_fleet_logbook_keeps_archived_history(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        _add_flight(app, acid)
        _login(app, client)
        resp = client.get("/flights")
        assert resp.status_code == 200
        assert b"OO-OLD" in resp.data

    def test_hidden_from_dashboard(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        _login(app, client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"OO-OLD" not in resp.data

    def test_excluded_from_new_flight_form_and_post(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        _login(app, client)
        resp = client.get("/flights/new")
        assert b"OO-OLD" not in resp.data
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(acid),
                "date": "2024-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "crew_name_0": "Test Pilot",
                "crew_role_0": "PIC",
            },
        )
        assert resp.status_code in (200, 400)
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=acid).count() == 0

    def test_flights_of_archived_aircraft_remain_editable(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        fid = _add_flight(app, acid)
        _login(app, client)
        resp = client.get(f"/flights/{fid}/edit")
        assert resp.status_code == 200

    def test_new_reservation_rejected_for_archived(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, archived=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{acid}/reservations/new")
        assert resp.status_code == 404


class TestArchivedNotificationExclusion:
    def test_maintenance_pass_skips_archived(self, app):
        _uid, tid = _create_user_and_tenant(app, email="owner@arch-notif.com")
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        with app.app_context():
            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=acid,
                    name="Annual inspection",
                    trigger_type=TriggerType.CALENDAR,
                    due_date=date.today() - timedelta(days=5),
                )
            )
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_maintenance

                _check_maintenance(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.MAINTENANCE_OVERDUE not in types_dispatched

    def test_insurance_pass_skips_archived(self, app):
        _uid, tid = _create_user_and_tenant(app, email="owner@arch-ins.com")
        acid = _add_aircraft(app, tid, registration="OO-OLD", archived=True)
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.insurance_expiry = date.today() + timedelta(days=10)
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_insurance

                _check_insurance(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.INSURANCE_EXPIRING not in types_dispatched
