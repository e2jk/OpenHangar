"""
Tests for Phase 37e: rental charges & settlement — draft math at check-in,
finalize/immutability, payments, renter account pages, and statement CSV
export. See docs/phase37_rental_spec.md § 37e.
"""

from datetime import datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftBookingSettings,
    BillingAccount,
    BillingAccountKind,
    Expense,
    ExpenseType,
    FlightEntry,
    LedgerEntry,
    RateBasis,
    RateType,
    RentalCharge,
    Reservation,
    ReservationStatus,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    db,
)
from services.billing import BillingService  # pyright: ignore[reportMissingImports]


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


def _make_aircraft(app, tenant_id, reg="OO-TST", **kwargs):
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


def _make_reservation(app, aircraft_id, pilot_user_id, start_offset=-1, end_offset=1):
    with app.app_context():
        now = datetime.now(timezone.utc)
        r = Reservation(
            aircraft_id=aircraft_id,
            pilot_user_id=pilot_user_id,
            start_dt=now + timedelta(hours=start_offset),
            end_dt=now + timedelta(hours=end_offset),
            status=ReservationStatus.CONFIRMED,
        )
        db.session.add(r)
        db.session.commit()
        return r.id


def _checkin_cycle(
    app,
    client,
    acid,
    res_id,
    out_engine=200.0,
    out_flight=100.0,
    in_engine=202.0,
    in_flight=101.5,
):
    client.post(
        f"/aircraft/{acid}/reservations/{res_id}/checkout",
        data={
            "out_engine_counter": str(out_engine) if out_engine is not None else "",
            "out_flight_counter": str(out_flight) if out_flight is not None else "",
            "walkaround_ok": "1",
            "snags_acknowledged": "1",
        },
    )
    client.post(
        f"/aircraft/{acid}/reservations/{res_id}/checkin",
        data={
            "in_engine_counter": str(in_engine) if in_engine is not None else "",
            "in_flight_counter": str(in_flight) if in_flight is not None else "",
        },
    )


# ── Draft math ─────────────────────────────────────────────────────────────────


class TestDraftMath:
    def test_engine_time_basis_selected(self, app, client):
        uid, tid = _make_user(app, "pilot1@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid,
                    hourly_rate=100.0,
                    rate_basis=RateBasis.ENGINE_TIME,
                )
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(
            app,
            client,
            acid,
            res_id,
            out_engine=200.0,
            out_flight=100.0,
            in_engine=203.0,
            in_flight=150.0,
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            # engine delta = 3.0, flight delta = 50.0 — engine basis must win
            assert float(charge.billable_hours) == 3.0
            assert charge.fallback_counter_used is False

    def test_flight_time_basis_selected(self, app, client):
        uid, tid = _make_user(app, "pilot2@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid,
                    hourly_rate=100.0,
                    rate_basis=RateBasis.FLIGHT_TIME,
                )
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(
            app,
            client,
            acid,
            res_id,
            out_engine=200.0,
            out_flight=100.0,
            in_engine=203.0,
            in_flight=101.5,
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.billable_hours) == 1.5

    def test_counter_fallback_when_preferred_left_blank(self, app, client):
        """rate_basis=engine_time but the engine counter was left blank at
        dispatch — falls back to the flight counter delta."""
        uid, tid = _make_user(app, "pilot3@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid,
                    hourly_rate=100.0,
                    rate_basis=RateBasis.ENGINE_TIME,
                )
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(
            app,
            client,
            acid,
            res_id,
            out_engine=None,
            out_flight=100.0,
            in_engine=None,
            in_flight=102.0,
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.billable_hours) == 2.0
            assert charge.fallback_counter_used is True

    def test_min_hours_per_day_floor_applied(self, app, client):
        uid, tid = _make_user(app, "pilot4@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid, hourly_rate=100.0, min_hours_per_day=5.0
                )
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        # Only a 1.5h counter delta, but the 1-day floor of 5h must win.
        _checkin_cycle(
            app,
            client,
            acid,
            res_id,
            out_engine=200.0,
            out_flight=100.0,
            in_engine=201.5,
            in_flight=101.5,
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.billable_hours) == 5.0

    def test_no_rate_configured_drafts_at_zero(self, app, client):
        uid, tid = _make_user(app, "pilot5@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(app, client, acid, res_id)
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.hourly_rate) == 0.0
            assert float(charge.total) == 0.0

    def test_fuel_credit_sums_only_renters_linked_fuel_expenses(self, app, client):
        uid, tid = _make_user(app, "pilot6@ex.com")
        other_uid, _ = _make_user(app, "other6@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid, hourly_rate=100.0, rate_type=RateType.WET
                )
            )
            db.session.commit()
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
            fe = FlightEntry(
                aircraft_id=acid,
                date=datetime.now(timezone.utc).date(),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                reservation_id=res_id,
            )
            db.session.add(fe)
            db.session.flush()
            # Renter's own fuel expense on the linked flight — counted.
            db.session.add(
                Expense(
                    aircraft_id=acid,
                    flight_entry_id=fe.id,
                    date=datetime.now(timezone.utc).date(),
                    expense_type=ExpenseType.FUEL,
                    amount=45.0,
                    created_by_id=uid,
                )
            )
            # Someone else's fuel expense on the same flight — not counted.
            db.session.add(
                Expense(
                    aircraft_id=acid,
                    flight_entry_id=fe.id,
                    date=datetime.now(timezone.utc).date(),
                    expense_type=ExpenseType.FUEL,
                    amount=99.0,
                    created_by_id=other_uid,
                )
            )
            # Renter's non-fuel expense on the flight — not counted.
            db.session.add(
                Expense(
                    aircraft_id=acid,
                    flight_entry_id=fe.id,
                    date=datetime.now(timezone.utc).date(),
                    expense_type=ExpenseType.PARTS,
                    amount=15.0,
                    created_by_id=uid,
                )
            )
            db.session.commit()
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "202.0", "in_flight_counter": "101.5"},
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.fuel_credit) == 45.0

    def test_dry_rate_forces_fuel_credit_zero(self, app, client):
        uid, tid = _make_user(app, "pilot7@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(
                    aircraft_id=acid, hourly_rate=100.0, rate_type=RateType.DRY
                )
            )
            db.session.commit()
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
            fe = FlightEntry(
                aircraft_id=acid,
                date=datetime.now(timezone.utc).date(),
                departure_icao="EBOS",
                arrival_icao="EBBR",
                reservation_id=res_id,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(
                Expense(
                    aircraft_id=acid,
                    flight_entry_id=fe.id,
                    date=datetime.now(timezone.utc).date(),
                    expense_type=ExpenseType.FUEL,
                    amount=45.0,
                    created_by_id=uid,
                )
            )
            db.session.commit()
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/checkin",
            data={"in_engine_counter": "202.0", "in_flight_counter": "101.5"},
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.fuel_credit) == 0.0

    def test_total_computation(self, app, client):
        uid, tid = _make_user(app, "pilot8@ex.com")
        acid = _make_aircraft(app, tid)
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(AircraftBookingSettings(aircraft_id=acid, hourly_rate=100.0))
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(
            app,
            client,
            acid,
            res_id,
            out_engine=200.0,
            out_flight=100.0,
            in_engine=203.0,
            in_flight=101.5,
        )
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            # engine basis (default) delta 3.0h * 100/h = 300, no fuel credit
            assert float(charge.total) == 300.0

    def test_no_pilot_no_draft(self, app, client):
        """An orphaned reservation (pilot_user_id NULL) gets no charge draft."""
        uid, tid = _make_user(app, "owner8b@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid)
        with app.app_context():
            now = datetime.now(timezone.utc)
            r = Reservation(
                aircraft_id=acid,
                pilot_user_id=None,
                start_dt=now - timedelta(hours=1),
                end_dt=now + timedelta(hours=1),
                status=ReservationStatus.CONFIRMED,
            )
            db.session.add(r)
            db.session.commit()
            res_id = r.id
        _login(app, client, uid)
        _checkin_cycle(app, client, acid, res_id)
        with app.app_context():
            assert RentalCharge.query.filter_by(reservation_id=res_id).first() is None


# ── Finalize / immutability ────────────────────────────────────────────────────


class TestFinalize:
    def _setup_draft(self, app, client, email="pilotf@ex.com", hourly_rate=100.0):
        uid, tid = _make_user(app, email)
        acid = _make_aircraft(app, tid, "OO-FIN")
        _grant_access(app, uid, acid)
        with app.app_context():
            db.session.add(
                AircraftBookingSettings(aircraft_id=acid, hourly_rate=hourly_rate)
            )
            db.session.commit()
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        _checkin_cycle(app, client, acid, res_id)
        return uid, tid, acid, res_id

    def test_finalize_posts_ledger_entry(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client)
        owner_uid, _ = _make_user(app, "ownerf1@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert charge.is_final
            assert charge.finalized_by_id == owner_uid
            assert float(charge.total) == 300.0
            account = BillingAccount.query.filter_by(
                tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
            ).one()
            entries = LedgerEntry.query.filter_by(account_id=account.id).all()
            assert len(entries) == 1
            assert entries[0].entry_type == "charge"
            assert float(entries[0].amount) == 300.0
            assert entries[0].source_type == "rental_charge"
            assert entries[0].source_id == charge.id

    def test_refinalize_refused(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf2@ex.com")
        owner_uid, _ = _make_user(app, "ownerf2@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"already been finalized" in r.data
        with app.app_context():
            account = BillingAccount.query.filter_by(
                tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
            ).one()
            assert LedgerEntry.query.filter_by(account_id=account.id).count() == 1

    def test_save_draft_without_finalizing(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf3@ex.com")
        owner_uid, _ = _make_user(app, "ownerf3@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "2.5",
                "hourly_rate": "80.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert not charge.is_final
            assert float(charge.billable_hours) == 2.5
            assert float(charge.hourly_rate) == 80.0
            assert float(charge.total) == 200.0

    def test_adjustment_without_note_rejected(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf4@ex.com")
        owner_uid, _ = _make_user(app, "ownerf4@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "-10",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.adjustment) == 0

    def test_adjustment_with_note_accepted(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf5@ex.com")
        owner_uid, _ = _make_user(app, "ownerf5@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "-10",
                "adjustment_note": "Goodwill discount",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert float(charge.adjustment) == -10.0
            assert charge.adjustment_note == "Goodwill discount"
            assert float(charge.total) == 290.0

    def test_negative_hours_rejected(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf6@ex.com")
        owner_uid, _ = _make_user(app, "ownerf6@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "-1",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        assert r.status_code == 200

    def test_invalid_field_values_rejected(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf7@ex.com")
        owner_uid, _ = _make_user(app, "ownerf7@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "bad",
                "hourly_rate": "bad",
                "fuel_credit": "bad",
                "adjustment": "bad",
            },
        )
        assert r.status_code == 200

    def test_pilot_cannot_access_charge_route(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf8@ex.com")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/charge")
        assert r.status_code == 403

    def test_no_charge_yet_404s(self, app, client):
        uid, tid = _make_user(app, "ownerf9@ex.com", role=Role.OWNER)
        acid = _make_aircraft(app, tid, "OO-NOCHG")
        res_id = _make_reservation(app, acid, uid)
        _login(app, client, uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/charge")
        assert r.status_code == 404

    def test_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "ownerf10@ex.com", role=Role.OWNER)
        other_uid, other_tid, other_acid, other_res_id = self._setup_draft(
            app, client, "pilotf10@ex.com"
        )
        _login(app, client, uid)
        r = client.get(f"/aircraft/{other_acid}/reservations/{other_res_id}/charge")
        assert r.status_code == 404

    def test_owner_get_renders_draft(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf11@ex.com")
        owner_uid, _ = _make_user(app, "ownerf11@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.get(f"/aircraft/{acid}/reservations/{res_id}/charge")
        assert r.status_code == 200

    def test_negative_hourly_rate_rejected(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf12@ex.com")
        owner_uid, _ = _make_user(app, "ownerf12@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "3.0",
                "hourly_rate": "-100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert not charge.is_final

    def test_negative_fuel_credit_rejected(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf13@ex.com")
        owner_uid, _ = _make_user(app, "ownerf13@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "save",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "-5",
                "adjustment": "0",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            charge = RentalCharge.query.filter_by(reservation_id=res_id).one()
            assert not charge.is_final

    def test_my_account_period_invalid_falls_back(self, app, client):
        uid, tid, acid, res_id = self._setup_draft(app, client, "pilotf14@ex.com")
        _login(app, client, uid)
        r = client.get("/my/account?period=notanumber")
        assert r.status_code == 200
        r = client.get("/my/account?period=-3")
        assert r.status_code == 200


# ── Payments / balance ─────────────────────────────────────────────────────────


class TestPayments:
    def test_owner_records_payment(self, app, client):
        uid, tid = _make_user(app, "renter_pay1@ex.com")
        owner_uid, _ = _make_user(app, "owner_pay1@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/config/renters/{uid}/account/payment",
            data={"amount": "50.00", "note": "Bank transfer"},
        )
        assert r.status_code == 302
        with app.app_context():
            account = BillingAccount.query.filter_by(
                tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
            ).one()
            assert BillingService.balance(account) == -50

    def test_negative_payment_rejected(self, app, client):
        uid, tid = _make_user(app, "renter_pay2@ex.com")
        owner_uid, _ = _make_user(app, "owner_pay2@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/config/renters/{uid}/account/payment",
            data={"amount": "-5"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        with app.app_context():
            account = BillingAccount.query.filter_by(
                tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
            ).first()
            assert account is None or BillingService.balance(account) == 0

    def test_invalid_payment_amount_rejected(self, app, client):
        uid, tid = _make_user(app, "renter_pay3@ex.com")
        owner_uid, _ = _make_user(app, "owner_pay3@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/config/renters/{uid}/account/payment",
            data={"amount": "not-a-number"},
            follow_redirects=True,
        )
        assert r.status_code == 200

    def test_invalid_payment_date_falls_back_to_today(self, app, client):
        uid, tid = _make_user(app, "renter_pay4@ex.com")
        owner_uid, _ = _make_user(app, "owner_pay4@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.post(
            f"/config/renters/{uid}/account/payment",
            data={"amount": "20", "date": "not-a-date"},
            follow_redirects=True,
        )
        assert r.status_code == 200

    def test_payment_for_cross_tenant_user_404s(self, app, client):
        uid, tid = _make_user(app, "owner_pay5b@ex.com", role=Role.OWNER)
        other_uid, _ = _make_user(app, "renter_pay5b@ex.com")
        _login(app, client, uid)
        r = client.post(
            f"/config/renters/{other_uid}/account/payment", data={"amount": "20"}
        )
        assert r.status_code == 404

    def test_account_period_invalid_falls_back(self, app, client):
        uid, tid = _make_user(app, "renter_pay6@ex.com")
        owner_uid, _ = _make_user(app, "owner_pay6@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.get(f"/config/renters/{uid}/account?period=notanumber")
        assert r.status_code == 200
        r = client.get(f"/config/renters/{uid}/account?period=-3")
        assert r.status_code == 200

    def test_balance_reflects_charge_and_payment(self, app, client):
        uid, tid, acid, res_id = TestFinalize()._setup_draft(
            app, client, "renter_pay5@ex.com"
        )
        owner_uid, _ = _make_user(app, "owner_pay5@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        client.post(f"/config/renters/{uid}/account/payment", data={"amount": "100.00"})
        with app.app_context():
            account = BillingAccount.query.filter_by(
                tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
            ).one()
            assert BillingService.balance(account) == 200


# ── Renter account pages / permission matrix ──────────────────────────────────


class TestRenterAccountPages:
    def test_owner_can_view_any_renter_account(self, app, client):
        uid, tid = _make_user(app, "renter_acc1@ex.com")
        owner_uid, _ = _make_user(app, "owner_acc1@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        r = client.get(f"/config/renters/{uid}/account")
        assert r.status_code == 200

    def test_pilot_cannot_view_others_account_via_owner_route(self, app, client):
        uid, tid = _make_user(app, "renter_acc2@ex.com")
        other_uid, _ = _make_user(app, "other_acc2@ex.com")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=other_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, other_uid)
        r = client.get(f"/config/renters/{uid}/account")
        assert r.status_code == 403

    def test_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner_acc3@ex.com", role=Role.OWNER)
        other_uid, _ = _make_user(app, "renter_acc3@ex.com")
        _login(app, client, uid)
        r = client.get(f"/config/renters/{other_uid}/account")
        assert r.status_code == 404

    def test_renter_sees_own_account(self, app, client):
        uid, tid = _make_user(app, "renter_acc4@ex.com")
        _login(app, client, uid)
        r = client.get("/my/account")
        assert r.status_code == 200

    def test_my_account_csv_download(self, app, client):
        uid, tid = _make_user(app, "renter_acc5@ex.com")
        _login(app, client, uid)
        r = client.get("/my/account/statement.csv")
        assert r.status_code == 200
        assert r.mimetype == "text/csv"

    def test_navbar_link_hidden_without_entries(self, app, client):
        uid, tid = _make_user(app, "renter_acc6@ex.com")
        with app.app_context():
            from models import TenantProfile

            db.session.add(TenantProfile(tenant_id=tid, allows_rental=True))
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/my/account")
        assert r.status_code == 200
        home = client.get("/")
        assert b"My account" not in home.data

    def test_navbar_link_shown_with_entries(self, app, client):
        uid, tid, acid, res_id = TestFinalize()._setup_draft(
            app, client, "renter_acc7@ex.com"
        )
        owner_uid, _ = _make_user(app, "owner_acc7@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            from models import TenantProfile

            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            if profile is None:
                db.session.add(TenantProfile(tenant_id=tid, allows_rental=True))
            else:
                profile.allows_rental = True
            db.session.commit()
        _login(app, client, owner_uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        _login(app, client, uid)
        home = client.get("/")
        assert b"My account" in home.data


# ── Statement CSV ────────────────────────────────────────────────────────────────


class TestStatementCsv:
    def test_owner_csv_headers_and_totals(self, app, client):
        uid, tid, acid, res_id = TestFinalize()._setup_draft(
            app, client, "renter_csv1@ex.com"
        )
        owner_uid, _ = _make_user(app, "owner_csv1@ex.com", role=Role.OWNER)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=owner_uid, tenant_id=tid, role=Role.OWNER)
            )
            db.session.commit()
        _login(app, client, owner_uid)
        client.post(
            f"/aircraft/{acid}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "3.0",
                "hourly_rate": "100.0",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        )
        r = client.get(f"/config/renters/{uid}/account/statement.csv")
        assert r.status_code == 200
        text = r.data.decode()
        assert "Export date" in text
        assert "Exporter" in text
        assert "Period" in text
        assert "Account holder" in text
        assert "300.00" in text
        assert "Closing balance" in text

    def test_owner_csv_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner_csv2@ex.com", role=Role.OWNER)
        other_uid, _ = _make_user(app, "renter_csv2@ex.com")
        _login(app, client, uid)
        r = client.get(f"/config/renters/{other_uid}/account/statement.csv")
        assert r.status_code == 404
