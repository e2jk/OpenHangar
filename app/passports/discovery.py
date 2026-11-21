"""Destination discovery: explicit homes, great-circle distance and still-air estimates.

No inferred home, tank capacity or operational suitability. All discovery inputs
are transient; only the aircraft/profile edit forms save defaults.
"""

from typing import Any, cast

from models import (
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    db,
)
from services import destination_search as search
from services.destination_search import (
    distance_nm,
    reserve_preference,
    theoretical_range,
)
from utils import db_id

from passports import catalogue as cat
from passports.builder import available_fields
from passports.catalogue_access import visible_identity_ids
from passports.management import BuilderError

__all__ = ["distance_nm", "reserve_preference", "theoretical_range"]


def positive(
    value: str, label: str, maximum: float = 9999.99, *, zero: bool = False
) -> float | None:
    try:
        return search.positive(value, label, maximum, zero=zero)
    except search.DestinationError as exc:
        raise BuilderError(str(exc)) from exc


def resolve_home(
    tenant_id: int, value: str, *, user_id: int | None = None
) -> PassportAirfieldIdentity | cat.CatalogueAirfield | None:
    try:
        return cast(
            PassportAirfieldIdentity | cat.CatalogueAirfield | None,
            search.resolve_origin(
                value,
                lambda reference: _resolve_home(tenant_id, reference, user_id=user_id),
            ),
        )
    except search.DestinationError as exc:
        raise BuilderError(str(exc)) from exc


def _resolve_home(
    tenant_id: int, value: str, *, user_id: int | None = None
) -> PassportAirfieldIdentity | cat.CatalogueAirfield | None:
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
    return None


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


def nearby(
    progress: dict[str, Any],
    origin: dict[str, Any] | None,
    speed: float | None,
    limit: float,
    mode: str,
    range_nm: float | None,
) -> tuple[list[dict[str, Any]], int]:
    return search.nearby(
        (
            row
            for row in progress["rows"]
            if not row["visited"] and not row.get("is_closed")
        ),
        origin,
        speed,
        limit,
        mode,
        range_nm,
        tie_breaker=lambda row: row["target_id"],
    )


def origin_choices(
    tenant_id: int, query: str, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    # Keep the cheap short-query guard before loading the tenant catalogue.
    if len(query.strip()) < 2:
        return []
    return search.origin_choices(
        available_fields(tenant_id, user_id=user_id).values(), query
    )


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
