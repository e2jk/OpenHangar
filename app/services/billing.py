"""Shared billing core — Phases 37 (rental), 38 (shared ownership), 39 (flying
club). See docs/billing_service_design.md for the full design rationale.

An account per person/scope, an append-only ledger of charges and payments,
a derived balance, and a period statement. All writes go through this
service; routes never insert LedgerEntry rows directly. Callers own the
transaction — nothing here commits.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from models import BillingAccount, LedgerEntry, User

TWO_PLACES = Decimal("0.01")


def _quantize(amount: Any) -> Decimal:
    return Decimal(str(amount)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class StatementLine:
    entry: "LedgerEntry"
    running_balance: Decimal


@dataclass
class Statement:
    account: "BillingAccount"
    start: date
    end: date
    opening_balance: Decimal
    closing_balance: Decimal
    lines: list[StatementLine] = field(default_factory=list)


class BillingService:
    @staticmethod
    def get_or_create_account(
        tenant_id: int, user_id: int, kind: str, aircraft_id: int | None = None
    ) -> "BillingAccount":
        """Return the existing account for this (tenant, user, kind, aircraft)
        scope, or create it. Idempotent under a concurrent-request race —
        the unique constraint is the source of truth, not a pre-check."""
        from sqlalchemy.exc import IntegrityError

        from models import BillingAccount, db

        existing: BillingAccount | None = BillingAccount.query.filter_by(
            tenant_id=tenant_id, user_id=user_id, kind=kind, aircraft_id=aircraft_id
        ).first()
        if existing is not None:
            return existing

        account = BillingAccount(
            tenant_id=tenant_id, user_id=user_id, kind=kind, aircraft_id=aircraft_id
        )
        db.session.add(account)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            existing = BillingAccount.query.filter_by(
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,
                aircraft_id=aircraft_id,
            ).first()
            if existing is None:  # pragma: no cover — defensive, race lost twice
                raise
            return existing
        return account

    @staticmethod
    def _insert(
        account: "BillingAccount",
        entry_type: str,
        amount: Any,
        description: str,
        entry_date: date,
        source_type: str | None = None,
        source_id: int | None = None,
        created_by: "User | None" = None,
        reverses_id: int | None = None,
    ) -> "LedgerEntry":
        from models import LedgerEntry, db

        entry = LedgerEntry(
            account_id=account.id,
            entry_type=entry_type,
            amount=_quantize(amount),
            description=description,
            entry_date=entry_date,
            source_type=source_type,
            source_id=source_id,
            reverses_id=reverses_id,
            created_by_id=created_by.id if created_by is not None else None,
        )
        db.session.add(entry)
        return entry

    @staticmethod
    def post(
        account: "BillingAccount",
        entry_type: str,
        amount: Any,
        description: str,
        entry_date: date,
        source_type: str | None = None,
        source_id: int | None = None,
        created_by: "User | None" = None,
    ) -> "LedgerEntry":
        """Validates sign against entry_type; commits nothing (caller owns
        the transaction)."""
        from models import LedgerEntryType

        if entry_type not in LedgerEntryType.ALL:
            raise ValueError(f"Unknown entry_type: {entry_type!r}")

        quantized = _quantize(amount)
        if entry_type == LedgerEntryType.CHARGE and quantized <= 0:
            raise ValueError("CHARGE amount must be > 0")
        if (
            entry_type in (LedgerEntryType.PAYMENT, LedgerEntryType.CREDIT)
            and quantized >= 0
        ):
            raise ValueError(f"{entry_type} amount must be < 0")
        if entry_type == LedgerEntryType.ADJUSTMENT and not description:
            raise ValueError("ADJUSTMENT requires a non-empty description")

        return BillingService._insert(
            account,
            entry_type,
            quantized,
            description,
            entry_date,
            source_type=source_type,
            source_id=source_id,
            created_by=created_by,
        )

    @staticmethod
    def reverse(
        entry: "LedgerEntry", created_by: "User | None", note: str
    ) -> "LedgerEntry":
        """Posts the mirror entry (same type, opposite amount) with
        reverses_id set. Refuses to reverse a reversal and refuses to
        reverse the same entry twice."""
        from models import LedgerEntry

        if entry.reverses_id is not None:
            raise ValueError("Cannot reverse a reversal entry")
        already_reversed = LedgerEntry.query.filter_by(reverses_id=entry.id).first()
        if already_reversed is not None:
            raise ValueError("This entry has already been reversed")

        return BillingService._insert(
            cast("BillingAccount", entry.account),
            entry.entry_type,
            -Decimal(entry.amount),
            note,
            entry.entry_date,
            source_type=entry.source_type,
            source_id=entry.source_id,
            created_by=created_by,
            reverses_id=entry.id,
        )

    @staticmethod
    def balance(account: "BillingAccount", as_of: date | None = None) -> Decimal:
        from models import LedgerEntry, db

        query = db.session.query(db.func.sum(LedgerEntry.amount)).filter(
            LedgerEntry.account_id == account.id
        )
        if as_of is not None:
            query = query.filter(LedgerEntry.entry_date <= as_of)
        total = query.scalar()
        return _quantize(total) if total is not None else Decimal("0.00")

    @staticmethod
    def statement(account: "BillingAccount", start: date, end: date) -> Statement:
        """Opening balance (sum of entries before start), chronological
        entries in [start, end], closing balance."""
        from models import LedgerEntry

        opening = BillingService.balance(account, as_of=start - timedelta(days=1))
        entries = (
            LedgerEntry.query.filter(
                LedgerEntry.account_id == account.id,
                LedgerEntry.entry_date >= start,
                LedgerEntry.entry_date <= end,
            )
            .order_by(LedgerEntry.entry_date, LedgerEntry.id)
            .all()
        )
        running = opening
        lines = []
        for entry in entries:
            running = _quantize(running + Decimal(entry.amount))
            lines.append(StatementLine(entry=entry, running_balance=running))
        return Statement(
            account=account,
            start=start,
            end=end,
            opening_balance=opening,
            closing_balance=running,
            lines=lines,
        )

    @staticmethod
    def statement_csv(statement: Statement, exported_by: "User | None" = None) -> str:
        """Header rows: export date, exporter, period, account holder, scope.
        Then one row per entry: date, type, description, amount, running
        balance."""
        from datetime import datetime, timezone

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Export date", datetime.now(timezone.utc).date().isoformat()])
        writer.writerow(["Exporter", exported_by.display_name if exported_by else ""])
        writer.writerow(
            ["Period", f"{statement.start.isoformat()} to {statement.end.isoformat()}"]
        )
        account = statement.account
        writer.writerow(
            ["Account holder", account.user.display_name if account.user else ""]
        )
        writer.writerow(["Scope", account.kind])
        writer.writerow([])
        writer.writerow(["Opening balance", "", "", "", str(statement.opening_balance)])
        writer.writerow(["Date", "Type", "Description", "Amount", "Running balance"])
        for line in statement.lines:
            writer.writerow(
                [
                    line.entry.entry_date.isoformat(),
                    line.entry.entry_type,
                    line.entry.description,
                    str(line.entry.amount),
                    str(line.running_balance),
                ]
            )
        writer.writerow(["Closing balance", "", "", "", str(statement.closing_balance)])
        return buf.getvalue()
