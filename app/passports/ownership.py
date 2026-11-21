"""Ownership changes require an authenticated participant's explicit acceptance.

Callers commit. Use the same series/parent lock order as admission and removal.
A pending offer grants no authority and is revalidated on every operation.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportOwnershipOffer,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    db,
)
from utils import as_utc

from passports import groups, memberships
from passports.management import BuilderError, require_actor
from passports.participation import active_participation


def _eligible(series: PassportSeries, passports: list[Passport], user_id: int) -> None:
    require_actor(series.tenant_id, user_id)
    groups.require_series(series, user_id)
    if not PassportParticipant.query.filter(
        PassportParticipant.passport_id.in_([p.id for p in passports]),
        PassportParticipant.user_id == user_id,
        *active_participation(),
    ).first():
        raise BuilderError(_("Choose an active participant in this group."), 409)


def offer(tenant_id: int, owner_id: int, series_id: int, target_id: int) -> None:
    series, passports = memberships.locked(tenant_id, owner_id, series_id, owner=True)
    if owner_id == target_id:
        raise BuilderError(_("Choose another participant."))
    _eligible(series, passports, target_id)
    row = db.session.get(PassportOwnershipOffer, series.id, populate_existing=True)
    if row is None:
        row = PassportOwnershipOffer(series_id=series.id)
        db.session.add(row)
    row.requester_user_id, row.target_user_id = owner_id, target_id
    row.request_key = secrets.token_hex(16)
    row.created_at = datetime.now(UTC)
    row.expires_at = row.created_at + timedelta(days=7)


def _current(series: PassportSeries, key: str) -> PassportOwnershipOffer:
    row = db.session.get(PassportOwnershipOffer, series.id, populate_existing=True)
    if row is None or not secrets.compare_digest(
        row.request_key.encode(), key.encode()
    ):
        raise BuilderError(_("This ownership offer is no longer available."), 409)
    expiry = as_utc(row.expires_at)
    if expiry < datetime.now(UTC) or row.requester_user_id != series.owner_user_id:
        raise BuilderError(_("This ownership offer is no longer available."), 409)
    return row


def respond(
    tenant_id: int, user_id: int, series_id: int, key: str, *, accept: bool
) -> None:
    series, passports = memberships.locked(tenant_id, user_id, series_id)
    row = _current(series, key)
    if row.target_user_id != user_id:
        raise BuilderError(
            _("Only the selected participant can respond to this offer."), 403
        )
    require_actor(tenant_id, user_id)
    if accept:
        _eligible(series, passports, user_id)
        if any(p.owner_user_id != row.requester_user_id for p in passports):
            raise BuilderError(
                _("Group ownership changed. Ask the owner to make a new offer."), 409
            )
        for passport in passports:
            passport.owner_user_id = user_id
        series.owner_user_id = user_id
    db.session.delete(row)


def cancel(tenant_id: int, user_id: int, series_id: int, key: str) -> None:
    series, _passports = memberships.locked(tenant_id, user_id, series_id, owner=True)
    # Even an expired offer may be cleared by the current owner.
    row = db.session.get(PassportOwnershipOffer, series.id, populate_existing=True)
    if row is None or not secrets.compare_digest(
        row.request_key.encode(), key.encode()
    ):
        raise BuilderError(_("This ownership offer is no longer available."), 409)
    db.session.delete(row)


def view(series: PassportSeries, user_id: int) -> dict[str, Any] | None:
    groups.require_series(series, user_id)
    row = db.session.get(PassportOwnershipOffer, series.id)
    if row is None or user_id not in (series.owner_user_id, row.target_user_id):
        return None
    target = db.session.get(PassportSeriesMember, (series.id, row.target_user_id))
    if target is None:
        return None
    return {
        "key": row.request_key,
        "target_name": target.display_name,
        "can_respond": row.target_user_id == user_id,
        "expires_at": row.expires_at,
    }
