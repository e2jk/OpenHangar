"""Owner-only mutations and a common impact preview for passport builders.

Callers commit. Archive/delete routes and builder finalisation use these checks;
an instance administrator cannot edit another pilot's personal passport.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from flask_babel import gettext as _
from models import (
    Flight,
    LogbookEntryType,
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportArrivalExclusion,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    Role,
    TenantUser,
    User,
    db,
    fits_column,
)

METADATA_FIELDS = ("title", "description", "edition_label", "start_date", "end_date")


class BuilderError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        self.status = status
        super().__init__(message)


def require_actor(tenant_id: int, user_id: int, *, creating: bool = False) -> User:
    user: User | None = db.session.get(User, user_id)
    membership = db.session.get(TenantUser, (user_id, tenant_id))
    if user is None or not user.is_active or user.view_only or membership is None:
        raise BuilderError(_("You cannot manage passports with this account."), 403)
    if creating and not (
        user.is_pilot
        or membership.role
        in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.STUDENT, Role.INSTRUCTOR)
    ):
        raise BuilderError(_("Pilot access is required to create a passport."), 403)
    return user


def owned_passport(
    tenant_id: int, user_id: int, passport_id: int, *, archived_ok: bool = False
) -> Passport:
    require_actor(tenant_id, user_id)
    passport: Passport | None = (
        Passport.query.filter_by(
            id=passport_id, tenant_id=tenant_id, owner_user_id=user_id
        )
        .with_for_update()
        .first()
    )
    if passport is None:
        raise BuilderError(_("Passport not found."), 404)
    if passport.archived_at is not None and not archived_ok:
        raise BuilderError(_("Reopen this passport before editing it."), 409)
    return passport


def metadata_values(values: dict[str, str]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in METADATA_FIELDS:
        raw = values.get(key, "").strip()
        if key in ("start_date", "end_date"):
            try:
                clean[key] = date.fromisoformat(raw) if raw else None
            except ValueError as exc:
                raise BuilderError(_("Enter valid passport dates.")) from exc
        else:
            if not fits_column(getattr(Passport, key), raw) or (
                key == "description" and len(raw) > 8000
            ):
                raise BuilderError(_("A passport field is too long."))
            clean[key] = raw or None
    if not clean["title"]:
        raise BuilderError(_("Enter a passport name."))
    if (
        clean["start_date"]
        and clean["end_date"]
        and clean["start_date"] > clean["end_date"]
    ):
        raise BuilderError(_("The passport end date must not precede its start date."))
    return clean


def fingerprint(passport: Passport | None) -> str:
    if passport is None:
        return ""
    targets = [
        (target.id, target.airfield_id, target.display_order, target.note)
        for target in PassportAirfield.query.filter_by(passport_id=passport.id)
        .order_by(PassportAirfield.id)
        .all()
    ]
    data = [
        passport.owner_user_id,
        passport.tenant_id,
        passport.series_id,
        passport.archived_at,
        passport.updated_at,
        *[getattr(passport, key) for key in METADATA_FIELDS],
        targets,
    ]
    return hashlib.sha256(json.dumps(data, default=str).encode()).hexdigest()


def protected_targets(passport_id: int, target_ids: set[int]) -> set[int]:
    if not target_ids:
        return set()
    visits = (
        db.session.query(PassportVisit.target_id)
        .filter(
            PassportVisit.passport_id == passport_id,
            PassportVisit.target_id.in_(target_ids),
        )
        .all()
    )
    photos = (
        db.session.query(PassportStampPhoto.target_id)
        .filter(
            PassportStampPhoto.passport_id == passport_id,
            PassportStampPhoto.target_id.in_(target_ids),
        )
        .all()
    )
    # Voucher allocations/redemptions will join this check when introduced in 41f.
    return {row[0] for row in visits + photos}


def completion_preview(
    passport: Passport | None,
    user_id: int,
    fields: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, int | None]:
    """Own-logbook coverage preview; no other member's private flights are read."""
    today = date.today()
    start, end = metadata.get("start_date"), metadata.get("end_date")

    def eligible(day: date) -> bool:
        return (
            day <= today
            and (start is None or day >= start)
            and (end is None or day <= end)
        )

    participant = (
        PassportParticipant.query.filter_by(
            passport_id=passport.id, user_id=user_id, is_active=True
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .first()
        if passport is not None
        else None
    )
    stamped: set[str] = set()
    codes: dict[str, list[tuple[str, date | None, date | None]]] = {}
    field_keys = {field["id"]: field["key"] for field in fields if field.get("id")}
    for field in fields:
        if not field.get("id"):
            for code in {field.get("ident"), field.get("icao_code")} - {
                None,
                "",
                "ZZZZ",
            }:
                codes.setdefault(str(code), []).append((field["key"], None, None))
    if field_keys:
        aliases = PassportAirfieldAlias.query.filter(
            PassportAirfieldAlias.airfield_id.in_(field_keys)
        ).all()
        for alias in aliases:
            codes.setdefault(alias.code, []).append(
                (field_keys[alias.airfield_id], alias.valid_from, alias.valid_until)
            )
    excluded = (
        {
            row.flight_id
            for row in PassportArrivalExclusion.query.filter_by(
                participant_id=participant.id
            ).all()
        }
        if participant
        else set()
    )
    if participant is not None or passport is None:
        flights = Flight.query.filter(
            db.or_(
                Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
            ),
            Flight.entry_type == LogbookEntryType.FLIGHT,
            Flight.date <= today,
        ).all()
        for flight in flights:
            if flight.id in excluded or not eligible(flight.date):
                continue
            # Explicit zero airborne time with no landings is a ground run;
            # unknown time on a historical flight is not evidence of no takeoff.
            if (
                flight.flight_time is not None
                and flight.flight_time <= 0
                and not (
                    flight.landing_count or flight.landings_day or flight.landings_night
                )
            ):
                continue
            matches = {
                key
                for key, first, last in codes.get(
                    (flight.arrival_icao or "").strip().upper(), []
                )
                if (first is None or flight.date >= first)
                and (last is None or flight.date <= last)
            }
            if len(matches) == 1:
                stamped.update(matches)
    if participant:
        visits = (
            db.session.query(PassportVisit.visit_date, PassportAirfield.airfield_id)
            .join(PassportAirfield, PassportVisit.target_id == PassportAirfield.id)
            .filter(PassportVisit.participant_id == participant.id)
            .all()
        )
        stamped.update(
            field_keys[airfield_id]
            for day, airfield_id in visits
            if airfield_id in field_keys and eligible(day)
        )
    denominator = sum(
        not field.get("is_closed") or field["key"] in stamped for field in fields
    )
    return {
        "visited": len(stamped),
        "total": denominator,
        "percent": round(100 * len(stamped) / denominator) if denominator else None,
    }


def create_personal(tenant_id: int, user_id: int, metadata: dict[str, Any]) -> Passport:
    user = require_actor(tenant_id, user_id, creating=True)
    passport = Passport(tenant_id=tenant_id, owner_user_id=user_id, **metadata)
    db.session.add(passport)
    db.session.flush()
    db.session.add(
        PassportParticipant(
            passport_id=passport.id,
            user_id=user_id,
            display_name=user.display_name,
            accepted_at=datetime.now(UTC),
        )
    )
    return passport


def set_archived(passport: Passport, archived: bool) -> None:
    passport.archived_at = datetime.now(UTC) if archived else None
