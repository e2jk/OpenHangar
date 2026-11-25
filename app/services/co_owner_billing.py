"""Phase 39b: shared-ownership charge computation.

A single idempotent pass converts source records (co-owner buy-ins, fixed
Expense rows, and Flight PIC hours) into LedgerEntry rows on each
co-owner's aircraft-scoped BillingAccount, via the shared billing core
(services/billing.py, see docs/billing_service_design.md). Safe to run
any number of times — the drift-correction mechanism compares the
expected amount against what's currently posted and reverses/reposts
only when something changed.

See docs/implementation_plan.md, Phase 39 ("Shared Ownership"), for the
full design.
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


def _current_owners(aircraft_id: int) -> list[AircraftOwner]:
    from models import AircraftOwner

    return cast(
        "list[AircraftOwner]",
        AircraftOwner.query.filter_by(aircraft_id=aircraft_id).all(),
    )


def _account_for(aircraft: Aircraft, user_id: int) -> BillingAccount:
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
) -> LedgerEntry | None:
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
    account: BillingAccount,
    source_type: str,
    source_id: int,
    expected_amount: Decimal,
    entry_type: str,
    entry_date: date,
    description: str,
    *,
    hours: Decimal | None = None,
    rate: Decimal | None = None,
) -> None:
    """Post or refresh the one entry for this (account, source) so it
    matches expected_amount.

    An hourly charge (`hours` and `rate` given) keeps the rate it was first
    billed at: a later rate change applies to flights billed after it, never
    to ones already billed (only a change in the flight's hours re-prices
    it, at its own rate). Called only while the source is currently in
    scope and billable — reversing a source that's *left* scope is
    `_reverse_orphaned`'s job (the shared mechanism across all three
    sub-steps), not this function's."""
    from services.billing import BillingService

    live = _live_entry(account.id, source_type, source_id)
    if live is not None and hours is not None and live.unit_rate is not None:
        rate = Decimal(live.unit_rate)  # billed at its own rate, kept
        expected_amount = _quantize(hours * rate)
    if live is not None:
        if _quantize(live.amount) == _quantize(expected_amount):
            return
        BillingService.reverse(live, None, str(_("Source record changed")))
    entry = BillingService.post(
        account,
        entry_type,
        expected_amount,
        description,
        entry_date,
        source_type=source_type,
        source_id=source_id,
    )
    entry.unit_rate = rate


def _reverse_orphaned(
    aircraft: Aircraft,
    owners: list[AircraftOwner],
    source_type: str,
    expected_keys: set[tuple[int, int]],
    *,
    all_accounts: bool = False,
) -> None:
    """Reverse any live entry of this source_type, on a current owner's
    account, whose (account_id, source_id) is no longer in expected_keys —
    the shared "left scope" mechanism for every sub-step (source deleted,
    re-categorised, PIC edited away, date edited out of range, rate/split
    change, ...). Accounts of departed owners are never touched, since
    `owners` only ever holds current AircraftOwner rows."""
    from models import LedgerEntry

    from services.billing import BillingService

    # Split costs are kept on departed owners' accounts too (all_accounts):
    # one that leaves scope is reversed there as well.
    accounts = (
        _aircraft_accounts(aircraft)
        if all_accounts
        else [_account_for(aircraft, owner.user_id) for owner in owners]
    )
    for account in accounts:
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


def _aircraft_accounts(aircraft: Aircraft) -> list[BillingAccount]:
    """Every co-owner account of this aircraft — departed owners' included."""
    from models import BillingAccount, BillingAccountKind

    return cast(
        "list[BillingAccount]",
        BillingAccount.query.filter_by(
            tenant_id=aircraft.tenant_id,
            kind=BillingAccountKind.CO_OWNER,
            aircraft_id=aircraft.id,
        ).all(),
    )


def _split_by_share(
    total: Decimal, weights: list[tuple[Any, Decimal]]
) -> list[tuple[Any, Decimal]]:
    """`total` split in proportion to the weights; the rounding residue goes
    to the last (largest-weight) item, so the parts always add up."""
    ordered = sorted(weights, key=lambda w: w[1])
    weight_sum = sum((w for _k, w in ordered), Decimal(0))
    parts: list[tuple[Any, Decimal]] = []
    running = Decimal(0)
    for i, (key, weight) in enumerate(ordered):
        if i < len(ordered) - 1:
            part = _quantize(total * weight / weight_sum)
            running += part
        else:
            part = _quantize(total - running)
        parts.append((key, part))
    return parts


def _sync_split(
    aircraft: Aircraft,
    owners: list[AircraftOwner],
    source_type: str,
    source_id: int,
    total: Decimal,
    entry_date: date,
    describe: Any,
    expected_keys: set[tuple[int, int]],
) -> None:
    """Split a shared cost (fixed expense, monthly reserve) once, then keep
    that split.

    The first pass splits `total` among the owners of the day by share. Later
    passes never re-split it when owners or shares change (a share sold in
    June does not re-bill February); if the total itself changes, or the
    split no longer adds up to it (re-splits by earlier versions billed
    more than 100% after an ownership change), the posted parts are
    rescaled in their own proportions on the same accounts — departed
    owners' included — with visible reversal and correction entries."""
    from models import LedgerEntryType

    from services.billing import BillingService

    total = _quantize(total)
    live = [
        entry
        for account in _aircraft_accounts(aircraft)
        if (entry := _live_entry(account.id, source_type, source_id)) is not None
    ]
    if not live:
        by_owner = {o.id: o for o in owners}
        for owner_id, amount in _split_by_share(
            total, [(o.id, Decimal(o.share_pct)) for o in owners]
        ):
            owner = by_owner[owner_id]
            account = _account_for(aircraft, owner.user_id)
            expected_keys.add((account.id, source_id))
            if amount > 0:
                BillingService.post(
                    account,
                    LedgerEntryType.CHARGE,
                    amount,
                    describe(owner.share_pct),
                    entry_date,
                    source_type=source_type,
                    source_id=source_id,
                )
        return
    expected_keys.update((entry.account_id, source_id) for entry in live)
    if sum((Decimal(e.amount) for e in live), Decimal(0)) == total:
        return
    by_id = {entry.id: entry for entry in live}
    for entry_id, amount in _split_by_share(
        total, [(e.id, Decimal(e.amount)) for e in live]
    ):
        entry = by_id[entry_id]
        BillingService.reverse(entry, None, str(_("Source record changed")))
        if amount > 0:
            BillingService.post(
                cast("BillingAccount", entry.account),
                LedgerEntryType.CHARGE,
                amount,
                entry.description,
                entry_date,
                source_type=source_type,
                source_id=source_id,
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


def _post_buy_ins(aircraft: Aircraft, owners: list[AircraftOwner]) -> None:
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


def _post_fixed_expense_shares(aircraft: Aircraft, owners: list[AircraftOwner]) -> None:
    from models import Expense, ExpenseCategory

    expected_keys: set[tuple[int, int]] = set()
    if owners:
        # The recurring template row is the series' first occurrence (its
        # generated rows start one interval later): billed like any other.
        expenses = Expense.query.filter(
            Expense.aircraft_id == aircraft.id,
            Expense.expense_category == ExpenseCategory.FIXED,
            Expense.date >= aircraft.co_owner_billing_start,
        ).all()
        for expense in expenses:

            def describe(pct: Any, expense: Any = expense) -> str:
                return str(
                    _(
                        "Fixed cost share (%(pct)s%%) — %(desc)s",
                        pct=pct,
                        desc=_expense_desc(expense),
                    )
                )

            _sync_split(
                aircraft,
                owners,
                "expense_share",
                expense.id,
                Decimal(expense.amount),
                expense.date,
                describe,
                expected_keys,
            )
    _reverse_orphaned(
        aircraft, owners, "expense_share", expected_keys, all_accounts=True
    )


def _post_flight_usage(aircraft: Aircraft, owners: list[AircraftOwner]) -> None:
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
                hours=Decimal(flight.flight_time),
                rate=Decimal(aircraft.co_owner_hourly_rate),
            )

    _reverse_orphaned(aircraft, owners, "flight_usage", expected_keys)


def _post_reserve_contributions(
    aircraft: Aircraft, owners: list[AircraftOwner]
) -> None:
    """Phase 39g (stretch): reserve/overhaul fund contributions. Exactly
    one of the two rate fields is ever set (validated on the owners form)
    — hourly mode piggybacks on the flight-usage source set (one charge
    per flight, to the PIC); monthly mode posts one charge per owner per
    calendar month from billing-start to the current month, split by
    share % with the same largest-share-residue rule as fixed expenses."""
    from models import Flight, LedgerEntryType, LogbookEntryType

    expected_keys: set[tuple[int, int]] = set()

    if aircraft.reserve_contribution_hourly is not None and owners:
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
                continue
            account = _account_for(aircraft, owner.user_id)
            amount = _quantize(
                Decimal(flight.flight_time)
                * Decimal(aircraft.reserve_contribution_hourly)
            )
            expected_keys.add((account.id, flight.id))
            description = str(
                _(
                    "Reserve fund contribution — flight %(date)s (%(hours)s h)",
                    date=flight.date.isoformat(),
                    hours=flight.flight_time,
                )
            )
            _sync_entry(
                account,
                "reserve_contribution",
                flight.id,
                amount,
                LedgerEntryType.CHARGE,
                flight.date,
                description,
                hours=Decimal(flight.flight_time),
                rate=Decimal(aircraft.reserve_contribution_hourly),
            )

    elif aircraft.reserve_contribution_monthly is not None and owners:
        today = date.today()
        year, month = (
            aircraft.co_owner_billing_start.year,
            aircraft.co_owner_billing_start.month,
        )
        while (year, month) <= (today.year, today.month):
            label = f"{year:04d}-{month:02d}"

            def describe(pct: Any, label: str = label) -> str:
                return str(
                    _(
                        "Reserve fund contribution (%(pct)s%%) — %(month)s",
                        pct=pct,
                        month=label,
                    )
                )

            _sync_split(
                aircraft,
                owners,
                "reserve_contribution",
                year * 100 + month,
                Decimal(aircraft.reserve_contribution_monthly),
                date(year, month, 1),
                describe,
                expected_keys,
            )
            month += 1
            if month > 12:
                month = 1
                year += 1

    _reverse_orphaned(aircraft, owners, "reserve_contribution", expected_keys)


def reserve_fund_balance(aircraft: Aircraft) -> Decimal:
    """Sum of all *live* reserve_contribution charges across this
    aircraft's co-owner accounts — a contribution is money owed *into*
    the fund, so it accumulates here regardless of which owner it was
    charged to. Spending the fund is out of scope for this phase."""
    from models import BillingAccount, BillingAccountKind, LedgerEntry, db
    from sqlalchemy.orm import aliased

    reversal = aliased(LedgerEntry)
    has_reversal = (
        db.session.query(reversal.id)
        .filter(reversal.reverses_id == LedgerEntry.id)
        .exists()
    )
    total = (
        db.session.query(db.func.sum(LedgerEntry.amount))
        .join(BillingAccount, BillingAccount.id == LedgerEntry.account_id)
        .filter(
            BillingAccount.tenant_id == aircraft.tenant_id,
            BillingAccount.kind == BillingAccountKind.CO_OWNER,
            BillingAccount.aircraft_id == aircraft.id,
            LedgerEntry.source_type == "reserve_contribution",
            LedgerEntry.reverses_id.is_(None),
            ~has_reversal,
        )
        .scalar()
    )
    return _quantize(total or Decimal(0))


def run_co_owner_billing_pass(aircraft: Aircraft) -> None:
    """Post/refresh all co-owner ledger entries for one aircraft. Caller
    owns the transaction (commit after calling).

    Every view of the billing page runs this pass, so two can run at once.
    The aircraft row is locked until the caller commits: a second pass waits,
    then finds the first one's entries instead of posting them again."""
    from models import Aircraft, db

    db.session.query(Aircraft.id).filter_by(id=aircraft.id).with_for_update().one()
    owners = _current_owners(aircraft.id)
    if aircraft.co_owner_billing_start is None:
        # Owners exist but no billing-start date somehow — treat as "no
        # billing" (post nothing), but still let any previously-posted
        # entries be reversed since nothing is in scope any more.
        _reverse_orphaned(aircraft, owners, "owner_buy_in", set())
        _reverse_orphaned(aircraft, owners, "expense_share", set())
        _reverse_orphaned(aircraft, owners, "flight_usage", set())
        _reverse_orphaned(aircraft, owners, "reserve_contribution", set())
        return
    _post_buy_ins(aircraft, owners)
    _post_fixed_expense_shares(aircraft, owners)
    _post_flight_usage(aircraft, owners)
    _post_reserve_contributions(aircraft, owners)


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


def overdue_since(account: BillingAccount) -> date | None:
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
    running = Decimal(0)
    streak_start: date | None = None
    for entry in entries:
        prev = running
        running = _quantize(running + Decimal(entry.amount))
        if running > 0 and prev <= 0:
            streak_start = entry.entry_date
        elif running <= 0:
            streak_start = None
    return streak_start if running > 0 else None
