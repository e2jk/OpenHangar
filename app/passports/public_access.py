"""Passport-specific live access checks for Internet publication.

Sharing within a group, publishing on the Internet, and admitting a new member
are separate permissions. Every public representation goes through these checks.
"""

from datetime import datetime

from models import (
    Passport,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportStampPhoto,
    PublicShare,
    Tenant,
    TenantUser,
    User,
    db,
)
from services import public_shares
from utils import as_utc

from passports.participation import active_participation


def current_consent(value: datetime | None, participant: PassportParticipant) -> bool:
    return bool(
        value is not None
        and participant.accepted_at is not None
        and as_utc(value) >= as_utc(participant.accepted_at)
    )


def participants(passport: Passport) -> list[PassportParticipant]:
    query = (
        db.session.query(PassportParticipant)
        .join(User, User.id == PassportParticipant.user_id)
        .join(
            TenantUser,
            (TenantUser.user_id == User.id)
            & (TenantUser.tenant_id == passport.tenant_id),
        )
        .filter(
            PassportParticipant.passport_id == passport.id,
            *active_participation(),
            User.is_active.is_(True),
        )
    )
    if passport.is_group:
        query = query.join(
            PassportSeriesMember,
            (PassportSeriesMember.user_id == User.id)
            & (PassportSeriesMember.series_id == passport.series_id)
            & (PassportSeriesMember.tenant_id == passport.tenant_id),
        ).filter(
            PassportSeriesMember.is_active.is_(True),
            PassportSeriesMember.blocked.is_(False),
        )
    else:
        query = query.filter(PassportParticipant.user_id == passport.owner_user_id)
    return query.populate_existing().all()


def resolve(token: str) -> tuple[PublicShare, Passport] | None:
    share = public_shares.lookup(token, "passport")
    if share is None:
        return None
    passport = db.session.get(Passport, share.resource_id, populate_existing=True)
    if passport is None or not public_shares.matches_resource(
        share,
        tenant_id=passport.tenant_id,
        owner_user_id=passport.owner_user_id,
        created_at=passport.created_at,
    ):
        return None
    tenant = db.session.get(Tenant, passport.tenant_id, populate_existing=True)
    if tenant is None or not tenant.is_active:
        return None
    if passport.is_group:
        series = db.session.get(
            PassportSeries, passport.series_id, populate_existing=True
        )
        if (
            series is None
            or not series.is_group
            or series.tenant_id != passport.tenant_id
        ):
            return None
    if not any(p.user_id == passport.owner_user_id for p in participants(passport)):
        return None
    return share, passport


def publishes_visits(passport: Passport, participant: PassportParticipant) -> bool:
    return (
        not passport.is_group or bool(participant.share_visits)
    ) and current_consent(participant.public_shared_at, participant)


def publishes_photo(
    passport: Passport,
    participant: PassportParticipant,
    photo: PassportStampPhoto,
) -> bool:
    return (
        photo.passport_id == passport.id
        and photo.participant_id == participant.id
        and photo.uploader_user_id == participant.user_id
        and bool(photo.public_consent)
        and current_consent(photo.public_shared_at, participant)
        and publishes_visits(passport, participant)
    )
