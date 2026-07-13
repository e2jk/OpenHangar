"""
Tests for the shared billing core (Phase 37a): BillingAccount, LedgerEntry,
BillingService. See docs/billing_service_design.md for the design this
implements. No UI in this step — model/service tests only; tenant-isolation
enforcement at the route layer is tested when the first consuming route
(Phase 37e) lands.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest
from models import (  # pyright: ignore[reportMissingImports]
    BillingAccount,
    BillingAccountKind,
    LedgerEntry,
    LedgerEntryType,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from services.billing import BillingService  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="renter@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT)
        )
        db.session.commit()
        return user.id, tenant.id


class TestGetOrCreateAccount:
    def test_creates_account_on_first_call(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            assert account.id is not None
            assert account.tenant_id == tid
            assert account.user_id == uid
            assert account.kind == BillingAccountKind.RENTER
            assert account.aircraft_id is None
            assert account.currency == "EUR"

    def test_second_call_returns_same_account(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            first = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            first_id = first.id
            second = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            assert second.id == first_id
            assert BillingAccount.query.count() == 1

    def test_lazy_creation_idempotent_under_race(self, app, monkeypatch):
        """Two concurrent requests both find no existing row and both attempt
        to insert — the unique constraint must resolve this to exactly one
        account, not a crash. There's no real second connection to race
        against in a single-process test, so the service's own pre-check
        SELECT is monkeypatched to miss on its first call only (as if it ran
        a moment before a concurrent request's INSERT committed) — this
        drives get_or_create_account's own INSERT-then-IntegrityError-
        fallback branch for real, not by simulating it from the outside."""
        from sqlalchemy.orm import Query

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            # A row already exists, as if a concurrent request won the race.
            db.session.add(
                BillingAccount(
                    tenant_id=tid, user_id=uid, kind=BillingAccountKind.RENTER
                )
            )
            db.session.commit()

            real_filter_by = Query.filter_by
            calls = {"n": 0}

            class _EmptyResult:
                def first(self) -> None:
                    return None

            def flaky_filter_by(self: Query, *args: Any, **kwargs: Any) -> Any:
                calls["n"] += 1
                if calls["n"] == 1:
                    return _EmptyResult()
                return real_filter_by(self, *args, **kwargs)

            monkeypatch.setattr(Query, "filter_by", flaky_filter_by)

            resolved = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            assert BillingAccount.query.count() == 1
            assert resolved.tenant_id == tid

    def test_different_scope_creates_separate_account(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            renter_acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            member_acc = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.MEMBER
            )
            db.session.commit()
            assert renter_acc.id != member_acc.id


class TestPostSignValidation:
    def test_charge_must_be_positive(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            with pytest.raises(ValueError, match="CHARGE amount must be > 0"):
                BillingService.post(
                    account, LedgerEntryType.CHARGE, -10, "bad", date(2026, 7, 1)
                )
            with pytest.raises(ValueError, match="CHARGE amount must be > 0"):
                BillingService.post(
                    account, LedgerEntryType.CHARGE, 0, "bad", date(2026, 7, 1)
                )

    def test_payment_must_be_negative(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            with pytest.raises(ValueError, match="payment amount must be < 0"):
                BillingService.post(
                    account, LedgerEntryType.PAYMENT, 10, "bad", date(2026, 7, 1)
                )

    def test_credit_must_be_negative(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            with pytest.raises(ValueError, match="credit amount must be < 0"):
                BillingService.post(
                    account, LedgerEntryType.CREDIT, 10, "bad", date(2026, 7, 1)
                )

    def test_adjustment_requires_description(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            with pytest.raises(ValueError, match="non-empty description"):
                BillingService.post(
                    account, LedgerEntryType.ADJUSTMENT, 5, "", date(2026, 7, 1)
                )

    def test_adjustment_and_opening_allow_either_sign(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account,
                LedgerEntryType.ADJUSTMENT,
                -3.5,
                "correction",
                date(2026, 7, 1),
            )
            BillingService.post(
                account, LedgerEntryType.OPENING, 100, "buy-in", date(2026, 7, 1)
            )
            BillingService.post(
                account,
                LedgerEntryType.OPENING,
                -100,
                "buy-in reversed",
                date(2026, 7, 1),
            )
            db.session.commit()
            assert LedgerEntry.query.filter_by(account_id=account.id).count() == 3

    def test_unknown_entry_type_rejected(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            with pytest.raises(ValueError, match="Unknown entry_type"):
                BillingService.post(account, "not-a-type", 10, "x", date(2026, 7, 1))

    def test_amount_quantized_to_two_decimals_half_up(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            entry = BillingService.post(
                account, LedgerEntryType.CHARGE, 12.345, "x", date(2026, 7, 1)
            )
            db.session.commit()
            assert Decimal(entry.amount) == Decimal("12.35")


class TestBalance:
    def test_mixed_entries_sum_correctly(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight 1", date(2026, 7, 1)
            )
            BillingService.post(
                account, LedgerEntryType.CREDIT, -20, "fuel credit", date(2026, 7, 1)
            )
            BillingService.post(
                account, LedgerEntryType.PAYMENT, -100, "part payment", date(2026, 7, 5)
            )
            db.session.commit()
            assert BillingService.balance(account) == Decimal("80.00")

    def test_as_of_excludes_later_entries(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight 1", date(2026, 7, 1)
            )
            BillingService.post(
                account, LedgerEntryType.CHARGE, 150, "flight 2", date(2026, 7, 10)
            )
            db.session.commit()
            assert BillingService.balance(account, as_of=date(2026, 7, 5)) == Decimal(
                "200.00"
            )
            assert BillingService.balance(account, as_of=date(2026, 7, 10)) == Decimal(
                "350.00"
            )

    def test_no_entries_balance_is_zero(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            assert BillingService.balance(account) == Decimal("0.00")


class TestReversal:
    def test_reversal_nets_to_zero(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            charge = BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight", date(2026, 7, 1)
            )
            db.session.commit()
            reversal = BillingService.reverse(charge, None, "wrong rate applied")
            db.session.commit()
            assert reversal.reverses_id == charge.id
            assert reversal.entry_type == LedgerEntryType.CHARGE
            assert Decimal(reversal.amount) == Decimal("-200.00")
            assert BillingService.balance(account) == Decimal("0.00")

    def test_cannot_reverse_a_reversal(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            charge = BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight", date(2026, 7, 1)
            )
            db.session.commit()
            reversal = BillingService.reverse(charge, None, "correction")
            db.session.commit()
            with pytest.raises(ValueError, match="Cannot reverse a reversal"):
                BillingService.reverse(reversal, None, "double correction")

    def test_cannot_reverse_the_same_entry_twice(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            charge = BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight", date(2026, 7, 1)
            )
            db.session.commit()
            BillingService.reverse(charge, None, "first correction")
            db.session.commit()
            with pytest.raises(ValueError, match="already been reversed"):
                BillingService.reverse(charge, None, "second correction")

    def test_reversal_records_created_by(self, app):
        uid, tid = _create_user_and_tenant(app)
        admin_uid, _ = _create_user_and_tenant(app, "admin@example.com")
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            charge = BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight", date(2026, 7, 1)
            )
            db.session.commit()
            admin = db.session.get(User, admin_uid)
            reversal = BillingService.reverse(charge, admin, "correction")
            db.session.commit()
            assert reversal.created_by_id == admin_uid


class TestStatement:
    def test_opening_plus_entries_equals_closing(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account, LedgerEntryType.OPENING, 50, "opening", date(2026, 6, 1)
            )
            BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "flight 1", date(2026, 7, 5)
            )
            BillingService.post(
                account, LedgerEntryType.PAYMENT, -100, "payment", date(2026, 7, 10)
            )
            db.session.commit()
            stmt = BillingService.statement(
                account, date(2026, 7, 1), date(2026, 7, 31)
            )
            assert stmt.opening_balance == Decimal("50.00")
            assert len(stmt.lines) == 2
            assert stmt.closing_balance == Decimal("150.00")
            assert stmt.lines[-1].running_balance == stmt.closing_balance

    def test_entries_outside_period_excluded(self, app):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account, LedgerEntryType.CHARGE, 200, "before period", date(2026, 6, 1)
            )
            BillingService.post(
                account, LedgerEntryType.CHARGE, 50, "in period", date(2026, 7, 15)
            )
            BillingService.post(
                account, LedgerEntryType.CHARGE, 30, "after period", date(2026, 8, 1)
            )
            db.session.commit()
            stmt = BillingService.statement(
                account, date(2026, 7, 1), date(2026, 7, 31)
            )
            assert stmt.opening_balance == Decimal("200.00")
            assert len(stmt.lines) == 1
            assert stmt.lines[0].entry.description == "in period"
            assert stmt.closing_balance == Decimal("250.00")

    def test_csv_totals_match_and_header_metadata_present(self, app):
        uid, tid = _create_user_and_tenant(app, "renter2@example.com")
        with app.app_context():
            account = BillingService.get_or_create_account(
                tid, uid, BillingAccountKind.RENTER
            )
            db.session.commit()
            BillingService.post(
                account, LedgerEntryType.CHARGE, 131.25, "flight", date(2026, 7, 5)
            )
            db.session.commit()
            stmt = BillingService.statement(
                account, date(2026, 7, 1), date(2026, 7, 31)
            )
            exporter = db.session.get(User, uid)
            csv_text = BillingService.statement_csv(stmt, exported_by=exporter)
        assert "Export date" in csv_text
        assert "Exporter" in csv_text
        assert "Period" in csv_text
        assert "Account holder" in csv_text
        assert "Scope" in csv_text
        assert "131.25" in csv_text
        assert "Closing balance" in csv_text
        assert str(stmt.closing_balance) in csv_text
