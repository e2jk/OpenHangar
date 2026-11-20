"""Private catalogue identities are scoped before passport-only tools expose them.

Public CSV data stays available. A reference to a hidden materialised public
field must never reveal its tenant overrides or manually maintained aliases.
Trusted non-request jobs retain their existing tenant catalogue access.
"""

from typing import Any

from flask import has_request_context, session
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    PassportParticipant,
    PassportSeriesMember,
    TenantUser,
    User,
    db,
)


def visible_identity_ids(
    tenant_id: int, user_id: int | None = None, *, private_metadata: bool = False
) -> Any:
    """Return a visibility subquery, or None for a full membership/trusted job."""
    if user_id is None:
        if not has_request_context():
            return None
        user_id = session.get("user_id")
    empty = db.select(PassportAirfield.airfield_id).where(db.false())
    if user_id is None:
        return empty
    # Select scalars so an identity-map entry cached before a scope change
    # cannot retain access. This is one bounded lookup, never one per field.
    scope = (
        db.session.query(TenantUser.access_scope)
        .join(User, User.id == TenantUser.user_id)
        .filter(
            TenantUser.user_id == user_id,
            TenantUser.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
        .scalar()
    )
    if scope == "full":
        return None
    if scope != "passport":
        return empty
    accepted = db.select(PassportParticipant.passport_id).where(
        PassportParticipant.user_id == user_id,
        PassportParticipant.is_active.is_(True),
        PassportParticipant.blocked.is_(False),
        PassportParticipant.accepted_at.is_not(None),
    )
    groups = db.select(PassportSeriesMember.series_id).where(
        PassportSeriesMember.tenant_id == tenant_id,
        PassportSeriesMember.user_id == user_id,
        PassportSeriesMember.is_active.is_(True),
        PassportSeriesMember.blocked.is_(False),
    )
    passports = db.select(Passport.id).where(
        Passport.tenant_id == tenant_id,
        db.or_(Passport.is_group.is_(False), Passport.series_id.in_(groups)),
        db.or_(Passport.owner_user_id == user_id, Passport.id.in_(accepted)),
    )
    permitted = db.select(PassportAirfield.airfield_id).where(
        PassportAirfield.tenant_id == tenant_id,
        PassportAirfield.passport_id.in_(passports),
    )
    if private_metadata:
        return permitted.where(PassportAirfield.public_catalogue.is_(False))
    return db.select(PassportAirfieldIdentity.id).where(
        PassportAirfieldIdentity.tenant_id == tenant_id,
        db.or_(
            PassportAirfieldIdentity.source == "ourairports",
            PassportAirfieldIdentity.id.in_(permitted),
        ),
    )
