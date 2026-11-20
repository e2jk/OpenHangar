"""Delete passport photo files only after committed ORM deletions.

Database cascades handle rows. The pre-flush lookup also captures files removed
by cascading deletion of a target, participant, passport, owner or tenant. A
rollback retains files. Core/bulk SQL deletes intentionally bypass ORM hooks and
must not be used to manage passport records with files.
"""

import re
from pathlib import Path
from typing import Any

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
from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session


@event.listens_for(Session, "before_flush")
def prepare_passport_changes(session: Session, _context: Any, _instances: Any) -> None:
    replaced: set[str] = set()
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
        if isinstance(obj, PassportStampPhoto):
            replaced.update(inspect(obj).attrs.filename.history.deleted)
        if isinstance(obj, Passport) and obj.series_id is not None:
            series = session.get(PassportSeries, obj.series_id)
            if series is None or series.tenant_id != obj.tenant_id:
                raise ValueError("Passport series belongs to another tenant")
    filters = []
    for obj in session.deleted:
        if isinstance(obj, PassportStampPhoto):
            filters.append(PassportStampPhoto.id == obj.id)
        elif isinstance(obj, Passport):
            filters.append(PassportStampPhoto.passport_id == obj.id)
        elif isinstance(obj, PassportParticipant):
            filters.append(PassportStampPhoto.participant_id == obj.id)
        elif isinstance(obj, PassportAirfield):
            filters.append(PassportStampPhoto.target_id == obj.id)
        elif isinstance(obj, User):
            filters.extend(
                [
                    PassportStampPhoto.uploader_user_id == obj.id,
                    PassportStampPhoto.participant_id.in_(
                        select(PassportParticipant.id).where(
                            PassportParticipant.user_id == obj.id
                        )
                    ),
                    PassportStampPhoto.passport_id.in_(
                        select(Passport.id).where(Passport.owner_user_id == obj.id)
                    ),
                ]
            )
        elif isinstance(obj, Tenant):
            filters.append(
                PassportStampPhoto.passport_id.in_(
                    select(Passport.id).where(Passport.tenant_id == obj.id)
                )
            )
    filenames = set(replaced)
    if filters:
        filenames.update(
            session.execute(
                select(PassportStampPhoto.filename).where(db.or_(*filters))
            ).scalars()
        )
    if filenames:
        transaction = session.get_nested_transaction() or session.get_transaction()
        session.info.setdefault("passport_deleted_photos", {}).setdefault(
            transaction, set()
        ).update(filenames)


@event.listens_for(Session, "after_commit")
def remove_passport_files(session: Session) -> None:
    queues = session.info.get("passport_deleted_photos", {})
    transaction = session.get_nested_transaction() or session.get_transaction()
    filenames = queues.pop(transaction, set())
    if transaction is not None and transaction.parent is not None:
        queues.setdefault(transaction.parent, set()).update(filenames)
        return
    for filename in filenames:
        path = Path(current_app.config["UPLOAD_FOLDER"]) / "passport_photos" / filename
        try:
            path.unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("Could not delete passport photo %s", filename)


@event.listens_for(Session, "after_soft_rollback")
def retain_passport_files(session: Session, previous_transaction: Any) -> None:
    session.info.get("passport_deleted_photos", {}).pop(previous_transaction, None)


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
