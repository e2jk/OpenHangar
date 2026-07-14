"""
Tests for Phase 37d: reservation detail page and dispatch (check-out /
check-in), including the discrepancy warning and the post-checkout
cancellation guard. See docs/phase37_rental_spec.md § 37d.
"""

from datetime import datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DispatchRecord,
    FlightEntry,
    Reservation,
    ReservationStatus,
    Role,
    Snag,
    Tenant,
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


def _make_reservation(
    app, aircraft_id, pilot_user_id, hours_from_now_start=-1, hours_from_now_end=1
):
    with app.app_context():
        now = datetime.now(timezone.utc)
        r = Reservation(
            aircraft_id=aircraft_id,
            pilot_user_id=pilot_user_id,
            start_dt=now + timedelta(hours=hours_from_now_start),
            end_dt=now + timedelta(hours=hours_from_now_end),
            status=ReservationStatus.CONFIRMED,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


# ── Reservation detail ────────────────────────────────────────────────────────


class TestReservationDetail:
    def test_pilot_can_view_own(self, app, client):
        uid, tid = _make_user(app, "pilot1@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}")
        assert r.status_code == 200

    def test_owner_can_view_any(self, app, client):
        uid, tid = _make_user(app, "owner1@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter1@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid)
        res_id = _make_reservation(app, acid, renter_uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}")
        assert r.status_code == 200

    def test_other_pilot_forbidden(self, app, client):
        uid, tid = _make_user(app, "pilot2@ex.com")
        other_uid, _ = _make_user(app, "other2@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        _grant_access(app, other_uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, other_uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}")
        assert r.status_code == 403

    def test_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner2@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "owner3@ex.com", role=Role.OWNER)
        other_acid = _make_aircraft(app, other_tid, "OO-OTH")
        res_id = _make_reservation(app, other_acid, other_uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{other_acid}/reservations/{res_id}")
        assert r.status_code == 404


# ── Check-out ──────────────────────────────────────────────────────────────────


class TestCheckout:
    def test_get_prefills_from_latest_flight(self, app, client):
        uid, tid = _make_user(app, "pilot3@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                FlightEntry(
                    aircraft_id=acid,
                    date=datetime.now(timezone.utc).date(),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    flight_time_counter_start=100.0,
                    flight_time_counter_end=102.5,
                    engine_time_counter_start=200.0,
                    engine_time_counter_end=202.9,
                )
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/checkout")
        assert r.status_code == 200
        assert b"102.5" in r.data
        assert b"202.9" in r.data

    def test_checkboxes_required(self, app, client):
        uid, tid = _make_user(app, "pilot4@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={"out_engine_counter": "100.0", "out_flight_counter": "50.0"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert DispatchRecord.query.filter_by(reservation_id=res_id).first() is None

    def test_successful_checkout(self, app, client):
        uid, tid = _make_user(app, "pilot5@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "out_engine_counter": "200.0",
                "out_flight_counter": "100.0",
                "out_fuel_state": "full",
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert d.is_checked_out
            assert float(d.out_engine_counter) == 200.0
            assert float(d.out_flight_counter) == 100.0
            assert d.out_fuel_state == "full"
            assert d.out_by_id == uid

    def test_grounded_blocks_non_owner(self, app, client):
        uid, tid = _make_user(app, "pilot6@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                Snag(aircraft_id=acid, title="Engine fire", is_grounding=True)
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={"walkaround_ok": "1", "snags_acknowledged": "1"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert DispatchRecord.query.filter_by(reservation_id=res_id).first() is None

    def test_owner_override_recorded(self, app, client):
        uid, tid = _make_user(app, "owner4@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid)
        with app.app_context():
            db.session.add(Snag(aircraft_id=acid, title="Flat tyre", is_grounding=True))
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
                "grounded_override": "1",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert d.is_checked_out
            assert d.out_grounded_override is True

    def test_double_checkout_refused(self, app, client):
        uid, tid = _make_user(app, "pilot7@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={"walkaround_ok": "1", "snags_acknowledged": "1"},
        )
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={"walkaround_ok": "1", "snags_acknowledged": "1"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"already been checked out" in r.data
        with app.app_context():
            assert DispatchRecord.query.filter_by(reservation_id=res_id).count() == 1

    def test_other_pilot_cannot_checkout(self, app, client):
        uid, tid = _make_user(app, "pilot8@ex.com")
        other_uid, _ = _make_user(app, "other8@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        _grant_access(app, other_uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, other_uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/checkout")
        assert r.status_code == 403

    def test_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner5@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "owner6@ex.com", role=Role.OWNER)
        other_acid = _make_aircraft(app, other_tid, "OO-XYZ")
        res_id = _make_reservation(app, other_acid, other_uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{other_acid}/reservations/{res_id}/checkout")
        assert r.status_code == 404

    def test_only_confirmed_can_be_checked_out(self, app, client):
        uid, tid = _make_user(app, "pilot9@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            now = datetime.now(timezone.utc)
            r = Reservation(
                aircraft_id=acid,
                pilot_user_id=uid,
                start_dt=now - timedelta(hours=1),
                end_dt=now + timedelta(hours=1),
                status=ReservationStatus.PENDING,
            )
            db.session.add(r)
            db.session.commit()
            res_id = r.id
        _login(app, client, uid)
        r = client.get(
            f"/aircraft/{acid}/reservations/{res_id}/checkout", follow_redirects=True
        )
        assert r.status_code == 200
        assert b"Only confirmed reservations" in r.data

    def test_invalid_counter_value_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot10@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
                "out_engine_counter": "not-a-number",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            assert DispatchRecord.query.filter_by(reservation_id=res_id).first() is None

    def test_invalid_flight_counter_value_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot10b@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
                "out_flight_counter": "not-a-number",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            assert DispatchRecord.query.filter_by(reservation_id=res_id).first() is None


# ── Check-in ───────────────────────────────────────────────────────────────────


class TestCheckin:
    def _checkout(self, app, client, acid, res_id, engine=200.0, flight=100.0):
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "out_engine_counter": str(engine),
                "out_flight_counter": str(flight),
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
            },
        )

    def test_checkin_without_checkout_refused(self, app, client):
        uid, tid = _make_user(app, "pilot11@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.get(
            f"/aircraft/{acid}/reservations/{res_id}/checkin", follow_redirects=True
        )
        assert r.status_code == 200
        assert b"Check out before checking in" in r.data

    def test_successful_checkin(self, app, client):
        uid, tid = _make_user(app, "pilot12@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={
                "in_engine_counter": "202.5",
                "in_flight_counter": "101.3",
                "in_fuel_state": "20 L",
                "in_notes": "Uneventful",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert d.is_checked_in
            assert float(d.in_engine_counter) == 202.5
            assert float(d.in_flight_counter) == 101.3
            assert d.in_notes == "Uneventful"

    def test_counter_below_checkout_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot13@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id, engine=200.0, flight=100.0)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "199.0", "in_flight_counter": "101.0"},
        )
        assert r.status_code == 200
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert not d.is_checked_in

    def test_double_checkin_refused(self, app, client):
        uid, tid = _make_user(app, "pilot14@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "202.5", "in_flight_counter": "101.3"},
        )
        r = client.get(
            f"/aircraft/{acid}/reservations/{res_id}/checkin", follow_redirects=True
        )
        assert r.status_code == 200
        assert b"already been checked in" in r.data

    def test_invalid_counter_value_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot15@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "bad", "in_flight_counter": "101.3"},
        )
        assert r.status_code == 200
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert not d.is_checked_in

    def test_invalid_flight_counter_value_rejected(self, app, client):
        uid, tid = _make_user(app, "pilot15b@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "202.5", "in_flight_counter": "bad"},
        )
        assert r.status_code == 200
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert not d.is_checked_in

    def test_flight_counter_below_checkout_rejected(self, app, client):
        """Pin the flight-counter-specific message — distinct from the
        engine-counter one, and only reached when the engine counter check
        didn't already add an error."""
        uid, tid = _make_user(app, "pilot15c@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id, engine=200.0, flight=100.0)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "205.0", "in_flight_counter": "99.0"},
        )
        assert r.status_code == 200
        assert b"Flight counter on return cannot be less" in r.data
        with app.app_context():
            d = DispatchRecord.query.filter_by(reservation_id=res_id).one()
            assert not d.is_checked_in

    def test_get_form_renders_when_ready_to_check_in(self, app, client):
        uid, tid = _make_user(app, "pilot15d@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/checkin")
        assert r.status_code == 200

    def test_other_pilot_cannot_checkin(self, app, client):
        uid, tid = _make_user(app, "pilot15e@ex.com")
        other_uid, _ = _make_user(app, "other15e@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        _grant_access(app, other_uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        self._checkout(app, client, acid, res_id)
        _login(app, client, other_uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/checkin")
        assert r.status_code == 403


# ── Discrepancy warning ─────────────────────────────────────────────────────────


class TestDiscrepancyWarning:
    def test_no_warning_when_deltas_match(self, app, client):
        uid, tid = _make_user(app, "pilot16@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "out_engine_counter": "200.0",
                "out_flight_counter": "100.0",
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
            },
        )
        with app.app_context():
            db.session.add(
                FlightEntry(
                    aircraft_id=acid,
                    date=datetime.now(timezone.utc).date(),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    flight_time_counter_start=100.0,
                    flight_time_counter_end=101.5,
                    engine_time_counter_start=200.0,
                    engine_time_counter_end=202.0,
                    reservation_id=res_id,
                )
            )
            db.session.commit()
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "202.0", "in_flight_counter": "101.5"},
        )
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}")
        assert b"discrepancy" not in r.data.lower()

    def test_warning_when_deltas_differ(self, app, client):
        uid, tid = _make_user(app, "pilot17@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={
                "out_engine_counter": "200.0",
                "out_flight_counter": "100.0",
                "walkaround_ok": "1",
                "snags_acknowledged": "1",
            },
        )
        with app.app_context():
            # Only a 1.0h flight logged, but the dispatch delta will be 3.0h.
            db.session.add(
                FlightEntry(
                    aircraft_id=acid,
                    date=datetime.now(timezone.utc).date(),
                    departure_icao="EBOS",
                    arrival_icao="EBBR",
                    flight_time_counter_start=100.0,
                    flight_time_counter_end=101.0,
                    reservation_id=res_id,
                )
            )
            db.session.commit()
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_flight_counter": "103.0"},
        )
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}")
        assert b"discrepancy" in r.data.lower()
        assert b"3.0" in r.data
        assert b"1.0" in r.data


# ── Cancellation after check-out ────────────────────────────────────────────────


class TestCancellationAfterCheckout:
    def test_cancel_refused_after_checkout(self, app, client):
        uid, tid = _make_user(app, "pilot18@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkout",
            data={"walkaround_ok": "1", "snags_acknowledged": "1"},
        )
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/cancel", follow_redirects=True
        )
        assert r.status_code == 200
        assert b"already been checked out" in r.data
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CONFIRMED

    def test_cancel_allowed_before_checkout(self, app, client):
        uid, tid = _make_user(app, "pilot19@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.post(f"/aircraft/{acid}/reservations/{res_id}/cancel")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CANCELLED
