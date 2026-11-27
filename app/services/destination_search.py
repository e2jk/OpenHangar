"""Source-independent destination filters, origin lookup and still-air estimates.

Callers provide authorised candidate rows and origin resolvers. This module
never infers an itinerary, chooses a tenant catalogue scope or grants access.
"""

import math
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from flask_babel import gettext as _
from models import (
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsStatus,
    PersonalMinimumsTag,
    db,
)

T = TypeVar("T")


class DestinationError(ValueError):
    """Invalid discovery input with a translated message for a form."""


def positive(
    value: str, label: str, maximum: float = 9999.99, *, zero: bool = False
) -> float | None:
    if not value.strip():
        return None
    try:
        number = float(value)
        if (
            not math.isfinite(number)
            or number > maximum
            or number < 0
            or (not zero and number == 0)
        ):
            raise ValueError
    except ValueError as exc:
        raise DestinationError(_("Enter a valid %(label)s.", label=label)) from exc
    return number


def resolve_origin(value: str, resolver: Callable[[str], T | None]) -> T | None:
    value = value.strip()
    if not value:
        return None
    if len(value) <= 80 and value.upper() != "ZZZZ":
        try:
            candidate = resolver(value)
            if candidate is not None:
                return candidate
        except ValueError:
            # Resolver rejections and missing matches share the actionable error below.
            pass
    raise DestinationError(
        _("Choose an unambiguous airfield code or a saved field reference.")
    )


def distance_nm(lat: float, lon: float, other_lat: float, other_lon: float) -> float:
    a, b = math.radians(lat), math.radians(other_lat)
    dlat, dlon = b - a, math.radians(other_lon - lon)
    hav = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(min(1, max(0, hav))))


def reserve_preference(user_id: int) -> float | None:
    rows = (
        db.session.query(PersonalMinimumsItem.numeric_value)
        .join(
            PersonalMinimumsSection,
            PersonalMinimumsItem.section_id == PersonalMinimumsSection.id,
        )
        .join(
            PersonalMinimumsRevision,
            PersonalMinimumsSection.revision_id == PersonalMinimumsRevision.id,
        )
        .filter(
            PersonalMinimumsRevision.user_id == user_id,
            PersonalMinimumsRevision.status == PersonalMinimumsStatus.ACTIVE,
            PersonalMinimumsItem.semantic_tag
            == PersonalMinimumsTag.MIN_FUEL_RESERVE_MINUTES,
        )
        .all()
    )
    values = [
        float(row[0]) for row in rows if row[0] is not None and 0 <= row[0] <= 1440
    ]
    # Conflicting sections use the most conservative published preference.
    return max(values) if values else None


def nearby(
    candidates: Iterable[dict[str, Any]],
    origin: dict[str, Any] | None,
    speed: float | None,
    limit: float,
    mode: str,
    range_nm: float | None,
    *,
    tie_breaker: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    rows = []
    missing = 0
    for field in candidates:
        if field.get("latitude") is None or field.get("longitude") is None:
            missing += 1
            continue
        if (
            origin is None
            or origin.get("latitude") is None
            or origin.get("longitude") is None
        ):
            continue
        distance = distance_nm(
            float(origin["latitude"]),
            float(origin["longitude"]),
            float(field["latitude"]),
            float(field["longitude"]),
        )
        minutes = distance / speed * 60 if speed is not None else None
        if (
            (mode == "time" and (minutes is None or minutes > limit))
            or (mode == "distance" and distance > limit)
            or (range_nm is not None and distance > range_nm)
        ):
            continue
        rows.append({**field, "distance_nm": distance, "minutes": minutes})
    return sorted(
        rows,
        key=lambda row: (
            row["distance_nm"],
            row["name"],
            tie_breaker(row) if tie_breaker is not None else row.get("key", ""),
        ),
    ), missing


def theoretical_range(
    speed: float | None,
    fuel: float | None,
    flow: float | None,
    reserve: float | None,
    returning: bool,
) -> float | None:
    if (
        speed is None
        or fuel is None
        or flow is None
        or reserve is None
        or not all(math.isfinite(value) for value in (speed, fuel, flow, reserve))
        or speed <= 0
        or fuel <= 0
        or flow <= 0
        or reserve < 0
        or fuel / flow <= reserve / 60
    ):
        return None
    return speed * (fuel / flow - reserve / 60) / (2 if returning else 1)


def origin_choices(rows: Iterable[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if len(query.strip()) < 2:
        return []
    words = query.casefold().split()
    matches = {
        field["key"]: field
        for field in rows
        if all(
            word
            in " ".join(
                (field.get("ident") or "", field.get("icao_code") or "", field["name"])
            ).casefold()
            for word in words
        )
    }
    return list(matches.values())[:50]
