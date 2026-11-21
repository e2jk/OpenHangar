"""Participant-owned evidence mutations. Callers commit; photos start private."""

import io
import secrets
import warnings
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
from PIL import Image, ImageOps, UnidentifiedImageError
from utils import db_id
from werkzeug.datastructures import FileStorage

from passports.eligibility import date_eligible, eligible_flight, participant_for
from passports.management import BuilderError, require_actor

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000


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
    if upload is None or not upload.filename:
        raise BuilderError(_("Choose a photo to upload."))
    raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise BuilderError(_("Photos must be at most 10 MB."))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if (
                    original.format not in {"JPEG", "PNG", "WEBP"}
                    or original.width * original.height > MAX_PHOTO_PIXELS
                ):
                    raise ValueError("Unsupported or oversized image")
                original.load()
                upright = ImageOps.exif_transpose(original)
                rgb = upright.convert("RGB")
                rgb.thumbnail((2000, 2000))
                # Copy pixels into a clean image: no EXIF, GPS, comments or profiles.
                clean = Image.new("RGB", rgb.size)
                clean.paste(rgb)
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=85)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise BuilderError(
            _("Upload a valid JPEG, PNG or WebP photo (up to 20 megapixels).")
        ) from exc
    filename = secrets.token_hex(16) + ".jpg"
    path = photo_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output.getvalue())
    return filename


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
    # A new picture needs a new sharing choice.
    photo.group_shared_at = None
