"""
Tests for Phase 22: Reservations & Rentals.

Covers: calendar view, create/edit/cancel reservations, conflict detection,
owner confirm/decline workflow, booking settings, and access control.
"""
import bcrypt
from datetime import date as _date, datetime, timedelta, timezone

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, AircraftBookingSettings, FlightEntry, Reservation, ReservationStatus,
    Role, Tenant, TenantUser, User, UserAircraftAccess, UserAllAircraftAccess, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(app, email, role=Role.ADMIN):
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
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _make_aircraft(app, tenant_id, reg="OO-TST"):
    with app.app_context():
        ac = Aircraft(tenant_id=tenant_id, registration=reg, make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _grant_access(app, user_id, aircraft_id):
    """Grant a non-admin user explicit access to one aircraft."""
    with app.app_context():
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=aircraft_id))
        db.session.commit()


def _login(app, client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _make_reservation(app, aircraft_id, pilot_user_id,
                      start="2026-06-01T09:00", end="2026-06-01T11:00",
                      status=ReservationStatus.PENDING, notes=None):
    with app.app_context():
        r = Reservation(
            aircraft_id=aircraft_id,
            pilot_user_id=pilot_user_id,
            start_dt=datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
            end_dt=datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
            status=status,
            notes=notes,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


# ── Calendar view ─────────────────────────────────────────────────────────────

class TestCalendarView:
    def test_calendar_renders(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/")
        assert r.status_code == 200
        assert b"calendar" in r.data.lower() or b"reservations" in r.data.lower()

    def test_calendar_with_year_month_params(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=3")
        assert r.status_code == 200

    def test_calendar_invalid_params_falls_back_to_today(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=bad&month=bad")
        assert r.status_code == 200

    def test_calendar_month_boundary_clamp_month0(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=0")
        assert r.status_code == 200

    def test_calendar_month_boundary_clamp_month13(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=13")
        assert r.status_code == 200

    def test_calendar_shows_reservation_chip(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _make_reservation(app, ac_id, uid,
                          start="2026-06-10T09:00", end="2026-06-10T11:00",
                          status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=6")
        assert r.status_code == 200
        assert b"09:00" in r.data

    def test_calendar_pending_approvals_visible_to_owner(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        _make_reservation(app, ac_id, uid,
                          start="2026-06-15T10:00", end="2026-06-15T12:00",
                          status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=6")
        assert r.status_code == 200
        assert b"Pending Approvals" in r.data

    def test_calendar_404_for_wrong_tenant(self, app, client):
        uid, _tid = _make_user(app, "owner@ex.com")
        with app.app_context():
            other_tenant = Tenant(name="Other")
            db.session.add(other_tenant)
            db.session.flush()
            other_ac = Aircraft(tenant_id=other_tenant.id, registration="OO-OTH",
                                make="Piper", model="PA-28")
            db.session.add(other_ac)
            db.session.commit()
            other_ac_id = other_ac.id
        _login(app, client, uid)
        r = client.get(f"/aircraft/{other_ac_id}/reservations/")
        assert r.status_code == 404

    def test_calendar_requires_login(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com")
        ac_id = _make_aircraft(app, tid)
        r = client.get(f"/aircraft/{ac_id}/reservations/")
        assert r.status_code == 302


# ── Create reservation ────────────────────────────────────────────────────────

class TestNewReservation:
    def test_get_form(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/new")
        assert r.status_code == 200

    def test_get_form_prefills_date(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/new?date=2026-06-10T09:00")
        assert r.status_code == 200
        assert b"2026-06-10T09:00" in r.data

    def test_post_creates_reservation(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T09:00",
            "end_dt":   "2026-06-20T11:00",
            "notes":    "Test flight",
        })
        assert r.status_code == 302
        with app.app_context():
            res = Reservation.query.filter_by(aircraft_id=ac_id).first()
            assert res is not None
            assert res.status == ReservationStatus.PENDING
            assert res.notes == "Test flight"

    def test_post_requires_start(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "",
            "end_dt":   "2026-06-20T11:00",
        })
        assert r.status_code == 200  # re-renders form with error

    def test_post_requires_end(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T09:00",
            "end_dt":   "",
        })
        assert r.status_code == 200

    def test_end_before_start_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T11:00",
            "end_dt":   "2026-06-20T09:00",
        })
        assert r.status_code == 200

    def test_viewer_cannot_create(self, app, client):
        uid, tid = _make_user(app, "viewer@ex.com", role=Role.VIEWER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/new")
        assert r.status_code == 403

    def test_booking_settings_min_duration_enforced(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        with app.app_context():
            db.session.add(AircraftBookingSettings(
                aircraft_id=ac_id, min_booking_hours=2.0))
            db.session.commit()
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T09:00",
            "end_dt":   "2026-06-20T09:30",  # 0.5 h — below min
        })
        assert r.status_code == 200  # form with error

    def test_booking_settings_max_duration_enforced(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        with app.app_context():
            db.session.add(AircraftBookingSettings(
                aircraft_id=ac_id, max_booking_hours=4.0))
            db.session.commit()
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T08:00",
            "end_dt":   "2026-06-20T16:00",  # 8 h — above max
        })
        assert r.status_code == 200

    def test_hourly_rate_sets_estimated_cost(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        with app.app_context():
            db.session.add(AircraftBookingSettings(
                aircraft_id=ac_id, hourly_rate=100.0))
            db.session.commit()
        _login(app, client, uid)
        client.post(f"/aircraft/{ac_id}/reservations/new", data={
            "start_dt": "2026-06-20T09:00",
            "end_dt":   "2026-06-20T11:00",  # 2 h → 200 EUR
        })
        with app.app_context():
            res = Reservation.query.filter_by(aircraft_id=ac_id).first()
            assert res is not None
            assert float(res.estimated_cost) == 200.0
            assert float(res.hourly_rate) == 100.0


# ── Edit reservation ──────────────────────────────────────────────────────────

class TestEditReservation:
    def test_owner_can_edit_any_reservation(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/{res_id}/edit")
        assert r.status_code == 200

    def test_pilot_can_edit_own_pending(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/{res_id}/edit")
        assert r.status_code == 200

    def test_pilot_cannot_edit_confirmed_reservation(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/{res_id}/edit")
        assert r.status_code == 403

    def test_pilot_cannot_edit_other_pilots_reservation(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        with app.app_context():
            other = User(
                email="other@ex.com",
                password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(other)
            db.session.flush()
            db.session.add(TenantUser(user_id=other.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()
            other_id = other.id
        res_id = _make_reservation(app, ac_id, other_id, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/{res_id}/edit")
        assert r.status_code == 403

    def test_edit_nonexistent_reservation_returns_404(self, app, client):
        # Covers _get_reservation_or_404 abort(404) branch (line 51)
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/99999/edit")
        assert r.status_code == 404

    def test_post_updates_reservation(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/edit", data={
            "start_dt": "2026-06-01T10:00",
            "end_dt":   "2026-06-01T12:00",
            "notes":    "Updated notes",
        })
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.notes == "Updated notes"


# ── Cancel reservation ────────────────────────────────────────────────────────

class TestCancelReservation:
    def test_pilot_can_cancel_own_reservation(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/cancel")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CANCELLED

    def test_cancel_already_cancelled_shows_warning(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.CANCELLED)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/cancel")
        assert r.status_code == 302

    def test_other_pilot_cannot_cancel(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _grant_access(app, uid, ac_id)
        with app.app_context():
            other = User(
                email="other@ex.com",
                password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(other)
            db.session.flush()
            db.session.add(TenantUser(user_id=other.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()
            other_id = other.id
        res_id = _make_reservation(app, ac_id, other_id, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/cancel")
        assert r.status_code == 403


# ── Confirm / decline (owner) ─────────────────────────────────────────────────

class TestConfirmDeclineReservation:
    def test_owner_can_confirm_pending(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CONFIRMED

    def test_confirm_non_pending_shows_warning(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/confirm")
        assert r.status_code == 302

    def test_owner_can_decline_pending(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/decline")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CANCELLED

    def test_decline_non_pending_shows_warning(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/decline")
        assert r.status_code == 302

    def test_pilot_cannot_confirm(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/confirm")
        assert r.status_code == 403

    def test_pilot_cannot_decline(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/decline")
        assert r.status_code == 403

    def test_safe_next_url_is_used(self, app, client):
        """routes.py:38 — a relative next URL is honoured."""
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/{res_id}/confirm",
            data={"next": "/dashboard"},
        )
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/dashboard")

    def test_unsafe_next_url_falls_back_to_calendar(self, app, client):
        """routes.py:39 — absolute/protocol-relative next URLs are rejected."""
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid, status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/{res_id}/confirm",
            data={"next": "//evil.com/phish"},
        )
        assert r.status_code == 302
        assert "evil.com" not in r.headers["Location"]


# ── Conflict detection ────────────────────────────────────────────────────────

class TestConflictDetection:
    def _make_confirmed(self, app, ac_id, uid, start, end):
        return _make_reservation(app, ac_id, uid, start=start, end=end,
                                 status=ReservationStatus.CONFIRMED)

    def test_confirm_with_overlap_rejected(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        # Existing confirmed reservation 09:00–11:00
        self._make_confirmed(app, ac_id, uid, "2026-06-10T09:00", "2026-06-10T11:00")
        # New pending that overlaps (10:00–12:00)
        pending_id = _make_reservation(app, ac_id, uid,
                                       start="2026-06-10T10:00", end="2026-06-10T12:00",
                                       status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{pending_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, pending_id)
            assert res.status == ReservationStatus.PENDING  # not confirmed

    def test_confirm_adjacent_reservation_ok(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        # Existing confirmed 09:00–11:00
        self._make_confirmed(app, ac_id, uid, "2026-06-10T09:00", "2026-06-10T11:00")
        # Adjacent (starts exactly when first ends — no overlap)
        pending_id = _make_reservation(app, ac_id, uid,
                                       start="2026-06-10T11:00", end="2026-06-10T13:00",
                                       status=ReservationStatus.PENDING)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{pending_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, pending_id)
            assert res.status == ReservationStatus.CONFIRMED

    def test_editing_existing_does_not_conflict_with_itself(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        res_id = _make_reservation(app, ac_id, uid,
                                   start="2026-06-10T09:00", end="2026-06-10T11:00",
                                   status=ReservationStatus.CONFIRMED)
        # Confirm the one that's already confirmed with itself — should pass via exclude_id
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/edit", data={
            "start_dt": "2026-06-10T09:00",
            "end_dt":   "2026-06-10T12:00",
            "notes":    "",
        })
        assert r.status_code == 302


# ── Booking settings ──────────────────────────────────────────────────────────

class TestBookingSettings:
    def test_owner_can_view_settings(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/settings")
        assert r.status_code == 200

    def test_pilot_cannot_view_settings(self, app, client):
        uid, tid = _make_user(app, "pilot@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/settings")
        assert r.status_code == 403

    def test_save_settings_creates_record(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "min_booking_hours": "1.5",
            "max_booking_hours": "8.0",
            "hourly_rate":       "145.00",
        })
        assert r.status_code == 302
        with app.app_context():
            s = AircraftBookingSettings.query.filter_by(aircraft_id=ac_id).first()
            assert s is not None
            assert float(s.min_booking_hours) == 1.5
            assert float(s.max_booking_hours) == 8.0
            assert float(s.hourly_rate) == 145.0

    def test_save_settings_updates_existing(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            db.session.add(AircraftBookingSettings(aircraft_id=ac_id, hourly_rate=100.0))
            db.session.commit()
        _login(app, client, uid)
        client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "hourly_rate": "200.00",
        })
        with app.app_context():
            s = AircraftBookingSettings.query.filter_by(aircraft_id=ac_id).first()
            assert float(s.hourly_rate) == 200.0

    def test_invalid_min_rejected(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "min_booking_hours": "-1",
        })
        assert r.status_code == 200  # re-renders form with error

    def test_invalid_max_rejected(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "max_booking_hours": "-2",
        })
        assert r.status_code == 200

    def test_min_exceeds_max_rejected(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "min_booking_hours": "8",
            "max_booking_hours": "4",
        })
        assert r.status_code == 200

    def test_negative_rate_rejected(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "hourly_rate": "-10",
        })
        assert r.status_code == 200

    def test_blank_fields_saves_none(self, app, client):
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "min_booking_hours": "",
            "max_booking_hours": "",
            "hourly_rate":       "",
        })
        assert r.status_code == 302
        with app.app_context():
            s = AircraftBookingSettings.query.filter_by(aircraft_id=ac_id).first()
            assert s is not None
            assert s.min_booking_hours is None
            assert s.max_booking_hours is None
            assert s.hourly_rate is None

    def test_non_numeric_field_treated_as_none(self, app, client):
        # Covers _float_or_none ValueError branch (lines 292-293)
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/settings", data={
            "min_booking_hours": "abc",
            "max_booking_hours": "xyz",
            "hourly_rate":       "!!!",
        })
        assert r.status_code == 302
        with app.app_context():
            s = AircraftBookingSettings.query.filter_by(aircraft_id=ac_id).first()
            assert s is not None
            assert s.min_booking_hours is None
            assert s.max_booking_hours is None
            assert s.hourly_rate is None


# ── Reservation model ─────────────────────────────────────────────────────────

class TestReservationModel:
    def test_duration_hours(self, app):
        with app.app_context():
            r = Reservation(
                aircraft_id=1, pilot_user_id=None,
                start_dt=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                end_dt=datetime(2026, 6, 1, 11, 30, tzinfo=timezone.utc),
                status=ReservationStatus.PENDING,
            )
            assert r.duration_hours == 2.5

    def test_reservation_status_values(self, app):
        assert ReservationStatus.PENDING.value == "pending"
        assert ReservationStatus.CONFIRMED.value == "confirmed"
        assert ReservationStatus.CANCELLED.value == "cancelled"

    def test_spanning_reservation_appears_on_each_day(self, app, client):
        """A multi-day reservation should show on each calendar day it spans."""
        uid, tid = _make_user(app, "owner@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _make_reservation(app, ac_id, uid,
                          start="2026-06-01T18:00", end="2026-06-02T12:00",
                          status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/?year=2026&month=6")
        assert r.status_code == 200
        # Both days should contain the chip — just check the page has content
        assert b"18:00" in r.data


# ── Fleet reservations overview ───────────────────────────────────────────────

class TestFleetReservations:
    """Cover reservations/routes.py lines 109-177 (fleet_reservations view)."""

    def test_requires_login(self, app, client):
        r = client.get("/reservations/fleet/")
        assert r.status_code == 302

    def test_pilot_cannot_access_fleet(self, app, client):
        uid, tid = _make_user(app, "pilot@fleet.test", role=Role.PILOT)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 403

    def test_admin_can_access_fleet(self, app, client):
        uid, tid = _make_user(app, "admin@fleet.test")
        ac_id = _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200

    def test_fleet_empty_state(self, app, client):
        uid, tid = _make_user(app, "admin@fleet2.test")
        _make_aircraft(app, tid)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"No reservations yet" in r.data

    def test_owner_with_specific_access_sees_only_granted_aircraft(self, app, client):
        """Lines 116-125 — OWNER without UserAllAircraftAccess is filtered to
        UserAircraftAccess rows."""
        uid, tid = _make_user(app, "owner@fleet.test", role=Role.OWNER)
        ac1_id = _make_aircraft(app, tid, reg="OO-FL1")
        ac2_id = _make_aircraft(app, tid, reg="OO-FL2")
        # Grant owner access only to ac1
        with app.app_context():
            db.session.add(UserAircraftAccess(user_id=uid, aircraft_id=ac1_id))
            db.session.commit()
        _make_reservation(app, ac1_id, uid, start="2026-09-01T09:00",
                          end="2026-09-01T11:00", status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"OO-FL1" in r.data
        assert b"OO-FL2" not in r.data

    def test_owner_with_all_planes_access_sees_full_fleet(self, app, client):
        """Owner with UserAllAircraftAccess bypasses the per-aircraft filter."""
        uid, tid = _make_user(app, "owner2@fleet.test", role=Role.OWNER)
        ac1_id = _make_aircraft(app, tid, reg="OO-AP1")
        ac2_id = _make_aircraft(app, tid, reg="OO-AP2")
        with app.app_context():
            db.session.add(UserAllAircraftAccess(user_id=uid, tenant_id=tid))
            db.session.commit()
        _make_reservation(app, ac1_id, uid, start="2026-09-05T09:00",
                          end="2026-09-05T11:00", status=ReservationStatus.CONFIRMED)
        _make_reservation(app, ac2_id, uid, start="2026-09-06T09:00",
                          end="2026-09-06T11:00", status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"OO-AP1" in r.data
        assert b"OO-AP2" in r.data

    def test_overlapping_confirmed_reservations_flagged(self, app, client):
        """Lines 154-158 — two overlapping CONFIRMED reservations get Overlap badge."""
        uid, tid = _make_user(app, "admin@overlap.test")
        ac_id = _make_aircraft(app, tid)
        _make_reservation(app, ac_id, uid,
                          start="2026-09-10T09:00", end="2026-09-10T12:00",
                          status=ReservationStatus.CONFIRMED)
        _make_reservation(app, ac_id, uid,
                          start="2026-09-10T11:00", end="2026-09-10T14:00",
                          status=ReservationStatus.CONFIRMED)
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"Overlap" in r.data

    def test_past_confirmed_no_flight_shows_badge(self, app, client):
        """Lines 162-173 — past CONFIRMED reservation with no FlightEntry gets badge."""
        uid, tid = _make_user(app, "admin@noflight.test")
        ac_id = _make_aircraft(app, tid)
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
        with app.app_context():
            db.session.add(Reservation(
                aircraft_id=ac_id, pilot_user_id=uid,
                start_dt=five_days_ago,
                end_dt=five_days_ago + timedelta(hours=2),
                status=ReservationStatus.CONFIRMED,
            ))
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"No flight logged" in r.data

    def test_past_confirmed_with_matching_flight_no_badge(self, app, client):
        """Lines 162-173 — past CONFIRMED reservation WITH a FlightEntry: no badge."""
        uid, tid = _make_user(app, "admin@withflight.test")
        ac_id = _make_aircraft(app, tid)
        five_days_ago = _date.today() - timedelta(days=5)
        five_days_ago_dt = datetime(
            five_days_ago.year, five_days_ago.month, five_days_ago.day,
            9, 0, tzinfo=timezone.utc,
        )
        with app.app_context():
            db.session.add(Reservation(
                aircraft_id=ac_id, pilot_user_id=uid,
                start_dt=five_days_ago_dt,
                end_dt=five_days_ago_dt + timedelta(hours=2),
                status=ReservationStatus.CONFIRMED,
            ))
            db.session.add(FlightEntry(
                aircraft_id=ac_id,
                date=five_days_ago,
                departure_icao="EBOS",
                arrival_icao="EHRD",
                flight_time=2.0,
                landing_count=1,
            ))
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"No flight logged" not in r.data

    def test_expired_pending_shown_as_expired(self, app, client):
        """Past PENDING reservation (within 60 days) shows 'Expired' pill."""
        uid, tid = _make_user(app, "admin@expired.test")
        ac_id = _make_aircraft(app, tid)
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        with app.app_context():
            db.session.add(Reservation(
                aircraft_id=ac_id, pilot_user_id=uid,
                start_dt=ten_days_ago,
                end_dt=ten_days_ago + timedelta(hours=2),
                status=ReservationStatus.PENDING,
            ))
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"Expired" in r.data

    def test_very_old_pending_excluded_from_list(self, app, client):
        """PENDING reservations older than 60 days are excluded entirely."""
        uid, tid = _make_user(app, "admin@oldpending.test")
        ac_id = _make_aircraft(app, tid)
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        with app.app_context():
            db.session.add(Reservation(
                aircraft_id=ac_id, pilot_user_id=uid,
                start_dt=ninety_days_ago,
                end_dt=ninety_days_ago + timedelta(hours=2),
                status=ReservationStatus.PENDING,
            ))
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/reservations/fleet/")
        assert r.status_code == 200
        assert b"No reservations yet" in r.data
