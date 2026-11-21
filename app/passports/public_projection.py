"""Allowlisted Internet DTO; never serialize private group marker dictionaries."""

from typing import Any

from flask import url_for
from models import Passport, PassportStampPhoto

from passports import public_access
from passports.eligibility import passport_progress, target_fields
from passports.evidence import photo_path


def project(passport: Passport, token: str) -> dict[str, Any]:
    fields = target_fields(passport)
    participants = public_access.participants(passport)
    targets = {
        field["target_id"]: {
            "name": field["name"],
            "ident": field["ident"],
            "latitude": field["latitude"],
            "longitude": field["longitude"],
            "closed": bool(field.get("is_closed")),
            "visited": False,
            "month": None,
            "frequency": None,
            "photos": [],
        }
        for field in fields
    }
    for participant in participants:
        if not public_access.publishes_visits(passport, participant):
            continue
        frequency = public_access.current_consent(
            participant.public_frequency_at, participant
        )
        for row in passport_progress(passport, participant.user_id)["rows"]:
            target = targets[row["target_id"]]
            if row["visited"]:
                target["visited"] = True
                month = row["first"]["date"].strftime("%Y-%m")
                if target["month"] is None or month < target["month"]:
                    target["month"] = month
                if frequency:
                    target["frequency"] = (target["frequency"] or 0) + row["count"]
        for photo in PassportStampPhoto.query.filter_by(
            passport_id=passport.id,
            participant_id=participant.id,
            public_consent=True,
        ).all():
            if (
                photo.target_id in targets
                and public_access.publishes_photo(passport, participant, photo)
                and photo_path(photo.filename).is_file()
            ):
                targets[photo.target_id]["photos"].append(
                    {
                        "url": url_for(
                            "passports.public_photo", token=token, photo_id=photo.id
                        ),
                        "name": participant.public_name or None,
                    }
                )
    rows = list(targets.values())
    total = sum(not row["closed"] or row["visited"] for row in rows)
    visited = sum(row["visited"] for row in rows)
    return {
        "title": passport.title,
        "edition": passport.edition_label,
        "targets": rows,
        "visited": visited,
        "total": total,
        "percent": round(visited * 100 / total) if total else None,
    }
