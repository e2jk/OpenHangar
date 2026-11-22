"""Offline airfield suggestions and explicit selection for a place."""

import math
from dataclasses import asdict
from typing import Any

from flask_babel import gettext as _
from models import PassportAirfieldIdentity, Place, db, fits_column
from passports import catalogue
from services.destination_search import distance_nm
from sqlalchemy.exc import IntegrityError

from places import airfields
from places.access import PlaceError, actor, place_access
from places.coordinates import coordinate_pair

SUGGESTED_TYPES = frozenset(
    {"small_airport", "medium_airport", "large_airport", "seaplane_base"}
)


def available_airfields(tenant_id: int, user_id: int) -> dict[str, dict[str, Any]]:
    """One batch of visible identities and the cached public offline catalogue."""
    actor(tenant_id, user_id)
    private = airfields._private_ids(tenant_id, user_id)
    query = PassportAirfieldIdentity.query.filter_by(tenant_id=tenant_id)
    if private is not None:
        query = query.filter(
            db.or_(
                PassportAirfieldIdentity.source == "ourairports",
                PassportAirfieldIdentity.id.in_(private),
            )
        )
    fields = query.all()
    private_ids = set(db.session.scalars(private)) if private is not None else None
    result = {}
    sources = set()
    for field in fields:
        public = private_ids is not None and field.id not in private_ids
        data = (
            catalogue.public_airfield(field)
            if public
            else catalogue.effective_airfield(field)
        )
        key = f"field:{field.id}"
        result[key] = {
            **data,
            "id": field.id,
            "source_id": field.source_id,
            "source": field.source,
            "key": key,
            "public_catalogue": public,
        }
        if field.source == "ourairports":
            sources.add(field.source_id)
    for row in catalogue.load_catalogue():
        if row.source_id not in sources:
            key = f"catalogue:{row.source_id}"
            result[key] = {
                **asdict(row),
                "id": None,
                "source": "ourairports",
                "key": key,
                "public_catalogue": True,
            }
    return result


def distance_to(place: Place, field: dict[str, Any]) -> float | None:
    if (
        place.latitude is None
        or place.longitude is None
        or field.get("latitude") is None
        or field.get("longitude") is None
    ):
        return None
    return (
        distance_nm(
            place.latitude,
            place.longitude,
            float(field["latitude"]),
            float(field["longitude"]),
        )
        * 1.852
    )


def suggestions(
    tenant_id: int,
    user_id: int,
    place_id: int,
    *,
    query: str = "",
    radius_km: float = 15,
) -> list[dict[str, Any]]:
    place = place_access(tenant_id, user_id, place_id)
    if not math.isfinite(radius_km) or not 0 < radius_km <= 200:
        raise PlaceError(
            _("Choose a search radius greater than zero and at most 200 km.")
        )
    if len(query) > 200:
        raise PlaceError(_("Use a search of at most 200 characters."))
    words = query.casefold().split()
    if not words and place.latitude is None:
        return []
    rows = []
    for row in available_airfields(tenant_id, user_id).values():
        if words:
            haystack = f"{row['name']} {row.get('ident') or ''} {row.get('icao_code') or ''}".casefold()
            if not all(word in haystack for word in words):
                continue
        elif (
            row.get("is_closed")
            or row.get("airfield_type") in {"heliport", "closed"}
            or (
                row["source"] != "manual"
                and row.get("airfield_type") not in SUGGESTED_TYPES
            )
        ):
            continue
        distance = distance_to(place, row)
        if not words and (distance is None or distance > radius_km):
            continue
        rows.append({**row, "distance_km": distance})
    return sorted(
        rows,
        key=lambda row: (
            row["distance_km"] is None,
            row["distance_km"] or 0,
            row["name"].casefold(),
            row["key"],
        ),
    )[:200]


def select_airfield(tenant_id: int, user_id: int, place_id: int, key: str) -> None:
    place_access(tenant_id, user_id, place_id, editing=True)
    choice = available_airfields(tenant_id, user_id).get(key)
    if choice is None:
        raise PlaceError(_("Airfield not found."))
    field_id = choice["id"]
    if field_id is None:
        row = next(
            row
            for row in catalogue.load_catalogue()
            if row.source_id == choice["source_id"]
        )
        try:
            with db.session.begin_nested():
                field_id = catalogue.materialise(tenant_id, row).id
        except (IntegrityError, catalogue.AirfieldConflict) as exc:
            # Another selection can win at the insert or the alias check.
            winner = PassportAirfieldIdentity.query.filter_by(
                tenant_id=tenant_id, source="ourairports", source_id=row.source_id
            ).first()
            if winner is None:
                if isinstance(exc, catalogue.AirfieldConflict):
                    raise PlaceError(
                        _(
                            "This airfield conflicts with an existing code. Review the catalogue first."
                        )
                    ) from None
                raise
            field_id = winner.id
    airfields.add_link(
        tenant_id,
        user_id,
        place_id,
        field_id,
        public_catalogue=choice["public_catalogue"],
    )


def create_manual_airfield(
    tenant_id: int,
    user_id: int,
    place_id: int,
    *,
    name: str,
    code: str = "",
    latitude: str = "",
    longitude: str = "",
) -> None:
    place_access(tenant_id, user_id, place_id, editing=True)
    name, code = name.strip(), code.strip()
    if not name or not fits_column(PassportAirfieldIdentity.name, name):
        raise PlaceError(_("Enter an airfield name of at most 255 characters."))
    if not fits_column(PassportAirfieldIdentity.ident, code):
        raise PlaceError(_("Use an airfield code of at most 64 characters."))
    lat, lon = (
        coordinate_pair(latitude, longitude)
        if latitude.strip() or longitude.strip()
        else (None, None)
    )
    try:
        field = catalogue.create_custom(
            tenant_id, name, code=code, latitude=lat, longitude=lon
        )
    except catalogue.AirfieldConflict:
        raise PlaceError(
            _("That code cannot be used. Choose another code or leave it blank.")
        ) from None
    # Creating the identity and sharing it through this place are one command.
    # The editor explicitly supplies its metadata, so this new identity needs no
    # existing passport reference to grant access to its own first place link.
    from models import PlaceAirfield

    place = place_access(tenant_id, user_id, place_id, editing=True)
    db.session.add(
        PlaceAirfield(
            tenant_id=tenant_id,
            collection_id=place.collection_id,
            place_id=place.id,
            airfield_id=field.id,
        )
    )
    airfields._touch(place, user_id)
    db.session.flush()
