"""Phase 39b: shared-ownership charge computation.

A single idempotent pass converts source records (co-owner buy-ins, fixed
Expense rows, and Flight PIC hours) into LedgerEntry rows on each
co-owner's aircraft-scoped BillingAccount, via the shared billing core
(services/billing.py, see docs/billing_service_design.md). Safe to run
any number of times — the drift-correction mechanism compares the
expected amount against what's currently posted and reverses/reposts
only when something changed.

See docs/phase39_shared_ownership_spec.md ("39b — Charge computation")
for the full design and worked examples.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, cast

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

if TYPE_CHECKING:
    from models import Aircraft, AircraftOwner, BillingAccount, LedgerEntry

TWO_PLACES = Decimal("0.01")


def _quantize(amount: Any) -> Decimal:
    return Decimal(str(amount)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _current_owners(aircraft_id: int) -> list["AircraftOwner"]:
    from models import AircraftOwner

    return cast(
        "list[AircraftOwner]",
        AircraftOwner.query.filter_by(aircraft_id=aircraft_id).all(),
    )


def _account_for(aircraft: "Aircraft", user_id: int) -> "BillingAccount":
    from models import BillingAccountKind
    from services.billing import BillingService

    return BillingService.get_or_create_account(
        aircraft.tenant_id,
        user_id,
        BillingAccountKind.CO_OWNER,
        aircraft_id=aircraft.id,
    )


def _live_entry(
    account_id: int, source_type: str, source_id: int
) -> "LedgerEntry | None":
    """The one entry for this source that is neither a reversal itself
    (reverses_id is NULL) nor already reversed (no other entry points at
    it via reverses_id). None if the source was never posted or its last
    posting was reversed."""
    from models import LedgerEntry

    candidates = (
        LedgerEntry.query.filter_by(
            account_id=account_id, source_type=source_type, source_id=source_id
        )
        .filter(LedgerEntry.reverses_id.is_(None))
        .order_by(LedgerEntry.id.desc())
        .all()
    )
    for entry in candidates:
        if LedgerEntry.query.filter_by(reverses_id=entry.id).first() is None:
            return cast("LedgerEntry", entry)
    return None


def _sync_entry(
    account: "BillingAccount",
    source_type: str,
    source_id: int,
    expected_amount: Decimal,
    entry_type: str,
    entry_date: date,
    description: str,
) -> None:
    """Post or refresh the one entry for this (account, source) so it
    matches expected_amount. Called only while the source is currently in
    scope and billable — reversing a source that's *left* scope is
    `_reverse_orphaned`'s job (the shared mechanism across all three
    sub-steps), not this function's."""
    from services.billing import BillingService

    live = _live_entry(account.id, source_type, source_id)

    if live is None:
        BillingService.post(
            account,
            entry_type,
            expected_amount,
            description,
            entry_date,
            source_type=source_type,
            source_id=source_id,
        )
        return

    if _quantize(live.amount) == _quantize(expected_amount):
        return

    BillingService.reverse(live, None, str(_("Source record changed")))
    BillingService.post(
        account,
        entry_type,
        expected_amount,
        description,
        entry_date,
        source_type=source_type,
        source_id=source_id,
    )


def _reverse_orphaned(
    aircraft: "Aircraft",
    owners: list["AircraftOwner"],
    source_type: str,
    expected_keys: set[tuple[int, int]],
) -> None:
    """Reverse any live entry of this source_type, on a current owner's
    account, whose (account_id, source_id) is no longer in expected_keys —
    the shared "left scope" mechanism for every sub-step (source deleted,
    re-categorised, PIC edited away, date edited out of range, rate/split
    change, ...). Accounts of departed owners are never touched, since
    `owners` only ever holds current AircraftOwner rows."""
    from models import LedgerEntry
    from services.billing import BillingService

    for owner in owners:
        account = _account_for(aircraft, owner.user_id)
        live_entries = (
            LedgerEntry.query.filter_by(account_id=account.id, source_type=source_type)
            .filter(LedgerEntry.reverses_id.is_(None))
            .all()
        )
        for entry in live_entries:
            if LedgerEntry.query.filter_by(reverses_id=entry.id).first() is not None:
                continue  # already reversed
            if (account.id, entry.source_id) not in expected_keys:
                BillingService.reverse(
                    entry, None, str(_("Source record removed or no longer billable"))
                )


def _expense_desc(expense: Any) -> str:
    from models import ExpenseType

    label = str(_(ExpenseType.LABELS.get(expense.expense_type, expense.expense_type)))
    if expense.description:
        return f"{label} — {expense.description}"
    return label


def _route_str(flight: Any) -> str:
    if flight.departure_icao and flight.arrival_icao:
        return f"{flight.departure_icao} → {flight.arrival_icao}"
    return ""


def _post_buy_ins(aircraft: "Aircraft", owners: list["AircraftOwner"]) -> None:
    from models import LedgerEntryType

    expected_keys: set[tuple[int, int]] = set()
    for owner in owners:
        account = _account_for(aircraft, owner.user_id)
        if owner.buy_in_amount and owner.buy_in_amount > 0:
            expected_keys.add((account.id, owner.id))
            description = str(_("Buy-in — %(pct)s%% share", pct=owner.share_pct))
            _sync_entry(
                account,
                "owner_buy_in",
                owner.id,
                -_quantize(owner.buy_in_amount),
                LedgerEntryType.OPENING,
                aircraft.co_owner_billing_start,
                description,
            )
    _reverse_orphaned(aircraft, owners, "owner_buy_in", expected_keys)


def _post_fixed_expense_shares(
    aircraft: "Aircraft", owners: list["AircraftOwner"]
) -> None:
    from models import Expense, ExpenseCategory, LedgerEntryType

    expected_keys: set[tuple[int, int]] = set()

    if owners:
        expenses = Expense.query.filter(
            Expense.aircraft_id == aircraft.id,
            Expense.expense_category == ExpenseCategory.FIXED,
            Expense.recurrence.is_(None),
            Expense.date >= aircraft.co_owner_billing_start,
        ).all()

        # Largest-share-residue rule: sort ascending by share_pct (ties
        # broken by user_id descending), every owner but the last gets
        # their exact proportional share quantized; the last (largest
        # share) absorbs the rounding residue so the split always sums
        # to the expense total exactly.
        ordered = sorted(owners, key=lambda o: (o.share_pct, -o.user_id))
        accounts = {o.id: _account_for(aircraft, o.user_id) for o in ordered}

        for expense in expenses:
            running_total = Decimal("0")
            for i, owner in enumerate(ordered):
                account = accounts[owner.id]
                if i < len(ordered) - 1:
                    share_amount = _quantize(
                        Decimal(expense.amount)
                        * Decimal(owner.share_pct)
                        / Decimal(100)
                    )
                    running_total += share_amount
                else:
                    share_amount = _quantize(Decimal(expense.amount) - running_total)
                expected_keys.add((account.id, expense.id))
                description = str(
                    _(
                        "Fixed cost share (%(pct)s%%) — %(desc)s",
                        pct=owner.share_pct,
                        desc=_expense_desc(expense),
                    )
                )
                _sync_entry(
                    account,
                    "expense_share",
                    expense.id,
                    share_amount,
                    LedgerEntryType.CHARGE,
                    expense.date,
                    description,
                )

    _reverse_orphaned(aircraft, owners, "expense_share", expected_keys)


def _post_flight_usage(aircraft: "Aircraft", owners: list["AircraftOwner"]) -> None:
    from models import Flight, LedgerEntryType, LogbookEntryType

    expected_keys: set[tuple[int, int]] = set()

    if aircraft.co_owner_hourly_rate is not None and owners:
        owner_by_user_id = {o.user_id: o for o in owners}
        flights = Flight.query.filter(
            Flight.aircraft_id == aircraft.id,
            Flight.entry_type == LogbookEntryType.FLIGHT,
            Flight.date >= aircraft.co_owner_billing_start,
            Flight.flight_time.isnot(None),
            Flight.flight_time > 0,
            Flight.pic_user_id.isnot(None),
        ).all()
        for flight in flights:
            owner = owner_by_user_id.get(flight.pic_user_id)
            if owner is None:
                continue  # unattributed hours — surfaced on the 39c dashboard, not billed
            account = _account_for(aircraft, owner.user_id)
            amount = _quantize(
                Decimal(flight.flight_time) * Decimal(aircraft.co_owner_hourly_rate)
            )
            expected_keys.add((account.id, flight.id))
            description = str(
                _(
                    "Flight %(date)s %(route)s — %(hours)s h",
                    date=flight.date.isoformat(),
                    route=_route_str(flight),
                    hours=flight.flight_time,
                )
            )
            _sync_entry(
                account,
                "flight_usage",
                flight.id,
                amount,
                LedgerEntryType.CHARGE,
                flight.date,
                description,
            )

    _reverse_orphaned(aircraft, owners, "flight_usage", expected_keys)


def run_co_owner_billing_pass(aircraft: "Aircraft") -> None:
    """Post/refresh all co-owner ledger entries for one aircraft. Caller
    owns the transaction (commit after calling)."""
    owners = _current_owners(aircraft.id)
    if aircraft.co_owner_billing_start is None:
        # Owners exist but no billing-start date somehow — treat as "no
        # billing" (post nothing), but still let any previously-posted
        # entries be reversed since nothing is in scope any more.
        _reverse_orphaned(aircraft, owners, "owner_buy_in", set())
        _reverse_orphaned(aircraft, owners, "expense_share", set())
        _reverse_orphaned(aircraft, owners, "flight_usage", set())
        return
    _post_buy_ins(aircraft, owners)
    _post_fixed_expense_shares(aircraft, owners)
    _post_flight_usage(aircraft, owners)


def run_co_owner_billing_pass_all(today: date | None = None) -> int:
    """Run the pass for every aircraft that has at least one AircraftOwner
    row. Returns the number of aircraft processed.

    Short-circuit rule: starts with a single cheap query for the distinct
    set of aircraft ids with owner rows, and returns 0 immediately when
    empty — on an instance that never uses shared ownership, this is the
    only work this function ever does (no AircraftOwner rows can exist
    there, since the manage-owners form — the sole write path — 404s for
    other operating models)."""
    from models import Aircraft, AircraftOwner, db

    del today  # accepted for interface symmetry with other daily-pass helpers

    aircraft_ids = [
        row[0] for row in db.session.query(AircraftOwner.aircraft_id).distinct().all()
    ]
    if not aircraft_ids:
        return 0

    count = 0
    for aircraft_id in aircraft_ids:
        aircraft = db.session.get(Aircraft, aircraft_id)
        if aircraft is None:  # pragma: no cover — defensive, FK guarantees it exists
            continue
        run_co_owner_billing_pass(aircraft)
        count += 1
    db.session.commit()
    return count


def overdue_since(account: "BillingAccount") -> date | None:
    """Date the capital balance last went negative (ledger balance went
    positive), or None if it is currently >= 0.

    Walks every entry (including reversals — they're ordinary entries that
    already net out correctly) in (entry_date, id) order, tracking the
    start of the current unbroken positive streak. On a
    negative-then-recover-then-negative history this naturally lands on
    the *latest* streak, since the candidate start date is cleared every
    time the running balance dips back to zero or below."""
    from models import LedgerEntry

    entries = (
        LedgerEntry.query.filter_by(account_id=account.id)
        .order_by(LedgerEntry.entry_date, LedgerEntry.id)
        .all()
    )
    running = Decimal("0")
    streak_start: date | None = None
    for entry in entries:
        prev = running
        running = _quantize(running + Decimal(entry.amount))
        if running > 0 and prev <= 0:
            streak_start = entry.entry_date
        elif running <= 0:
            streak_start = None
    return streak_start if running > 0 else None
