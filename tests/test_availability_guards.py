"""
Tests for Phase 37f: availability guards — grounded-reservation policy,
maintenance downtimes, and the RESERVATION_AIRCRAFT_GROUNDED notification.
See docs/phase37_rental_spec.md § 37f.
"""

from datetime import datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    MaintenanceDowntime,
    Reservation,
    ReservationStatus,
    Role,
    Snag,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    UserAircraftAccess,
    db,
)


def _make_user(app, email, role=Role.PILOT):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _make_aircraft(app, tenant_id, reg="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=reg, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _grant_access(app, user_id, aircraft_id):
    with app.app_context():
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=aircraft_id))
        db.session.commit()


def _login(app, client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _set_grounded_policy(app, tenant_id, policy):
    with app.app_context():
        profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
        if profile is None:
            profile = TenantProfile(tenant_id=tenant_id, setup_complete=True)
            db.session.add(profile)
        profile.grounded_reservation_policy = policy
        db.session.commit()


def _open_grounding_snag(app, aircraft_id):
    with app.app_context():
        s = Snag(aircraft_id=aircraft_id, title="Flat tyre", is_grounding=True)
        db.session.add(s)
        db.session.commit()
        return s.id


def _make_reservation(
    app, aircraft_id, pilot_user_id, status=ReservationStatus.PENDING, start_offset=1
):
    with app.app_context():
        now = datetime.now(timezone.utc)
        r = Reservation(
            aircraft_id=aircraft_id,
            pilot_user_id=pilot_user_id,
            start_dt=now + timedelta(hours=start_offset),
            end_dt=now + timedelta(hours=start_offset + 2),
            status=status,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


# ── Grounded reservation policy — create ────────────────────────────────────────


class TestGroundedPolicyCreate:
    def test_warn_allows_creation_by_renter(self, app, client):
        uid, tid = _make_user(app, "gc_warn@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC1")
        _grant_access(app, uid, acid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "warn")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=acid).count() == 1

    def test_block_refuses_creation_by_renter(self, app, client):
        uid, tid = _make_user(app, "gc_block@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC2")
        _grant_access(app, uid, acid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-20T11:00"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=acid).count() == 0

    def test_block_still_allows_owner(self, app, client):
        uid, tid = _make_user(app, "gc_owner@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GC3")
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=acid).count() == 1

    def test_not_grounded_no_guard_triggered(self, app, client):
        uid, tid = _make_user(app, "gc_notgrounded@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC4")
        _grant_access(app, uid, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=acid).count() == 1

    def test_new_reservation_form_shows_grounded_warning(self, app, client):
        uid, tid = _make_user(app, "gc_form@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC5")
        _grant_access(app, uid, acid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "warn")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/new")
        assert r.status_code == 200
        assert b"open grounding snag" in r.data

    def test_new_reservation_form_shows_blocked_banner(self, app, client):
        uid, tid = _make_user(app, "gc_form2@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC6")
        _grant_access(app, uid, acid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/new")
        assert r.status_code == 200
        assert b"requires bookings to be blocked" in r.data

    def test_edit_reservation_form_renders_with_grounded_aircraft(self, app, client):
        uid, tid = _make_user(app, "gc_edit@ex.com")
        acid = _make_aircraft(app, tid, "OO-GC7")
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _open_grounding_snag(app, acid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/edit")
        assert r.status_code == 200


# ── Grounded reservation policy — confirm ────────────────────────────────────────


class TestGroundedPolicyConfirm:
    def test_block_refuses_confirm_for_renter(self, app, client):
        uid, tid = _make_user(app, "gconf_block@ex.com")
        owner_uid, _ = _make_user(app, "gconf_owner1@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid, "OO-GCF1")
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, owner_uid)
        r = client.post(f"/aircraft/{acid}/reservations/{res_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.PENDING

    def test_warn_allows_confirm(self, app, client):
        uid, tid = _make_user(app, "gconf_warn@ex.com")
        owner_uid, _ = _make_user(app, "gconf_owner2@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid, "OO-GCF2")
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "warn")
        _login(app, client, owner_uid)
        r = client.post(f"/aircraft/{acid}/reservations/{res_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CONFIRMED

    def test_block_still_allows_confirm_for_owner_pilot(self, app, client):
        owner_uid, tid = _make_user(app, "gconf_owner3@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GCF3")
        res_id = _make_reservation(app, acid, owner_uid)
        _open_grounding_snag(app, acid)
        _set_grounded_policy(app, tid, "block")
        _login(app, client, owner_uid)
        r = client.post(f"/aircraft/{acid}/reservations/{res_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CONFIRMED


# ── Maintenance downtime CRUD ─────────────────────────────────────────────────────


class TestDowntimeCrud:
    def test_owner_get_new_downtime_form(self, app, client):
        uid, tid = _make_user(app, "dt_owner0@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT0")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/downtimes/new")
        assert r.status_code == 200

    def test_owner_can_create_downtime(self, app, client):
        uid, tid = _make_user(app, "dt_owner1@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT1")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={
                "start_dt": "2026-08-20T09:00",
                "end_dt": "2026-08-22T17:00",
                "reason": "Annual inspection",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            d = MaintenanceDowntime.query.filter_by(aircraft_id=acid).one()
            assert d.reason == "Annual inspection"

    def test_maintenance_role_can_create_downtime(self, app, client):
        uid, tid = _make_user(app, "dt_maint1@ex.com", role=Role.MAINTENANCE)
        acid = _make_aircraft(app, tid, "OO-DT2")
        _grant_access(app, uid, acid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-22T17:00"},
        )
        assert r.status_code == 302

    def test_pilot_cannot_create_downtime(self, app, client):
        uid, tid = _make_user(app, "dt_pilot1@ex.com", role=Role.PILOT)
        acid = _make_aircraft(app, tid, "OO-DT3")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-22T17:00"},
        )
        assert r.status_code == 403

    def test_missing_fields_rejected(self, app, client):
        uid, tid = _make_user(app, "dt_owner2@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT4")
        _login(app, client, uid)
        r = client.post(f"/aircraft/{acid}/downtimes/new", data={})
        assert r.status_code == 200
        with app.app_context():
            assert MaintenanceDowntime.query.filter_by(aircraft_id=acid).count() == 0

    def test_end_before_start_rejected(self, app, client):
        uid, tid = _make_user(app, "dt_owner3@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT5")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-22T09:00", "end_dt": "2026-08-20T17:00"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert MaintenanceDowntime.query.filter_by(aircraft_id=acid).count() == 0

    def test_edit_downtime(self, app, client):
        uid, tid = _make_user(app, "dt_owner4@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT6")
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-22T17:00"},
        )
        with app.app_context():
            did = MaintenanceDowntime.query.filter_by(aircraft_id=acid).one().id
        r = client.get(f"/aircraft/{acid}/downtimes/{did}/edit")
        assert r.status_code == 200
        r = client.post(
            f"/aircraft/{acid}/downtimes/{did}/edit",
            data={
                "start_dt": "2026-08-21T09:00",
                "end_dt": "2026-08-23T17:00",
                "reason": "Extended",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            d = db.session.get(MaintenanceDowntime, did)
            assert d.reason == "Extended"

    def test_delete_downtime(self, app, client):
        uid, tid = _make_user(app, "dt_owner5@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DT7")
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-22T17:00"},
        )
        with app.app_context():
            did = MaintenanceDowntime.query.filter_by(aircraft_id=acid).one().id
        r = client.post(f"/aircraft/{acid}/downtimes/{did}/delete")
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(MaintenanceDowntime, did) is None

    def test_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "dt_owner6@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "dt_owner7@ex.com", role=Role.OWNER)
        other_acid = _make_aircraft(app, other_tid, "OO-DT8")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{other_acid}/downtimes/new")
        assert r.status_code == 404

    def test_downtime_edit_404_for_wrong_aircraft(self, app, client):
        uid, tid = _make_user(app, "dt_owner8@ex.com", role=Role.OWNER)
        acid1 = _make_aircraft(app, tid, "OO-DT9")
        acid2 = _make_aircraft(app, tid, "OO-DT10")
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid1}/downtimes/new",
            data={"start_dt": "2026-08-20T09:00", "end_dt": "2026-08-22T17:00"},
        )
        with app.app_context():
            did = MaintenanceDowntime.query.filter_by(aircraft_id=acid1).one().id
        r = client.get(f"/aircraft/{acid2}/downtimes/{did}/edit")
        assert r.status_code == 404


# ── Downtime conflict detection ───────────────────────────────────────────────────


class TestDowntimeConflicts:
    def test_confirming_reservation_over_downtime_rejected(self, app, client):
        owner_uid, tid = _make_user(app, "dtc_owner1@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DTC1")
        with app.app_context():
            db.session.add(
                MaintenanceDowntime(
                    aircraft_id=acid,
                    start_dt=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
                    end_dt=datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc),
                    reason="Shop visit",
                )
            )
            db.session.commit()
        pilot_uid, _ = _make_user(app, "dtc_pilot1@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            r = Reservation(
                aircraft_id=acid,
                pilot_user_id=pilot_uid,
                start_dt=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                end_dt=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
                status=ReservationStatus.PENDING,
            )
            db.session.add(r)
            db.session.commit()
            res_id = r.id
        _login(app, client, owner_uid)
        resp = client.post(f"/aircraft/{acid}/reservations/{res_id}/confirm")
        assert resp.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.PENDING

    def test_creating_downtime_over_confirmed_reservation_shows_conflict(
        self, app, client
    ):
        owner_uid, tid = _make_user(app, "dtc_owner2@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DTC2")
        pilot_uid, _ = _make_user(app, "dtc_pilot2@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            r = Reservation(
                aircraft_id=acid,
                pilot_user_id=pilot_uid,
                start_dt=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                end_dt=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
                status=ReservationStatus.CONFIRMED,
            )
            db.session.add(r)
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={"start_dt": "2026-08-20T00:00", "end_dt": "2026-08-22T00:00"},
        )
        assert r.status_code == 200
        assert b"overlaps" in r.data
        with app.app_context():
            assert MaintenanceDowntime.query.filter_by(aircraft_id=acid).count() == 0

    def test_confirm_conflicts_flag_saves_anyway(self, app, client):
        owner_uid, tid = _make_user(app, "dtc_owner3@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-DTC3")
        pilot_uid, _ = _make_user(app, "dtc_pilot3@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            r = Reservation(
                aircraft_id=acid,
                pilot_user_id=pilot_uid,
                start_dt=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                end_dt=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
                status=ReservationStatus.CONFIRMED,
            )
            db.session.add(r)
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/downtimes/new",
            data={
                "start_dt": "2026-08-20T00:00",
                "end_dt": "2026-08-22T00:00",
                "confirm_conflicts": "1",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            assert MaintenanceDowntime.query.filter_by(aircraft_id=acid).count() == 1


# ── Calendar rendering ─────────────────────────────────────────────────────────────


class TestDowntimeCalendar:
    def test_downtime_renders_on_calendar(self, app, client):
        uid, tid = _make_user(app, "cal_owner1@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-CAL1")
        now = datetime.now(timezone.utc)
        with app.app_context():
            db.session.add(
                MaintenanceDowntime(
                    aircraft_id=acid,
                    start_dt=now + timedelta(days=1),
                    end_dt=now + timedelta(days=2),
                    reason="Prop balancing",
                )
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.get(
            f"/aircraft/{acid}/reservations/?year={now.year}&month={now.month}"
        )
        assert r.status_code == 200
        assert b"Prop balancing" in r.data

    def test_block_period_button_visible_to_owner(self, app, client):
        uid, tid = _make_user(app, "cal_owner2@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-CAL2")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/")
        assert r.status_code == 200
        assert b"Block period" in r.data

    def test_block_period_button_hidden_from_pilot(self, app, client):
        uid, tid = _make_user(app, "cal_pilot1@ex.com", role=Role.PILOT)
        acid = _make_aircraft(app, tid, "OO-CAL3")
        _grant_access(app, uid, acid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/")
        assert r.status_code == 200
        assert b"Block period" not in r.data


# ── Grounding notification ─────────────────────────────────────────────────────────


class TestGroundingNotification:
    def test_grounding_snag_notifies_future_confirmed_holder(self, app, client):
        owner_uid, tid = _make_user(app, "gn_owner1@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GN1")
        pilot_uid, _ = _make_user(app, "gn_pilot1@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        res_id = _make_reservation(
            app, acid, pilot_uid, status=ReservationStatus.CONFIRMED, start_offset=48
        )
        _login(app, client, owner_uid)
        sent = []
        import services.notification_service as ns

        original_dispatch = ns.dispatch

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append((notification_type, target_user_ids))
            return original_dispatch(notification_type, tenant_id, ctx, target_user_ids)

        ns.dispatch = _capture
        try:
            r = client.post(
                f"/aircraft/{acid}/snags/new",
                data={"title": "Cracked windshield", "is_grounding": "1"},
            )
            assert r.status_code == 302
        finally:
            ns.dispatch = original_dispatch

        from models import NotificationType

        grounded_calls = [
            c for c in sent if c[0] == NotificationType.RESERVATION_AIRCRAFT_GROUNDED
        ]
        assert len(grounded_calls) == 1
        assert grounded_calls[0][1] == [pilot_uid]
        assert res_id  # reservation exists and was matched by tenant/aircraft

    def test_grounding_snag_skips_notification_with_no_future_reservations(
        self, app, client
    ):
        owner_uid, tid = _make_user(app, "gn_owner2@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GN2")
        _login(app, client, owner_uid)
        sent = []
        import services.notification_service as ns

        original_dispatch = ns.dispatch

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append((notification_type, target_user_ids))
            return original_dispatch(notification_type, tenant_id, ctx, target_user_ids)

        ns.dispatch = _capture
        try:
            r = client.post(
                f"/aircraft/{acid}/snags/new",
                data={"title": "Cracked windshield", "is_grounding": "1"},
            )
            assert r.status_code == 302
        finally:
            ns.dispatch = original_dispatch

        from models import NotificationType

        grounded_calls = [
            c for c in sent if c[0] == NotificationType.RESERVATION_AIRCRAFT_GROUNDED
        ]
        assert grounded_calls == []

    def test_non_grounding_snag_never_triggers_notification(self, app, client):
        owner_uid, tid = _make_user(app, "gn_owner3@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GN3")
        pilot_uid, _ = _make_user(app, "gn_pilot3@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _make_reservation(
            app, acid, pilot_uid, status=ReservationStatus.CONFIRMED, start_offset=48
        )
        _login(app, client, owner_uid)
        sent = []
        import services.notification_service as ns

        original_dispatch = ns.dispatch

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append((notification_type, target_user_ids))
            return original_dispatch(notification_type, tenant_id, ctx, target_user_ids)

        ns.dispatch = _capture
        try:
            r = client.post(
                f"/aircraft/{acid}/snags/new",
                data={"title": "Cosmetic scratch"},
            )
            assert r.status_code == 302
        finally:
            ns.dispatch = original_dispatch

        from models import NotificationType

        grounded_calls = [
            c for c in sent if c[0] == NotificationType.RESERVATION_AIRCRAFT_GROUNDED
        ]
        assert grounded_calls == []

    def test_past_and_cancelled_reservations_excluded(self, app, client):
        owner_uid, tid = _make_user(app, "gn_owner4@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-GN4")
        pilot_uid, _ = _make_user(app, "gn_pilot4@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=pilot_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        # Past confirmed reservation — excluded.
        _make_reservation(
            app, acid, pilot_uid, status=ReservationStatus.CONFIRMED, start_offset=-48
        )
        # Future cancelled reservation — excluded.
        _make_reservation(
            app, acid, pilot_uid, status=ReservationStatus.CANCELLED, start_offset=48
        )
        _login(app, client, owner_uid)
        sent = []
        import services.notification_service as ns

        original_dispatch = ns.dispatch

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append((notification_type, target_user_ids))
            return original_dispatch(notification_type, tenant_id, ctx, target_user_ids)

        ns.dispatch = _capture
        try:
            r = client.post(
                f"/aircraft/{acid}/snags/new",
                data={"title": "Cracked windshield", "is_grounding": "1"},
            )
            assert r.status_code == 302
        finally:
            ns.dispatch = original_dispatch

        from models import NotificationType

        grounded_calls = [
            c for c in sent if c[0] == NotificationType.RESERVATION_AIRCRAFT_GROUNDED
        ]
        assert grounded_calls == []

    def test_notification_preference_default_enabled(self, app):
        from models import NotificationType

        with app.app_context():
            assert (
                NotificationType.SYSTEM_DEFAULTS[
                    NotificationType.RESERVATION_AIRCRAFT_GROUNDED
                ]["enabled"]
                is True
            )
            assert set(
                NotificationType.REQUIRED_CAPS[
                    NotificationType.RESERVATION_AIRCRAFT_GROUNDED
                ]
            ) == {"is_owner", "is_pilot", "is_maint"}
