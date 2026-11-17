"""Membership permissions independent of pilot-role permissions."""

from typing import Any

from flask import abort, session
from models import Passport, PassportParticipant, db
from utils import current_membership


def accessible_passports() -> Any:
    membership = current_membership()
    uid = session.get("user_id")
    if membership is None or uid is None:
        return Passport.query.filter(db.false())
    accepted = db.select(PassportParticipant.passport_id).where(
        PassportParticipant.user_id == uid,
        PassportParticipant.is_active.is_(True),
        PassportParticipant.accepted_at.is_not(None),
    )
    return Passport.query.filter(
        Passport.tenant_id == membership.tenant_id,
        db.or_(Passport.owner_user_id == uid, Passport.id.in_(accepted)),
    )


def require_passport(passport_id: int) -> Passport:
    passport: Passport | None = (
        accessible_passports().filter(Passport.id == passport_id).first()
    )
    if passport is None:
        abort(404)
    return passport
