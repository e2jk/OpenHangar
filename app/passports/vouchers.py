"""Explicit voucher accounting; arrivals and stamps never consume an allocation.

Callers commit. Parent-then-pool row locks serialize allocation edits, redemptions,
corrections and undo across workers. Personal ledgers remain participant-owned.
"""

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from flask_babel import gettext as _
from models import (
    Flight,
    Passport,
    PassportAirfield,
    PassportParticipant,
    TenantUser,
    db,
)
from models import PassportVoucherAllocation as Allocation
from models import PassportVoucherRedemption as Redemption
from utils import int_in

from passports.eligibility import date_eligible, eligible_flight
from passports.evidence import writable_participant
from passports.management import BuilderError, owned_passport
from passports.participation import active_participation

MAX_ALLOCATION_COPIES = 5000

allocation_quantity = int_in(0, 100000)
redemption_quantity = int_in(1, 100000)


def _day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError as exc:
        raise BuilderError(_("Enter valid voucher dates.")) from exc


def _quantity(raw: str, *, redemption: bool = False) -> int:
    try:
        return (redemption_quantity if redemption else allocation_quantity)(raw)
    except ValueError as exc:
        raise BuilderError(
            _(
                "Enter a whole voucher quantity between 0 and 100,000 (at least 1 for a redemption)."
            )
        ) from exc


def used(allocation: Allocation, *, excluding: int | None = None) -> int:
    query = db.session.query(
        db.func.coalesce(db.func.sum(Redemption.quantity), 0)
    ).filter(Redemption.allocation_id == allocation.id, Redemption.voided_at.is_(None))
    if excluding is not None:
        query = query.filter(Redemption.id != excluding)
    return int(query.scalar())


def _bounds(passport: Passport, first: date | None, last: date | None) -> None:
    if (first and last and first > last) or any(
        day is not None
        and (
            (passport.start_date and day < passport.start_date)
            or (passport.end_date and day > passport.end_date)
        )
        for day in (first, last)
    ):
        raise BuilderError(_("Voucher validity must stay within the edition dates."))


def configure(
    passport: Passport,
    user_id: int,
    target_id: int,
    values: Any,
    personal_user_id: int | None,
) -> Allocation:
    passport = owned_passport(passport.tenant_id, user_id, passport.id)
    if (
        PassportAirfield.query.filter_by(id=target_id, passport_id=passport.id).first()
        is None
    ):
        raise BuilderError(_("Airfield not found."), 404)
    if (
        personal_user_id is not None
        and db.session.get(TenantUser, (personal_user_id, passport.tenant_id)) is None
    ):
        raise BuilderError(_("Choose a participant from this organisation."), 404)
    quantity = _quantity(values.get("quantity", ""))
    first, last = (
        _day(values.get("valid_from", "")),
        _day(values.get("valid_until", "")),
    )
    _bounds(passport, first, last)
    allocation: Allocation | None = (
        Allocation.query.filter_by(
            passport_id=passport.id,
            target_id=target_id,
            personal_user_id=personal_user_id,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if allocation is None:
        allocation = Allocation(
            passport_id=passport.id,
            target_id=target_id,
            personal_user_id=personal_user_id,
        )
        db.session.add(allocation)
    else:
        if quantity < used(allocation):
            raise BuilderError(
                _("The allocation cannot be smaller than its recorded use."), 409
            )
        for redemption in Redemption.query.filter_by(allocation_id=allocation.id).all():
            if (first and redemption.redemption_date < first) or (
                last and redemption.redemption_date > last
            ):
                raise BuilderError(
                    _("Voucher validity cannot exclude saved redemption history."), 409
                )
    allocation.quantity, allocation.valid_from, allocation.valid_until = (
        quantity,
        first,
        last,
    )
    return allocation


def _context(passport: Passport, user_id: int) -> tuple[Passport, PassportParticipant]:
    locked = (
        Passport.query.filter_by(id=passport.id, tenant_id=passport.tenant_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if locked is None:
        raise BuilderError(_("Passport not found."), 404)
    return locked, writable_participant(locked, user_id)


def _pool(passport: Passport, user_id: int, allocation_id: int) -> Allocation:
    allocation: Allocation | None = (
        Allocation.query.filter(
            Allocation.id == allocation_id,
            Allocation.passport_id == passport.id,
            db.or_(
                Allocation.personal_user_id == user_id,
                Allocation.personal_user_id.is_(None),
            ),
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if allocation is None:
        raise BuilderError(_("Voucher allocation not found."), 404)
    return allocation


def _saving(values: Any) -> tuple[Decimal | None, str | None]:
    raw, currency = (
        values.get("saving_amount", "").strip(),
        values.get("currency", "").strip().upper(),
    )
    if not raw and not currency:
        return None, None
    try:
        if len(raw) > 40:
            raise ValueError
        amount = Decimal(raw)
        if (
            not amount.is_finite()
            or not 0 <= amount <= Decimal("9999999999.99")
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise ValueError
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError
    except (ValueError, InvalidOperation) as exc:
        raise BuilderError(
            _(
                "Enter a non-negative saving with at most two decimals and a three-letter currency, or leave both empty."
            )
        ) from exc
    return amount, currency


def _flight(user_id: int, day: date, raw: str) -> int | None:
    if not raw:
        return None
    from utils import db_id

    try:
        flight_id = db_id(raw)
    except ValueError as exc:
        raise BuilderError(
            _("Choose a flight from your own logbook on the visit date.")
        ) from exc
    flight = db.session.get(Flight, flight_id)
    if (
        flight is None
        or user_id not in (flight.pic_user_id, flight.second_crew_user_id)
        or flight.date != day
        or not eligible_flight(flight)
    ):
        raise BuilderError(
            _("Choose a flight from your own logbook on the visit date.")
        )
    return flight_id


def redeem(
    passport: Passport,
    user_id: int,
    allocation_id: int,
    values: Any,
    *,
    replaces_id: int | None = None,
) -> Redemption:
    passport, participant = _context(passport, user_id)
    allocation = _pool(passport, user_id, allocation_id)
    previous = None
    if replaces_id is not None:
        previous = (
            Redemption.query.filter_by(
                id=replaces_id,
                passport_id=passport.id,
                participant_id=participant.id,
                allocation_id=allocation.id,
            )
            .with_for_update()
            .populate_existing()
            .first()
        )
        if previous is None:
            raise BuilderError(_("Voucher redemption not found."), 404)
        if previous.voided_at is not None:
            raise BuilderError(
                _("This redemption was already corrected or undone."), 409
            )
    key = values.get("request_key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", key):
        raise BuilderError(_("Reload the voucher form before submitting."))
    if Redemption.query.filter_by(request_key=key).first() is not None:
        raise BuilderError(
            _(
                "This voucher use was already saved. Reload the page to record another use."
            ),
            409,
        )
    day = _day(values.get("redemption_date", ""))
    if (
        day is None
        or not date_eligible(passport, day)
        or (allocation.valid_from and day < allocation.valid_from)
        or (allocation.valid_until and day > allocation.valid_until)
    ):
        raise BuilderError(
            _(
                "The redemption must be within the voucher validity and not in the future."
            )
        )
    quantity = _quantity(values.get("quantity", ""), redemption=True)
    if quantity > allocation.quantity - used(allocation, excluding=replaces_id):
        raise BuilderError(_("There are not enough vouchers remaining."), 409)
    note = values.get("note", "").strip()
    if len(note) > 8000:
        raise BuilderError(_("The voucher note is too long."))
    saving, currency = _saving(values)
    flight_id = _flight(user_id, day, values.get("supporting_flight", "").strip())
    redemption = Redemption(
        passport_id=passport.id,
        allocation_id=allocation.id,
        participant_id=participant.id,
        redemption_date=day,
        quantity=quantity,
        flight_id=flight_id,
        saving_amount=saving,
        currency=currency,
        note=note or None,
        request_key=key,
        replaces_id=replaces_id,
    )
    if previous is not None:
        previous.voided_at = datetime.now(UTC)
    db.session.add(redemption)
    return redemption


def undo(passport: Passport, user_id: int, redemption_id: int) -> None:
    passport, participant = _context(passport, user_id)
    redemption = (
        Redemption.query.filter_by(
            id=redemption_id, passport_id=passport.id, participant_id=participant.id
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if redemption is None:
        raise BuilderError(_("Voucher redemption not found."), 404)
    _pool(passport, user_id, redemption.allocation_id)
    if redemption.voided_at is None:
        redemption.voided_at = datetime.now(UTC)


def validate_dates(passport: Passport, start: date | None, end: date | None) -> None:
    for allocation in Allocation.query.filter_by(passport_id=passport.id).all():
        for day in (allocation.valid_from, allocation.valid_until):
            if day and ((start and day < start) or (end and day > end)):
                raise BuilderError(
                    _(
                        "These edition dates would exclude saved voucher validity. Create a new edition instead."
                    ),
                    409,
                )
    for redemption in Redemption.query.filter_by(passport_id=passport.id).all():
        if (start and redemption.redemption_date < start) or (
            end and redemption.redemption_date > end
        ):
            raise BuilderError(
                _(
                    "These edition dates would exclude saved redemptions. Create a new edition instead."
                ),
                409,
            )


def protected_targets(passport_id: int, target_ids: set[int]) -> set[int]:
    return {
        row.target_id
        for row in Allocation.query.filter(
            Allocation.passport_id == passport_id, Allocation.target_id.in_(target_ids)
        ).all()
    }


def ledger(passport: Passport, user_id: int) -> dict[str, Any]:
    """Visible balances plus *only this participant's* private redemption events."""
    from passports.eligibility import participant_for

    participant = participant_for(passport.id, user_id)
    if participant is None:
        return {"pools": [], "redemptions": [], "savings": {}}
    pools = (
        Allocation.query.filter(
            Allocation.passport_id == passport.id,
            db.or_(
                Allocation.personal_user_id == user_id,
                Allocation.personal_user_id.is_(None),
            ),
        )
        .order_by(Allocation.target_id, Allocation.id)
        .all()
    )
    counts = dict(
        db.session.query(Redemption.allocation_id, db.func.sum(Redemption.quantity))
        .filter(
            Redemption.allocation_id.in_([pool.id for pool in pools]),
            Redemption.voided_at.is_(None),
        )
        .group_by(Redemption.allocation_id)
        .all()
    )
    rows = (
        Redemption.query.filter_by(
            passport_id=passport.id, participant_id=participant.id
        )
        .order_by(Redemption.redemption_date.desc(), Redemption.id.desc())
        .all()
    )
    savings: dict[str, Decimal] = {}
    for row in rows:
        if row.voided_at is None and row.currency is not None:
            savings[row.currency] = (
                savings.get(row.currency, Decimal(0)) + row.saving_amount
            )
    today = date.today()
    balances = []
    for pool in pools:
        first, last = (
            pool.valid_from or passport.start_date,
            pool.valid_until or passport.end_date,
        )
        balances.append(
            {
                "allocation": pool,
                "used": counts.get(pool.id, 0),
                "remaining": pool.quantity - counts.get(pool.id, 0),
                "valid_from": first,
                "valid_until": last,
                "expired": last is not None and last < today,
                "future": first is not None and first > today,
            }
        )
    return {"pools": balances, "redemptions": rows, "savings": savings}


def delete_unused(passport: Passport, user_id: int, allocation_id: int) -> None:
    passport = owned_passport(passport.tenant_id, user_id, passport.id)
    allocation: Allocation | None = (
        Allocation.query.filter_by(id=allocation_id, passport_id=passport.id)
        .with_for_update()
        .first()
    )
    if allocation is None:
        raise BuilderError(_("Voucher allocation not found."), 404)
    if Redemption.query.filter_by(allocation_id=allocation.id).first() is not None:
        raise BuilderError(
            _("Keep allocations with redemption history. Archive the edition instead."),
            409,
        )
    db.session.delete(allocation)


def copy_allocations(source: Passport, destination: Passport, user_id: int) -> None:
    if source.id == destination.id or source.tenant_id != destination.tenant_id:
        raise BuilderError(
            _("Copy vouchers to a different edition in the same organisation."), 409
        )
    owned_passport(destination.tenant_id, user_id, destination.id)
    owned_passport(source.tenant_id, user_id, source.id, archived_ok=True)
    targets = {
        target.airfield_id: target.id
        for target in PassportAirfield.query.filter_by(passport_id=destination.id).all()
    }
    destination.default_voucher_quantity = source.default_voucher_quantity
    for row in copy_preview(source, list(targets)):
        # Fresh quantity, not the old balance. No participant or redemption copy.
        db.session.add(
            Allocation(
                passport_id=destination.id,
                target_id=targets[row["airfield_id"]],
                personal_user_id=row["personal_user_id"],
                quantity=row["quantity"],
            )
        )


def copy_preview(source: Passport, field_ids: list[int]) -> list[dict[str, Any]]:
    """Only quantities/settings, never another participant's private redemption data."""
    query = (
        db.session.query(Allocation, PassportAirfield.airfield_id)
        .join(PassportAirfield, Allocation.target_id == PassportAirfield.id)
        .outerjoin(
            TenantUser,
            db.and_(
                TenantUser.user_id == Allocation.personal_user_id,
                TenantUser.tenant_id == source.tenant_id,
            ),
        )
        .filter(
            Allocation.passport_id == source.id,
            PassportAirfield.airfield_id.in_(field_ids),
            db.or_(
                Allocation.personal_user_id.is_(None), TenantUser.user_id.is_not(None)
            ),
        )
    )
    if source.default_voucher_quantity:
        # A common allowance is inherited when a person accepts the new edition;
        # copy only individual exceptions, not a roster of identical entitlements.
        query = query.filter(
            db.or_(
                Allocation.personal_user_id.is_(None),
                Allocation.quantity != source.default_voucher_quantity,
            )
        )
    selected = query.order_by(Allocation.id).limit(MAX_ALLOCATION_COPIES + 1).all()
    if len(selected) > MAX_ALLOCATION_COPIES:
        raise BuilderError(
            _(
                "Copy at most 5,000 voucher allocations. Narrow the target selection or copy without allocations."
            )
        )
    names = dict(
        db.session.query(PassportParticipant.user_id, PassportParticipant.display_name)
        .filter(
            PassportParticipant.passport_id == source.id,
            PassportParticipant.user_id.in_(
                [
                    pool.personal_user_id
                    for pool, _field in selected
                    if pool.personal_user_id is not None
                ]
            ),
        )
        .all()
    )
    return [
        {
            "airfield_id": field_id,
            "personal_user_id": pool.personal_user_id,
            "quantity": pool.quantity,
            "recipient": names.get(pool.personal_user_id, _("Awaiting participation")),
        }
        for pool, field_id in selected
    ]


def _provision_defaults(passport: Passport, user_id: int | None = None) -> None:
    """Caller holds the passport lock. INSERT SELECT fills missing personal rows.

    A default is an independent entitlement for every accepted participant/target,
    never a group pool. Existing quantities and redemption history stay intact.
    """
    from sqlalchemy import insert, literal

    if not passport.default_voucher_quantity:
        return
    db.session.flush()
    exists = (
        db.select(Allocation.id)
        .where(
            Allocation.passport_id == passport.id,
            Allocation.target_id == PassportAirfield.id,
            Allocation.personal_user_id == PassportParticipant.user_id,
        )
        .correlate(PassportAirfield, PassportParticipant)
        .exists()
    )
    rows = (
        db.select(
            literal(passport.id),
            PassportAirfield.id,
            PassportParticipant.user_id,
            literal(passport.default_voucher_quantity),
            literal(datetime.now(UTC)),
        )
        .select_from(PassportAirfield)
        .join(
            PassportParticipant,
            PassportParticipant.passport_id == PassportAirfield.passport_id,
        )
        .join(
            TenantUser,
            db.and_(
                TenantUser.user_id == PassportParticipant.user_id,
                TenantUser.tenant_id == passport.tenant_id,
            ),
        )
        .where(
            PassportAirfield.passport_id == passport.id,
            *active_participation(),
            ~exists,
        )
    )
    if user_id is not None:
        rows = rows.where(PassportParticipant.user_id == user_id)
    db.session.execute(
        insert(Allocation).from_select(
            ["passport_id", "target_id", "personal_user_id", "quantity", "created_at"],
            rows,
        )
    )


def configure_default(passport: Passport, user_id: int, raw: str) -> None:
    passport = owned_passport(passport.tenant_id, user_id, passport.id)
    passport.default_voucher_quantity = _quantity(raw)
    db.session.flush()
    _provision_defaults(passport)


def provision_participant(passport: Passport, user_id: int) -> None:
    """Called only after accepting an edition; membership alone creates no vouchers."""
    passport, _participant = _context(passport, user_id)
    _provision_defaults(passport, user_id)
