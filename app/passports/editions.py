"""Explicitly linked editions; no recurrence rules or inferred relationships."""

from datetime import date
from typing import Any

from flask_babel import gettext as _
from models import Passport, PassportSeries, db, fits_column

from passports.eligibility import progress_many
from passports.management import (
    BuilderError,
    fingerprint,
    owned_passport,
    require_actor,
)

COLOURS = (
    "#0072b2",
    "#d55e00",
    "#009e73",
    "#cc79a7",
    "#e69f00",
    "#56b4e9",
    "#332288",
    "#882255",
)


def ordered(passports: list[Passport]) -> list[Passport]:
    return sorted(
        passports,
        key=lambda p: (
            p.start_date is None and p.end_date is None,
            p.start_date or date.min,
            p.series_display_order,
            p.id,
        ),
    )


def current_edition(
    passports: list[Passport], *, today: date | None = None
) -> Passport | None:
    day = today or date.today()
    candidates = [
        p
        for p in passports
        if p.archived_at is None
        and (p.start_date is not None or p.end_date is not None)
        and (p.start_date is None or p.start_date <= day)
        and (p.end_date is None or p.end_date >= day)
    ]
    return max(candidates, key=lambda p: (p.start_date or date.min, p.id), default=None)


def latest_edition(passports: list[Passport]) -> Passport | None:
    dated = [p for p in passports if p.start_date is not None or p.end_date is not None]
    return (
        max(dated, key=lambda p: (p.start_date or date.min, p.id), default=None)
        if dated
        else next(iter(reversed(ordered(passports))), None)
    )


def owned_series(tenant_id: int, user_id: int, series_id: int) -> PassportSeries:
    require_actor(tenant_id, user_id)
    series: PassportSeries | None = (
        PassportSeries.query.filter_by(
            id=series_id,
            tenant_id=tenant_id,
            owner_user_id=user_id,
        )
        .with_for_update()
        .first()
    )
    if series is None:
        raise BuilderError(_("Passport series not found."), 404)
    return series


def link(
    passport: Passport, user_id: int, *, series_id: int | None = None, title: str = ""
) -> PassportSeries:
    owned_passport(passport.tenant_id, user_id, passport.id, archived_ok=True)
    if series_id is not None:
        series = owned_series(passport.tenant_id, user_id, series_id)
    else:
        title = title.strip()
        if not title or not fits_column(PassportSeries.title, title):
            raise BuilderError(_("Enter a series name of at most 128 characters."))
        series = PassportSeries(
            tenant_id=passport.tenant_id, owner_user_id=user_id, title=title
        )
        db.session.add(series)
        db.session.flush()
    # Linking changes neither targets nor evidence. Explicit order for undated editions.
    last = (
        db.session.query(db.func.max(Passport.series_display_order))
        .filter_by(series_id=series.id)
        .scalar()
    )
    passport.series_id = series.id
    passport.series_display_order = (last or 0) + 1
    return series


def validate_copy(tenant_id: int, user_id: int, context: dict[str, Any]) -> Passport:
    source = owned_passport(tenant_id, user_id, context["source_id"], archived_ok=True)
    if fingerprint(source) != context["fingerprint"]:
        raise BuilderError(_("The source edition changed. Start the copy again."), 409)
    return source


def finish_copy(passport: Passport, user_id: int, context: dict[str, Any]) -> None:
    source = validate_copy(passport.tenant_id, user_id, context)
    series = (
        link(source, user_id, series_id=source.series_id, title=context["series_title"])
        if source.series_id is None
        else owned_series(passport.tenant_id, user_id, source.series_id)
    )
    link(passport, user_id, series_id=series.id)


def combined(
    passports: list[Passport],
    user_id: int,
    *,
    oldest: bool = False,
    current: Passport | None = None,
    colours: dict[int, str] | None = None,
    progress: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Own evidence only. Edition priority affects colour, never counts or stamps."""
    editions = ordered(passports)
    colours = colours or {
        p.id: COLOURS[i % len(COLOURS)] for i, p in enumerate(editions)
    }
    progress = progress_many(editions, user_id) if progress is None else progress
    fields: dict[int, dict[str, Any]] = {}
    seen: dict[int, set[tuple[str, int]]] = {}
    target_sets: dict[int, set[int]] = {}
    comparisons = []
    previous: set[int] = set()
    for passport in editions:
        target_sets[passport.id] = set()
        for row in progress[passport.id]["rows"]:
            identity = row["id"]
            target_sets[passport.id].add(identity)
            if identity not in fields:
                fields[identity] = {
                    key: row.get(key)
                    for key in (
                        "id",
                        "ident",
                        "name",
                        "latitude",
                        "longitude",
                        "is_closed",
                    )
                }
                fields[identity].update(
                    editions=[],
                    count=0,
                    visited=False,
                    colour=None,
                    edition_label=None,
                    current=False,
                )
                seen[identity] = set()
            field = fields[identity]
            field["current"] = field["current"] or (
                current is not None and passport.id == current.id
            )
            label = passport.edition_label or passport.title
            field["editions"].append(
                {
                    "id": passport.id,
                    "label": label,
                    "count": row["count"],
                    "colour": colours[passport.id],
                    "colour_class": "passport-edition-colour-"
                    + str(COLOURS.index(colours[passport.id])),
                }
            )
            if row["visited"]:
                field["visited"] = True
                if field["colour"] is None or not oldest:
                    field["colour"], field["edition_label"] = (
                        colours[passport.id],
                        label,
                    )
            for event in row["events"]:
                if not event["excluded"]:
                    seen[identity].add(
                        ("flight", event["flight_id"])
                        if event["flight_id"] is not None
                        else ("visit", event["id"])
                    )
        targets = target_sets[passport.id]
        comparisons.append(
            {
                "passport": passport,
                "progress": progress[passport.id],
                "added": len(targets - previous),
                "removed": len(previous - targets),
                "colour": colours[passport.id],
                "colour_class": "passport-edition-colour-"
                + str(COLOURS.index(colours[passport.id])),
            }
        )
        previous = targets
    for identity, field in fields.items():
        field["count"] = len(seen[identity])
    return {
        "rows": list(fields.values()),
        "comparisons": comparisons,
        "progress": progress,
        "colours": colours,
    }
