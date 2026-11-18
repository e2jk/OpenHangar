"""Membership permissions independent of pilot-role permissions."""

from typing import Any

from flask import abort, session
from models import (
    Passport,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    db,
)
from utils import current_membership


def accessible_passports() -> Any:
    membership = current_membership()
    uid = session.get("user_id")
    if membership is None or uid is None:
        return Passport.query.filter(db.false())
    accepted = db.select(PassportParticipant.passport_id).where(
        PassportParticipant.user_id == uid,
        PassportParticipant.is_active.is_(True),
        PassportParticipant.blocked.is_(False),
        PassportParticipant.accepted_at.is_not(None),
    )
    group_memberships = db.select(PassportSeriesMember.series_id).where(
        PassportSeriesMember.user_id == uid,
        PassportSeriesMember.tenant_id == membership.tenant_id,
        PassportSeriesMember.is_active.is_(True),
        PassportSeriesMember.blocked.is_(False),
    )
    return Passport.query.filter(
        Passport.tenant_id == membership.tenant_id,
        db.or_(Passport.is_group.is_(False), Passport.series_id.in_(group_memberships)),
        db.or_(
            db.and_(Passport.is_group.is_(False), Passport.owner_user_id == uid),
            Passport.id.in_(accepted),
        ),
    )


def require_passport(passport_id: int) -> Passport:
    passport: Passport | None = (
        accessible_passports().filter(Passport.id == passport_id).first()
    )
    if passport is None:
        abort(404)
    return passport


def accessible_group_series() -> Any:
    membership = current_membership()
    uid = session.get("user_id")
    if membership is None or uid is None:
        return PassportSeries.query.filter(db.false())
    return PassportSeries.query.join(
        PassportSeriesMember, PassportSeriesMember.series_id == PassportSeries.id
    ).filter(
        PassportSeries.tenant_id == membership.tenant_id,
        PassportSeries.is_group.is_(True),
        PassportSeriesMember.user_id == uid,
        PassportSeriesMember.tenant_id == membership.tenant_id,
        PassportSeriesMember.is_active.is_(True),
        PassportSeriesMember.blocked.is_(False),
    )
