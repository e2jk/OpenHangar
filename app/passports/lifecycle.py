"""Delete passport photo files only after committed ORM deletions.

Database cascades handle rows. The pre-flush lookup also captures files removed
by cascading deletion of a target, participant, passport, owner or tenant. A
rollback retains files. Core/bulk SQL deletes intentionally bypass ORM hooks and
must not be used to manage passport records with files.
"""

import re
from pathlib import Path
from typing import Any, cast

from flask import current_app
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PassportParticipant,
    PassportSeries,
    PassportStampPhoto,
    PassportVoucherRedemption,
    Tenant,
    User,
    db,
)
from services import file_cleanup
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


@event.listens_for(Session, "before_flush")
def prepare_passport_changes(session: Session, _context: Any, _instances: Any) -> None:
    for obj in (*session.new, *session.dirty):
        if isinstance(obj, PassportAirfield):
            classifications = (
                obj.classification_overrides
                if obj.classification_overrides is not None
                else {}
            )
            if (
                not isinstance(classifications, dict)
                or classifications.keys() - {"military", "ulm"}
                or any(
                    value is not None and not isinstance(value, bool)
                    for value in classifications.values()
                )
            ):
                raise ValueError("Invalid passport-local classifications")
            if obj.public_catalogue:
                identity = session.get(PassportAirfieldIdentity, obj.airfield_id)
                if identity is None or identity.source != "ourairports":
                    raise ValueError("Public catalogue targets require a public source")
        if (
            isinstance(obj, PassportAirfieldAlias)
            and obj.catalogue_source_id is not None
        ):
            identity = session.get(PassportAirfieldIdentity, obj.airfield_id)
            if (
                identity is None
                or identity.source != "ourairports"
                or identity.source_id != obj.catalogue_source_id
            ):
                raise ValueError(
                    "Public alias provenance must match its airfield source"
                )
        if isinstance(obj, PassportStampPhoto) and not re.fullmatch(
            r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)", obj.filename or ""
        ):
            raise ValueError("Invalid passport photo filename")
        if isinstance(obj, Passport) and obj.series_id is not None:
            series = session.get(PassportSeries, obj.series_id)
            if series is None or series.tenant_id != obj.tenant_id:
                raise ValueError("Passport series belongs to another tenant")


def photo_cascade_filter(obj: Any) -> ColumnElement[bool] | None:
    if isinstance(obj, Passport):
        return cast(ColumnElement[bool], PassportStampPhoto.passport_id == obj.id)
    if isinstance(obj, PassportParticipant):
        return cast(ColumnElement[bool], PassportStampPhoto.participant_id == obj.id)
    if isinstance(obj, PassportAirfield):
        return cast(ColumnElement[bool], PassportStampPhoto.target_id == obj.id)
    if isinstance(obj, User):
        predicate: ColumnElement[bool] = db.or_(
            PassportStampPhoto.uploader_user_id == obj.id,
            PassportStampPhoto.participant_id.in_(
                select(PassportParticipant.id).where(
                    PassportParticipant.user_id == obj.id
                )
            ),
            PassportStampPhoto.passport_id.in_(
                select(Passport.id).where(Passport.owner_user_id == obj.id)
            ),
        )
        return predicate
    if isinstance(obj, Tenant):
        predicate = PassportStampPhoto.passport_id.in_(
            select(Passport.id).where(Passport.tenant_id == obj.id)
        )
        return predicate
    return None


file_cleanup.register(
    PassportStampPhoto,
    "filename",
    lambda: Path(current_app.config["UPLOAD_FOLDER"]) / "passport_photos",
    cascade_filter=photo_cascade_filter,
    error_label="passport photo",
)


@event.listens_for(Session, "before_flush")
def anonymize_voucher_history(session: Session, _context: Any, _instances: Any) -> None:
    """Keep shared consumption without retaining a removed participant's private data."""
    filters = []
    for obj in session.deleted:
        if isinstance(obj, PassportParticipant):
            filters.append(PassportVoucherRedemption.participant_id == obj.id)
        elif isinstance(obj, User):
            filters.append(
                PassportVoucherRedemption.participant_id.in_(
                    select(PassportParticipant.id).where(
                        PassportParticipant.user_id == obj.id
                    )
                )
            )
    if filters:
        session.execute(
            update(PassportVoucherRedemption)
            .where(db.or_(*filters))
            .values(
                note=None,
                saving_amount=None,
                currency=None,
                flight_id=None,
            )
        )
