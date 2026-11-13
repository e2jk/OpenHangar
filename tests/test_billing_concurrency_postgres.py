"""Real-connection ledger races in an isolated PostgreSQL schema (audit C03).

Set TEST_POSTGRES_URL to a disposable PostgreSQL database (see
scripts/test-reservation-concurrency.sh, which runs this lane in CI). Each
test starts competing transactions on separate connections and checks that
exactly one intended effect reaches the ledger.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from models import (
    Aircraft,
    AircraftOwner,
    BillingAccount,
    BillingAccountKind,
    LedgerEntry,
    LedgerEntryType,
    RentalCharge,
    RentalChargeStatus,
    Reservation,
    ReservationStatus,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService

from tests.postgres_schema import postgres_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def _world(app: Flask) -> tuple[int, int, int]:
    with app.app_context():
        tenant = Tenant(name="Ledger races")
        owner = User(email="owner@example.test", password_hash="unused")
        renter = User(email="renter@example.test", password_hash="unused")
        db.session.add_all([tenant, owner, renter])
        db.session.flush()
        db.session.add_all(
            [
                TenantUser(tenant_id=tenant.id, user_id=owner.id, role=Role.OWNER),
                TenantUser(tenant_id=tenant.id, user_id=renter.id, role=Role.PILOT),
            ]
        )
        ac = Aircraft(tenant_id=tenant.id, registration="RACE", make="T", model="T")
        db.session.add(ac)
        db.session.commit()
        return owner.id, renter.id, ac.id


def _in_parallel(app: Flask, work, n: int = 2) -> list:
    barrier = Barrier(n, timeout=10)

    def run(i: int):
        with app.app_context():
            try:
                return work(i, barrier)
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result(timeout=30) for f in [pool.submit(run, i) for i in range(n)]]


def test_concurrent_reversals_post_exactly_one(app: Flask) -> None:
    owner_id, renter_id, _ = _world(app)
    with app.app_context():
        tenant_id = TenantUser.query.filter_by(user_id=owner_id).one().tenant_id
        account = BillingService.get_or_create_account(
            tenant_id, renter_id, BillingAccountKind.RENTER
        )
        entry = BillingService.post(
            account, LedgerEntryType.CHARGE, Decimal(100), "Rental", date.today()
        )
        db.session.commit()
        entry_id = entry.id

    def reverse(_i: int, barrier: Barrier) -> str:
        target = db.session.get(LedgerEntry, entry_id)
        assert LedgerEntry.query.filter_by(reverses_id=entry_id).first() is None
        barrier.wait()  # both passed the pre-check: only the index can decide
        try:
            BillingService.reverse(target, None, "Reversed")
            db.session.commit()
            return "reversed"
        except ValueError:
            db.session.rollback()
            return "refused"

    assert sorted(_in_parallel(app, reverse)) == ["refused", "reversed"]
    with app.app_context():
        assert LedgerEntry.query.filter_by(reverses_id=entry_id).count() == 1


def test_concurrent_finalizations_post_one_charge(app: Flask) -> None:
    owner_id, renter_id, ac_id = _world(app)
    with app.app_context():
        reservation = Reservation(
            aircraft_id=ac_id,
            pilot_user_id=renter_id,
            start_dt=datetime(2027, 1, 1, 10),
            end_dt=datetime(2027, 1, 1, 11),
            status=ReservationStatus.CONFIRMED,
        )
        db.session.add(reservation)
        db.session.flush()
        charge = RentalCharge(
            reservation_id=reservation.id,
            renter_user_id=renter_id,
            billable_hours=Decimal("1.0"),
            hourly_rate=Decimal(150),
            rate_type="wet",
            total=Decimal(150),
        )
        db.session.add(charge)
        db.session.commit()
        res_id, charge_id = reservation.id, charge.id

    def finalize(_i: int, barrier: Barrier) -> int:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = owner_id
        barrier.wait()
        return client.post(
            f"/aircraft/{ac_id}/reservations/{res_id}/charge",
            data={
                "action": "finalize",
                "billable_hours": "1.0",
                "hourly_rate": "150",
                "fuel_credit": "0",
                "adjustment": "0",
            },
        ).status_code

    for _ in range(3):
        with app.app_context():
            db.session.get(RentalCharge, charge_id).status = RentalChargeStatus.DRAFT
            LedgerEntry.query.delete()
            db.session.commit()
        assert _in_parallel(app, finalize) == [302, 302]
        with app.app_context():
            charges = LedgerEntry.query.filter_by(
                source_type="rental_charge", source_id=charge_id
            ).all()
            assert len(charges) == 1
            assert db.session.get(RentalCharge, charge_id).is_final


def test_concurrent_co_owner_passes_post_each_entry_once(app: Flask) -> None:
    from services.co_owner_billing import run_co_owner_billing_pass

    owner_id, renter_id, ac_id = _world(app)
    with app.app_context():
        ac = db.session.get(Aircraft, ac_id)
        ac.co_owner_billing_start = date(2026, 1, 1)
        db.session.add_all(
            [
                AircraftOwner(
                    aircraft_id=ac_id,
                    user_id=uid,
                    share_pct=Decimal(50),
                    buy_in_amount=Decimal(1000),
                )
                for uid in (owner_id, renter_id)
            ]
        )
        # Accounts exist already, so both passes go straight to posting.
        for uid in (owner_id, renter_id):
            BillingService.get_or_create_account(
                ac.tenant_id, uid, BillingAccountKind.CO_OWNER, aircraft_id=ac_id
            )
        db.session.commit()

    def billing_pass(_i: int, barrier: Barrier) -> None:
        aircraft = db.session.get(Aircraft, ac_id)
        barrier.wait()
        run_co_owner_billing_pass(aircraft)
        db.session.commit()

    _in_parallel(app, billing_pass)
    with app.app_context():
        assert BillingAccount.query.count() == 2
        assert LedgerEntry.query.filter_by(source_type="owner_buy_in").count() == 2


@pytest.mark.parametrize("_round", range(3))
def test_account_race_keeps_the_callers_pending_writes(app: Flask, _round: int) -> None:
    owner_id, renter_id, _ = _world(app)
    with app.app_context():
        tenant_id = TenantUser.query.filter_by(user_id=owner_id).one().tenant_id

    def create(i: int, barrier: Barrier) -> str:
        user = db.session.get(User, owner_id if i == 0 else renter_id)
        user.name = f"Pending write {i}"  # must survive a lost account race
        barrier.wait()
        account = BillingService.get_or_create_account(
            tenant_id, renter_id, BillingAccountKind.RENTER
        )
        db.session.commit()
        return str(account.id)

    ids = _in_parallel(app, create)
    assert len(set(ids)) == 1
    with app.app_context():
        assert BillingAccount.query.count() == 1
        assert db.session.get(User, owner_id).name == "Pending write 0"
        assert db.session.get(User, renter_id).name == "Pending write 1"
