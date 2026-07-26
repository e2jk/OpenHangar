"""
Tests for Phase 39b: the co-owner billing pass (app/services/co_owner_billing.py).

See docs/phase39_shared_ownership_spec.md ("39b — Charge computation").
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    BillingAccount,
    BillingAccountKind,
    Expense,
    ExpenseCategory,
    ExpenseType,
    Flight,
    LedgerEntry,
    LogbookEntryType,
    OperatingModel,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService  # pyright: ignore[reportMissingImports]
from services.co_owner_billing import (  # pyright: ignore[reportMissingImports]
    run_co_owner_billing_pass,
    run_co_owner_billing_pass_all,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant(app, name="Test Hangar"):
    with app.app_context():
        tenant = Tenant(name=name)
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantProfile(
                tenant_id=tenant.id,
                operating_model=OperatingModel.SHARED_OWNERSHIP,
                setup_complete=True,
            )
        )
        db.session.commit()
        return tenant.id


def _make_user(app, tenant_id, email, name, role=Role.OWNER):
    with app.app_context():
        user = User(
            email=email, password_hash=_pw_hash.hash("pw"), is_active=True, name=name
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant_id, role=role))
        db.session.commit()
        return user.id


def _make_aircraft(
    app,
    tenant_id,
    registration="OO-SHR",
    hourly_rate=None,
    billing_start=None,
):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            co_owner_hourly_rate=hourly_rate,
            co_owner_billing_start=billing_start,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_owner(app, aircraft_id, user_id, share_pct, buy_in=0):
    with app.app_context():
        o = AircraftOwner(
            aircraft_id=aircraft_id,
            user_id=user_id,
            share_pct=Decimal(str(share_pct)),
            buy_in_amount=Decimal(str(buy_in)),
        )
        db.session.add(o)
        db.session.commit()
        return o.id


def _add_expense(
    app,
    aircraft_id,
    amount,
    expense_date,
    category=ExpenseCategory.FIXED,
    recurrence=None,
    expense_type=ExpenseType.INSURANCE,
    description=None,
):
    with app.app_context():
        e = Expense(
            aircraft_id=aircraft_id,
            date=expense_date,
            expense_type=expense_type,
            expense_category=category,
            amount=Decimal(str(amount)),
            recurrence=recurrence,
            description=description,
        )
        db.session.add(e)
        db.session.commit()
        return e.id


def _add_flight(
    app,
    aircraft_id,
    pic_user_id,
    flight_date,
    flight_time,
    entry_type=LogbookEntryType.FLIGHT,
):
    with app.app_context():
        f = Flight(
            aircraft_id=aircraft_id,
            date=flight_date,
            departure_icao="EBOS",
            arrival_icao="EBBR",
            pic_user_id=pic_user_id,
            flight_time=flight_time,
            entry_type=entry_type,
        )
        db.session.add(f)
        db.session.commit()
        return f.id


def _account(app, tenant_id, user_id, aircraft_id):
    with app.app_context():
        return BillingAccount.query.filter_by(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=BillingAccountKind.CO_OWNER,
            aircraft_id=aircraft_id,
        ).first()


def _balance(app, tenant_id, user_id, aircraft_id):
    with app.app_context():
        acc = BillingAccount.query.filter_by(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=BillingAccountKind.CO_OWNER,
            aircraft_id=aircraft_id,
        ).first()
        if acc is None:
            return Decimal("0.00")
        return BillingService.balance(acc)


def _run_pass(app, aircraft_id):
    with app.app_context():
        ac = db.session.get(Aircraft, aircraft_id)
        run_co_owner_billing_pass(ac)
        db.session.commit()


def _live_entries(app, tenant_id, user_id, aircraft_id, source_type=None):
    with app.app_context():
        acc = BillingAccount.query.filter_by(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=BillingAccountKind.CO_OWNER,
            aircraft_id=aircraft_id,
        ).first()
        if acc is None:
            return []
        q = LedgerEntry.query.filter_by(account_id=acc.id).filter(
            LedgerEntry.reverses_id.is_(None)
        )
        if source_type:
            q = q.filter_by(source_type=source_type)
        entries = q.all()
        # only "live" (not themselves later reversed)
        live = []
        for e in entries:
            if LedgerEntry.query.filter_by(reverses_id=e.id).first() is None:
                live.append(e)
        return live


# ── Defensive: owners exist but billing_start is somehow NULL ────────────────


class TestNullBillingStart:
    def test_posts_nothing_and_reverses_anything_previously_posted(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100, buy_in=1000)

        _run_pass(app, acid)
        assert _balance(app, tid, uid, acid) == Decimal("-1000.00")

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.co_owner_billing_start = None
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("0.00")


# ── Buy-in ────────────────────────────────────────────────────────────────────


class TestBuyIn:
    def test_posts_one_negative_opening(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100, buy_in=1000)

        _run_pass(app, acid)

        entries = _live_entries(app, tid, uid, acid, "owner_buy_in")
        assert len(entries) == 1
        assert entries[0].amount == Decimal("-1000.00")

    def test_second_run_is_idempotent(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100, buy_in=1000)

        _run_pass(app, acid)
        _run_pass(app, acid)

        entries = _live_entries(app, tid, uid, acid, "owner_buy_in")
        assert len(entries) == 1

    def test_edited_buy_in_reverses_and_reposts(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        oid = _add_owner(app, acid, uid, 100, buy_in=1000)

        _run_pass(app, acid)
        with app.app_context():
            owner = db.session.get(AircraftOwner, oid)
            owner.buy_in_amount = Decimal("1500")
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("-1500.00")

    def test_zero_buy_in_posts_nothing(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100, buy_in=0)

        _run_pass(app, acid)

        entries = _live_entries(app, tid, uid, acid, "owner_buy_in")
        assert entries == []


# ── Fixed expense shares ──────────────────────────────────────────────────────


class TestFixedExpenseShares:
    def test_split_matches_share_and_sums_to_total(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        carol = _make_user(app, tid, "carol@example.com", "Carol")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, alice, "33.33")
        _add_owner(app, acid, bob, "33.33")
        _add_owner(app, acid, carol, "33.34")
        _add_expense(app, acid, "100.01", date(2026, 2, 1))

        _run_pass(app, acid)

        a1 = _live_entries(app, tid, alice, acid, "expense_share")[0].amount
        a2 = _live_entries(app, tid, bob, acid, "expense_share")[0].amount
        a3 = _live_entries(app, tid, carol, acid, "expense_share")[0].amount
        assert a1 == Decimal("33.33")
        assert a2 == Decimal("33.33")
        assert a3 == Decimal("33.35")
        assert a1 + a2 + a3 == Decimal("100.01")

    def test_description_includes_expense_description(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        _add_expense(
            app,
            acid,
            "100",
            date(2026, 2, 1),
            description="Annual premium renewal",
        )

        _run_pass(app, acid)

        entry = _live_entries(app, tid, uid, acid, "expense_share")[0]
        assert "Annual premium renewal" in entry.description

    def test_recurring_template_row_not_billed_children_are(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        _add_expense(app, acid, "1200", date(2026, 1, 1), recurrence="monthly")
        child_id = _add_expense(app, acid, "100", date(2026, 2, 1))

        _run_pass(app, acid)

        entries = _live_entries(app, tid, uid, acid, "expense_share")
        assert len(entries) == 1
        assert entries[0].source_id == child_id

    def test_expense_deleted_reverses_only(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        eid = _add_expense(app, acid, "100", date(2026, 2, 1))

        _run_pass(app, acid)
        assert _balance(app, tid, uid, acid) == Decimal("100.00")

        with app.app_context():
            db.session.delete(db.session.get(Expense, eid))
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("0.00")
        assert _live_entries(app, tid, uid, acid, "expense_share") == []

    def test_recategorised_to_operating_reverses(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, uid, 100)
        eid = _add_expense(app, acid, "100", date(2026, 2, 1))

        _run_pass(app, acid)
        assert _balance(app, tid, uid, acid) == Decimal("100.00")

        with app.app_context():
            e = db.session.get(Expense, eid)
            e.expense_category = ExpenseCategory.OPERATING
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("0.00")

    def test_date_before_billing_start_not_billed_then_drift_corrected(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 2, 1))
        _add_owner(app, acid, uid, 100)
        eid = _add_expense(app, acid, "100", date(2026, 1, 15))

        _run_pass(app, acid)
        assert _live_entries(app, tid, uid, acid, "expense_share") == []

        with app.app_context():
            e = db.session.get(Expense, eid)
            e.date = date(2026, 2, 15)
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("100.00")

    def test_owner_share_change_drift_corrects_split(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        alice_owner_id = _add_owner(app, acid, alice, 50)
        bob_owner_id = _add_owner(app, acid, bob, 50)
        _add_expense(app, acid, "100", date(2026, 2, 1))

        _run_pass(app, acid)
        assert _balance(app, tid, alice, acid) == Decimal("50.00")
        assert _balance(app, tid, bob, acid) == Decimal("50.00")

        with app.app_context():
            db.session.get(AircraftOwner, alice_owner_id).share_pct = Decimal("70")
            db.session.get(AircraftOwner, bob_owner_id).share_pct = Decimal("30")
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, alice, acid) == Decimal("70.00")
        assert _balance(app, tid, bob, acid) == Decimal("30.00")


# ── Flight usage ──────────────────────────────────────────────────────────────


class TestFlightUsage:
    def test_charge_equals_hours_times_rate(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="120.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(app, acid, uid, date(2026, 2, 1), "2.5")

        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("300.00")

    def test_description_omits_route_when_icao_fields_blank(self, app):
        """A standalone/rental-style flight with no ICAO fields (free-text
        place, or none at all) still gets billed — just without a route
        segment in the ledger description."""
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        with app.app_context():
            f = Flight(
                aircraft_id=acid,
                date=date(2026, 2, 1),
                pic_user_id=uid,
                flight_time=Decimal("1.0"),
                entry_type=LogbookEntryType.FLIGHT,
            )
            db.session.add(f)
            db.session.commit()

        _run_pass(app, acid)

        entry = _live_entries(app, tid, uid, acid, "flight_usage")[0]
        assert "2026-02-01" in entry.description
        assert "→" not in entry.description

    def test_non_owner_pic_not_billed(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        other = _make_user(app, tid, "dave@example.com", "Dave", role=Role.PILOT)
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(app, acid, other, date(2026, 2, 1), "1.0")

        _run_pass(app, acid)

        assert _live_entries(app, tid, uid, acid, "flight_usage") == []

    def test_null_rate_bills_nothing(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate=None, billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(app, acid, uid, date(2026, 2, 1), "1.0")

        _run_pass(app, acid)

        assert _live_entries(app, tid, uid, acid, "flight_usage") == []

    def test_zero_or_null_flight_time_not_billed(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(app, acid, uid, date(2026, 2, 1), None)
        _add_flight(app, acid, uid, date(2026, 2, 2), "0")

        _run_pass(app, acid)

        assert _live_entries(app, tid, uid, acid, "flight_usage") == []

    def test_fstd_entry_type_not_billed(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(
            app, acid, uid, date(2026, 2, 1), "1.0", entry_type=LogbookEntryType.FSTD
        )

        _run_pass(app, acid)

        assert _live_entries(app, tid, uid, acid, "flight_usage") == []

    def test_operating_attribution_not_shared_across_owners(self, app):
        """A's flight never appears on B's account."""
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, alice, 50)
        _add_owner(app, acid, bob, 50)
        _add_flight(app, acid, alice, date(2026, 2, 1), "2.0")

        _run_pass(app, acid)

        assert _balance(app, tid, alice, acid) == Decimal("200.00")
        assert _balance(app, tid, bob, acid) == Decimal("0.00")

    def test_rate_removed_reverses_all_flight_charges(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100)
        _add_flight(app, acid, uid, date(2026, 2, 1), "2.0")

        _run_pass(app, acid)
        assert _balance(app, tid, uid, acid) == Decimal("200.00")

        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            ac.co_owner_hourly_rate = None
            db.session.commit()
        _run_pass(app, acid)

        assert _balance(app, tid, uid, acid) == Decimal("0.00")


# ── Departed owner ────────────────────────────────────────────────────────────


class TestDepartedOwner:
    def test_departed_owner_untouched_no_new_charges(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        bob = _make_user(app, tid, "bob@example.com", "Bob")
        acid = _make_aircraft(app, tid, billing_start=date(2026, 1, 1))
        _add_owner(app, acid, alice, 50, buy_in=500)
        bob_owner_id = _add_owner(app, acid, bob, 50, buy_in=500)

        _run_pass(app, acid)
        assert _balance(app, tid, bob, acid) == Decimal("-500.00")

        with app.app_context():
            db.session.delete(db.session.get(AircraftOwner, bob_owner_id))
            db.session.commit()
        _run_pass(app, acid)

        # Bob's history/balance is untouched — no reversal posted against him.
        assert _balance(app, tid, bob, acid) == Decimal("-500.00")
        with app.app_context():
            acc = BillingAccount.query.filter_by(
                tenant_id=tid,
                user_id=bob,
                kind=BillingAccountKind.CO_OWNER,
                aircraft_id=acid,
            ).first()
            count_after = LedgerEntry.query.filter_by(account_id=acc.id).count()
        assert count_after == 1  # still just the original OPENING entry


# ── Capital arithmetic ────────────────────────────────────────────────────────


class TestCapitalArithmetic:
    def test_buy_in_plus_payments_minus_liabilities_equals_balance(self, app):
        tid = _make_tenant(app)
        uid = _make_user(app, tid, "alice@example.com", "Alice")
        acid = _make_aircraft(
            app, tid, hourly_rate="100.00", billing_start=date(2026, 1, 1)
        )
        _add_owner(app, acid, uid, 100, buy_in=1000)
        _add_expense(app, acid, "200", date(2026, 2, 1))
        _add_flight(app, acid, uid, date(2026, 2, 2), "1.0")

        _run_pass(app, acid)

        with app.app_context():
            from models import LedgerEntryType

            acc = BillingAccount.query.filter_by(
                tenant_id=tid,
                user_id=uid,
                kind=BillingAccountKind.CO_OWNER,
                aircraft_id=acid,
            ).first()
            recorder = None
            BillingService.post(
                acc,
                LedgerEntryType.PAYMENT,
                Decimal("-150"),
                "Payment",
                date(2026, 2, 5),
                source_type="payment",
                created_by=recorder,
            )
            db.session.commit()

        with app.app_context():
            acc = BillingAccount.query.filter_by(
                tenant_id=tid,
                user_id=uid,
                kind=BillingAccountKind.CO_OWNER,
                aircraft_id=acid,
            ).first()
            balance = BillingService.balance(acc)
            capital = -balance
            # buy-in(1000) + payments(150) - fixed(200) - operating(100) = 850
            assert capital == Decimal("850.00")


# ── Aircraft-level: run_co_owner_billing_pass_all / daily pass ───────────────


class TestRunAllAircraft:
    def test_no_trace_when_no_owner_rows(self, app):
        with app.app_context():
            before_accounts = BillingAccount.query.count()
            before_entries = LedgerEntry.query.count()
            result = run_co_owner_billing_pass_all()
            assert result == 0
            assert BillingAccount.query.count() == before_accounts
            assert LedgerEntry.query.count() == before_entries

    def test_processes_every_aircraft_with_owners(self, app):
        tid = _make_tenant(app)
        alice = _make_user(app, tid, "alice@example.com", "Alice")
        acid1 = _make_aircraft(app, tid, "OO-AA1", billing_start=date(2026, 1, 1))
        acid2 = _make_aircraft(app, tid, "OO-AA2", billing_start=date(2026, 1, 1))
        _add_owner(app, acid1, alice, 100, buy_in=100)
        _add_owner(app, acid2, alice, 100, buy_in=200)

        with app.app_context():
            result = run_co_owner_billing_pass_all()

        assert result == 2
        assert _balance(app, tid, alice, acid1) == Decimal("-100.00")
        assert _balance(app, tid, alice, acid2) == Decimal("-200.00")

    def test_run_daily_checks_includes_co_owner_billing_pass(self, app):
        with app.app_context():
            with (
                patch(
                    "services.co_owner_billing.run_co_owner_billing_pass_all"
                ) as mock_pass,
                patch("services.notification_service._check_maintenance"),
                patch("services.notification_service._check_insurance"),
                patch("services.notification_service._check_medical_and_sep"),
                patch("services.notification_service._check_documents"),
                patch("services.notification_service._check_airworthiness_reviews"),
                patch("services.notification_service._check_renter_authorizations"),
                patch("services.notification_service._check_personal_minimums_recency"),
                patch(
                    "services.recurring_expense_service.materialize_recurring_expenses"
                ),
            ):
                from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

                run_daily_checks(app)
                assert mock_pass.called
