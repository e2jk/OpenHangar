"""Stamp photos shown to an edition's group: an explicit, withdrawable choice.

A photo is private until its uploader shares it with the group. It then stays
visible only while both the uploader and the viewer are active, accepted,
visit-sharing participants of that edition and members of the group. A share
made before the uploader's current acceptance (a rejoin) no longer counts.
Every check runs server-side, both for the page and for the image itself.
"""

from datetime import UTC, datetime
from typing import Any

from models import (
    Passport,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportStampPhoto,
    TenantUser,
    User,
    db,
)

from passports import groups
from passports.public_access import current_consent


def _sharing(participant: PassportParticipant | None) -> bool:
    return bool(
        participant is not None
        and participant.is_active
        and not participant.blocked
        and participant.accepted_at is not None
        and participant.share_visits
    )


def viewer_may_see(passport: Passport, user_id: int) -> bool:
    """Only an edition's own sharing participants see each other's photos."""
    if not passport.is_group or passport.series_id is None:
        return False
    series = db.session.get(PassportSeries, passport.series_id)
    if series is None or not series.is_group:
        return False
    if groups.membership(series, user_id) is None:
        return False
    viewer = PassportParticipant.query.filter_by(
        passport_id=passport.id, user_id=user_id
    ).first()
    return _sharing(viewer)


def _shared_query(passport: Passport) -> Any:
    return (
        db.session.query(PassportStampPhoto, PassportParticipant)
        .join(
            PassportParticipant,
            PassportParticipant.id == PassportStampPhoto.participant_id,
        )
        .join(
            PassportSeriesMember,
            (PassportSeriesMember.user_id == PassportParticipant.user_id)
            & (PassportSeriesMember.series_id == passport.series_id),
        )
        .join(User, User.id == PassportParticipant.user_id)
        .join(
            TenantUser,
            (TenantUser.user_id == User.id)
            & (TenantUser.tenant_id == passport.tenant_id),
        )
        .filter(
            PassportStampPhoto.passport_id == passport.id,
            PassportParticipant.passport_id == passport.id,
            PassportStampPhoto.group_shared_at.is_not(None),
            PassportParticipant.is_active.is_(True),
            PassportParticipant.blocked.is_(False),
            PassportParticipant.accepted_at.is_not(None),
            PassportParticipant.share_visits.is_(True),
            PassportSeriesMember.is_active.is_(True),
            PassportSeriesMember.blocked.is_(False),
            User.is_active.is_(True),
        )
    )


def _current(photo: PassportStampPhoto, participant: PassportParticipant) -> bool:
    # The same rule as public consent: only a choice made since joining counts.
    return current_consent(photo.group_shared_at, participant)


def friends_photos(
    passport: Passport, target_id: int, user_id: int
) -> list[dict[str, Any]]:
    """Other participants' photos of this airfield that the viewer may see."""
    if not viewer_may_see(passport, user_id):
        return []
    rows = (
        _shared_query(passport)
        .filter(
            PassportStampPhoto.target_id == target_id,
            PassportParticipant.user_id != user_id,
        )
        .all()
    )
    return sorted(
        (
            {"id": photo.id, "name": participant.display_name}
            for photo, participant in rows
            if _current(photo, participant)
        ),
        key=lambda item: (item["name"].casefold(), item["id"]),
    )


def visible_photo(
    passport: Passport, photo_id: int, user_id: int
) -> PassportStampPhoto | None:
    """A friend's photo, rechecked at every request; None when not shared."""
    if not viewer_may_see(passport, user_id):
        return None
    row = _shared_query(passport).filter(PassportStampPhoto.id == photo_id).first()
    if row is None or not _current(*row):
        return None
    photo: PassportStampPhoto = row[0]
    return photo


def is_shared(photo: PassportStampPhoto, participant: PassportParticipant) -> bool:
    """Whether the owner's own photo currently reaches the group."""
    return _sharing(participant) and _current(photo, participant)


def set_shared(photo: PassportStampPhoto, share: bool) -> None:
    photo.group_shared_at = datetime.now(UTC) if share else None
