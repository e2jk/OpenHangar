"""An explicit own-data projection for the private printable passport."""

from typing import Any

from flask import url_for
from flask_babel import gettext as _
from models import Passport, PassportStampPhoto
from utils import db_id

from passports.drafts import MAX_TARGETS
from passports.eligibility import participant_for, passport_progress
from passports.evidence import photo_path
from passports.management import BuilderError


def personal(passport: Passport, user_id: int, selected: list[str]) -> dict[str, Any]:
    if len(selected) > MAX_TARGETS:
        raise BuilderError(_("Choose fewer stamp photos."))
    try:
        selected_ids = {db_id(value) for value in selected}
    except ValueError as exc:
        raise BuilderError(_("Choose valid stamp photos.")) from exc
    progress = passport_progress(passport, user_id)
    participant = participant_for(passport.id, user_id)
    photos = (
        PassportStampPhoto.query.filter_by(
            passport_id=passport.id,
            participant_id=participant.id,
            uploader_user_id=user_id,
        ).all()
        if participant is not None
        else []
    )
    if selected_ids - {photo.id for photo in photos}:
        raise BuilderError(_("Stamp photo not found."), 404)
    by_target = {photo.target_id: photo for photo in photos}
    choices, targets = [], []
    for row in progress["rows"]:
        photo = by_target.get(row["target_id"])
        choice = None
        if photo is not None:
            choice = {
                "id": photo.id,
                "name": row["name"],
                "selected": photo.id in selected_ids,
                "available": photo_path(photo.filename).is_file(),
                "url": url_for(
                    "passports.stamp_photo", passport_id=passport.id, photo_id=photo.id
                ),
            }
            choices.append(choice)
        targets.append(
            {
                "name": row["name"],
                "ident": row["ident"],
                "visited": row["visited"],
                "is_closed": row["is_closed"],
                "date": row["first"]["date"] if row["first"] else None,
                "photo": choice if choice is not None and choice["selected"] else None,
            }
        )
    return {
        "id": passport.id,
        "title": passport.title,
        "edition_label": passport.edition_label,
        "start_date": passport.start_date,
        "end_date": passport.end_date,
        "archived": passport.archived_at is not None,
        "visited": progress["visited"],
        "total": progress["total"],
        "percent": progress["percent"],
        "closed_excluded": progress["excluded_closed"],
        "targets": targets,
        "photos": choices,
        "include_photos": bool(selected_ids),
    }
