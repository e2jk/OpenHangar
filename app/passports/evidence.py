"""Participant-owned evidence mutations. Callers commit; photos start private."""

from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app
from flask_babel import gettext as _
from models import (
    Flight,
    Passport,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    db,
)
from services import images
from services.images import MAX_PHOTO_BYTES
from utils import db_id
from werkzeug.datastructures import FileStorage

from passports.eligibility import date_eligible, eligible_flight, participant_for
from passports.management import BuilderError, require_actor

__all__ = ["MAX_PHOTO_BYTES"]


def writable_participant(passport: Passport, user_id: int) -> PassportParticipant:
    require_actor(passport.tenant_id, user_id)
    participant = participant_for(passport.id, user_id)
    if participant is None:
        raise BuilderError(_("Join this passport before recording visits."), 403)
    if passport.archived_at is not None:
        raise BuilderError(_("Reopen this passport before editing it."), 409)
    return participant


def visit_values(
    passport: Passport, participant: PassportParticipant, values: Any
) -> dict[str, Any]:
    try:
        day = date.fromisoformat(values.get("visit_date", ""))
    except ValueError as exc:
        raise BuilderError(_("Enter a valid visit date.")) from exc
    if not date_eligible(passport, day):
        raise BuilderError(
            _("The visit must be within the passport dates and not in the future.")
        )
    note = values.get("note", "").strip()
    if len(note) > 8000:
        raise BuilderError(_("The visit note is too long."))
    flight_id = None
    if values.get("supporting_flight", "").strip():
        try:
            flight_id = db_id(values["supporting_flight"])
        except ValueError as exc:
            raise BuilderError(
                _("Choose a flight from your own logbook on the visit date.")
            ) from exc
        flight = db.session.get(Flight, flight_id)
        if (
            flight is None
            or participant.user_id
            not in (flight.pic_user_id, flight.second_crew_user_id)
            or flight.date != day
            or not eligible_flight(flight)
        ):
            raise BuilderError(
                _("Choose a flight from your own logbook on the visit date.")
            )
    return {"visit_date": day, "flight_id": flight_id, "note": note or None}


def add_visit(
    passport: Passport, participant: PassportParticipant, target_id: int, values: Any
) -> None:
    db.session.add(
        PassportVisit(
            passport_id=passport.id,
            participant_id=participant.id,
            target_id=target_id,
            author_user_id=participant.user_id,
            **visit_values(passport, participant, values),
        )
    )


def photo_path(filename: str) -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / "passport_photos" / filename


def save_photo(upload: FileStorage | None) -> str:
    try:
        data = images.clean_photo(upload)
    except images.PhotoError as exc:
        raise BuilderError(str(exc)) from exc
    return images.store(photo_path(""), data)


def replace_photo(
    passport: Passport, participant: PassportParticipant, target_id: int, filename: str
) -> None:
    photo = PassportStampPhoto.query.filter_by(
        participant_id=participant.id, target_id=target_id
    ).first()
    if photo is None:
        photo = PassportStampPhoto(
            passport_id=passport.id,
            participant_id=participant.id,
            target_id=target_id,
            uploader_user_id=participant.user_id,
            filename=filename,
        )
        db.session.add(photo)
    photo.filename = filename
    photo.public_consent = False
    photo.public_shared_at = None
    # A new picture needs a new sharing choice.
    photo.group_shared_at = None
