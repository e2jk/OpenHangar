"""Owner-only mutations and a common impact preview for passport builders.

Callers commit. Archive/delete routes and builder finalisation use these checks;
an instance administrator cannot edit another pilot's personal passport.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportAirfield,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    Role,
    TenantUser,
    User,
    db,
    fits_column,
)

METADATA_FIELDS = ("title", "description", "edition_label", "start_date", "end_date")


class BuilderError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        self.status = status
        super().__init__(message)


def require_actor(tenant_id: int, user_id: int, *, creating: bool = False) -> User:
    user: User | None = db.session.get(User, user_id)
    membership = db.session.get(TenantUser, (user_id, tenant_id))
    if user is None or not user.is_active or user.view_only or membership is None:
        raise BuilderError(_("You cannot manage passports with this account."), 403)
    if creating and not (
        membership.access_scope == "passport"
        or user.is_pilot
        or membership.role
        in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.STUDENT, Role.INSTRUCTOR)
    ):
        raise BuilderError(_("Pilot access is required to create a passport."), 403)
    return user


def owned_passport(
    tenant_id: int, user_id: int, passport_id: int, *, archived_ok: bool = False
) -> Passport:
    require_actor(tenant_id, user_id)
    passport: Passport | None = (
        Passport.query.filter_by(
            id=passport_id, tenant_id=tenant_id, owner_user_id=user_id
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if passport is None:
        raise BuilderError(_("Passport not found."), 404)
    if passport.archived_at is not None and not archived_ok:
        raise BuilderError(_("Reopen this passport before editing it."), 409)
    return passport


def metadata_values(values: dict[str, str]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in METADATA_FIELDS:
        raw = values.get(key, "").strip()
        if key in ("start_date", "end_date"):
            try:
                clean[key] = date.fromisoformat(raw) if raw else None
            except ValueError as exc:
                raise BuilderError(_("Enter valid passport dates.")) from exc
        else:
            if not fits_column(getattr(Passport, key), raw) or (
                key == "description" and len(raw) > 8000
            ):
                raise BuilderError(_("A passport field is too long."))
            clean[key] = raw or None
    if not clean["title"]:
        raise BuilderError(_("Enter a passport name."))
    if (
        clean["start_date"]
        and clean["end_date"]
        and clean["start_date"] > clean["end_date"]
    ):
        raise BuilderError(_("The passport end date must not precede its start date."))
    return clean


def fingerprint(passport: Passport | None) -> str:
    if passport is None:
        return ""
    targets = [
        (
            target.id,
            target.airfield_id,
            target.display_order,
            target.note,
            target.public_catalogue,
            target.classification_overrides,
        )
        for target in PassportAirfield.query.filter_by(passport_id=passport.id)
        .order_by(PassportAirfield.id)
        .all()
    ]
    from models import PassportVoucherAllocation

    allocations = [
        (
            pool.id,
            pool.target_id,
            pool.personal_user_id,
            pool.quantity,
            pool.valid_from,
            pool.valid_until,
        )
        for pool in PassportVoucherAllocation.query.filter_by(passport_id=passport.id)
        .order_by(PassportVoucherAllocation.id)
        .all()
    ]
    data = [
        allocations,
        passport.owner_user_id,
        passport.tenant_id,
        passport.series_id,
        passport.default_voucher_quantity,
        passport.archived_at,
        passport.updated_at,
        *[getattr(passport, key) for key in METADATA_FIELDS],
        targets,
    ]
    return hashlib.sha256(json.dumps(data, default=str).encode()).hexdigest()


def protected_targets(passport_id: int, target_ids: set[int]) -> set[int]:
    if not target_ids:
        return set()
    visits = (
        db.session.query(PassportVisit.target_id)
        .filter(
            PassportVisit.passport_id == passport_id,
            PassportVisit.target_id.in_(target_ids),
        )
        .all()
    )
    photos = (
        db.session.query(PassportStampPhoto.target_id)
        .filter(
            PassportStampPhoto.passport_id == passport_id,
            PassportStampPhoto.target_id.in_(target_ids),
        )
        .all()
    )
    from passports.vouchers import protected_targets as voucher_targets

    return {row[0] for row in visits + photos} | voucher_targets(
        passport_id, target_ids
    )


def completion_preview(
    passport: Passport | None,
    user_id: int,
    fields: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    tenant_id: int | None = None,
) -> dict[str, int | None]:
    """Use the live stamp engine for both staged and saved selections."""
    from passports.eligibility import evaluate, participant_for

    result = evaluate(
        passport.tenant_id
        if passport
        else tenant_id
        or TenantUser.query.filter_by(user_id=user_id)
        .order_by(TenantUser.tenant_id)
        .first()
        .tenant_id,
        user_id,
        fields,
        metadata.get("start_date"),
        metadata.get("end_date"),
        participant_for(passport.id, user_id) if passport else None,
        preview=passport is None,
    )
    return {key: result[key] for key in ("visited", "total", "percent")}


def create_personal(tenant_id: int, user_id: int, metadata: dict[str, Any]) -> Passport:
    user = require_actor(tenant_id, user_id, creating=True)
    passport = Passport(tenant_id=tenant_id, owner_user_id=user_id, **metadata)
    db.session.add(passport)
    db.session.flush()
    db.session.add(
        PassportParticipant(
            passport_id=passport.id,
            user_id=user_id,
            display_name=user.display_name,
            accepted_at=datetime.now(UTC),
        )
    )
    return passport


def set_archived(passport: Passport, archived: bool) -> None:
    passport.archived_at = datetime.now(UTC) if archived else None
