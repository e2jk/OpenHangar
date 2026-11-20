"""Destination discovery: explicit homes, great-circle distance and still-air estimates.

No inferred home, tank capacity or operational suitability. All discovery inputs
are transient; only the aircraft/profile edit forms save defaults.
"""

import math
from typing import Any

from flask_babel import gettext as _
from models import (
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsStatus,
    PersonalMinimumsTag,
    db,
)
from utils import db_id

from passports import catalogue as cat
from passports.builder import available_fields
from passports.catalogue_access import visible_identity_ids
from passports.management import BuilderError


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
        raise BuilderError(_("Enter a valid %(label)s.", label=label)) from exc
    return number


def resolve_home(
    tenant_id: int, value: str, *, user_id: int | None = None
) -> PassportAirfieldIdentity | cat.CatalogueAirfield | None:
    value = value.strip()
    if not value:
        return None
    if len(value) > 80 or value.upper() == "ZZZZ":
        raise BuilderError(
            _("Choose an unambiguous airfield code or a saved field reference.")
        )
    visible = visible_identity_ids(tenant_id, user_id)
    private = (
        visible_identity_ids(tenant_id, user_id, private_metadata=True)
        if visible is not None
        else None
    )

    def candidate(
        field: PassportAirfieldIdentity,
    ) -> PassportAirfieldIdentity | cat.CatalogueAirfield:
        if (
            private is not None
            and not PassportAirfieldIdentity.query.filter(
                PassportAirfieldIdentity.id == field.id,
                PassportAirfieldIdentity.id.in_(private),
            ).first()
        ):
            return cat.public_candidate(field)
        return field

    try:
        if value.startswith("field:"):
            query = PassportAirfieldIdentity.query.filter_by(
                id=db_id(value[6:]), tenant_id=tenant_id
            )
            if visible is not None:
                query = query.filter(PassportAirfieldIdentity.id.in_(visible))
            result: PassportAirfieldIdentity | None = query.first()
            if result is None:
                raise ValueError
            return candidate(result)
        result = cat.resolve_code(
            tenant_id, value, airfield_ids=visible, private_airfield_ids=private
        )
        if result is not None:
            return candidate(result)
        matches = [
            row
            for row in cat.load_catalogue()
            if value.upper() in (row.ident, row.icao_code)
        ]
        if len(matches) == 1 and value.upper() != "ZZZZ":
            return matches[0]
    except (ValueError, cat.AirfieldConflict):
        # All invalid or ambiguous inputs use the same user-facing error below.
        pass
    raise BuilderError(
        _("Choose an unambiguous airfield code or a saved field reference.")
    )


def store_home(
    candidate: PassportAirfieldIdentity | cat.CatalogueAirfield | None, tenant_id: int
) -> int | None:
    if candidate is None:
        return None
    result: int = (
        candidate.id
        if isinstance(candidate, PassportAirfieldIdentity)
        else cat.materialise(tenant_id, candidate).id
    )
    return result


def home_value(owner: Any, tenant_id: int) -> str:
    field = owner.home_airfield if owner is not None else None
    # PilotProfile belongs to the user globally; never expose a home in another tenant.
    return (
        f"field:{field.id}"
        if field is not None and field.tenant_id == tenant_id
        else ""
    )


def home_descriptor(owner: Any, tenant_id: int) -> dict[str, Any] | None:
    value = home_value(owner, tenant_id)
    if not value:
        return None
    field = owner.home_airfield
    visible = visible_identity_ids(tenant_id)
    if (
        visible is not None
        and not PassportAirfieldIdentity.query.filter(
            PassportAirfieldIdentity.id == field.id,
            PassportAirfieldIdentity.id.in_(visible),
        ).first()
    ):
        return None
    private = (
        visible_identity_ids(tenant_id, private_metadata=True)
        if visible is not None
        else None
    )
    public = (
        private is not None
        and not PassportAirfieldIdentity.query.filter(
            PassportAirfieldIdentity.id == field.id,
            PassportAirfieldIdentity.id.in_(private),
        ).first()
    )
    data = cat.public_airfield(field) if public else cat.effective_airfield(field)
    return {**data, "key": value}


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
    progress: dict[str, Any],
    origin: dict[str, Any] | None,
    speed: float | None,
    limit: float,
    mode: str,
    range_nm: float | None,
) -> tuple[list[dict[str, Any]], int]:
    rows = []
    missing = 0
    for field in progress["rows"]:
        if field["visited"] or field.get("is_closed"):
            continue
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
        rows, key=lambda row: (row["distance_nm"], row["name"], row["target_id"])
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


def origin_choices(
    tenant_id: int, query: str, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    if len(query.strip()) < 2:
        return []
    words = query.casefold().split()
    # Deduplicate a catalogue row already materialised in this tenant.
    matches = {
        field["key"]: field
        for field in available_fields(tenant_id, user_id=user_id).values()
        if all(
            word in f"{field.get('ident') or ''} {field['name']}".casefold()
            for word in words
        )
    }
    return list(matches.values())[:50]


def latest_arrival(
    tenant_id: int, user_id: int, participant_id: int | None
) -> dict[str, Any] | None:
    """Most recent resolvable own full-stop arrival, independent of passport dates."""
    from datetime import date

    from models import Flight, LogbookEntryType, PassportArrivalExclusion

    from passports.eligibility import eligible_flight

    excluded = db.select(PassportArrivalExclusion.flight_id).where(
        PassportArrivalExclusion.participant_id == participant_id
    )
    flights = (
        Flight.query.filter(
            db.or_(
                Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
            ),
            Flight.entry_type == LogbookEntryType.FLIGHT,
            Flight.date <= date.today(),
            Flight.id.not_in(excluded),
        )
        .order_by(
            Flight.date.desc(), Flight.arrival_time.desc().nullslast(), Flight.id.desc()
        )
        .yield_per(200)
    )
    aliases: dict[str, list[Any]] = {}
    alias_query = (
        db.session.query(PassportAirfieldAlias, PassportAirfieldIdentity)
        .join(PassportAirfieldIdentity)
        .filter(PassportAirfieldIdentity.tenant_id == tenant_id)
    )
    visible = visible_identity_ids(tenant_id, user_id)
    private = (
        visible_identity_ids(tenant_id, user_id, private_metadata=True)
        if visible is not None
        else None
    )
    private_ids = set(db.session.scalars(private)) if private is not None else None
    if visible is not None:
        alias_query = alias_query.filter(
            PassportAirfieldIdentity.id.in_(visible),
            db.or_(
                PassportAirfieldAlias.catalogue_source_id.is_not(None),
                PassportAirfieldIdentity.id.in_(private),
            ),
        )
    for alias, field in alias_query.all():
        aliases.setdefault(alias.code, []).append((alias, field))
    sources: dict[str, list[cat.CatalogueAirfield]] = {}
    for row in cat.load_catalogue():
        for code in {row.ident, row.icao_code} - {None, ""}:
            sources.setdefault(str(code), []).append(row)
    for flight in flights:
        if not eligible_flight(flight):
            continue
        code = (flight.arrival_icao or "").strip().upper()
        if not code or code == "ZZZZ":
            continue
        if code in aliases:
            matches = {
                field.id: field
                for alias, field in aliases[code]
                if (alias.valid_from is None or flight.date >= alias.valid_from)
                and (alias.valid_until is None or flight.date <= alias.valid_until)
            }
            if len(matches) == 1:
                field = next(iter(matches.values()))
                data = (
                    cat.public_airfield(field)
                    if private_ids is not None and field.id not in private_ids
                    else cat.effective_airfield(field)
                )
                return {**data, "key": f"field:{field.id}"}
            continue
        rows = sources.get(code, [])
        if len(rows) == 1:
            row = rows[0]
            return {**vars(row), "key": "catalogue:" + row.source_id}
    return None
