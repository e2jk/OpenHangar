"""Collection-scoped airfield links and the Places source for route planning."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from flask_babel import gettext as _
from models import (
    PassportAirfieldIdentity,
    Place,
    PlaceAirfield,
    PlaceCollectionMember,
    PlaceVisit,
    db,
    fits_column,
)
from passports import catalogue
from passports.catalogue_access import visible_identity_ids
from services.destination_search import distance_nm

from places.access import PlaceError, actor, place_access
from places.presentation import place_label


def _private_ids(tenant_id: int, user_id: int) -> Any:
    """Private metadata needs passport access or an explicitly shared place link."""
    permitted = visible_identity_ids(tenant_id, user_id, private_metadata=True)
    if permitted is None:
        return None
    shared = (
        db.select(PlaceAirfield.airfield_id)
        .join(Place, Place.id == PlaceAirfield.place_id)
        .join(
            PlaceCollectionMember,
            PlaceCollectionMember.collection_id == Place.collection_id,
        )
        .where(
            PlaceAirfield.tenant_id == tenant_id,
            PlaceCollectionMember.tenant_id == tenant_id,
            PlaceCollectionMember.user_id == user_id,
            PlaceAirfield.public_catalogue.is_(False),
            Place.deleted_at.is_(None),
        )
    )
    return permitted.union(shared)


def _metadata(
    tenant_id: int, user_id: int, airfield_id: int, public: bool
) -> tuple[PassportAirfieldIdentity, bool]:
    query = PassportAirfieldIdentity.query.filter_by(
        tenant_id=tenant_id, id=airfield_id
    )
    if public:
        query = query.filter(PassportAirfieldIdentity.source == "ourairports")
    else:
        permitted = _private_ids(tenant_id, user_id)
        if permitted is not None:
            query = query.filter(PassportAirfieldIdentity.id.in_(permitted))
    field = query.first()
    if field is None:
        raise PlaceError(_("Airfield not found."))
    return field, public


def _note(value: str) -> str | None:
    value = value.strip()
    if not fits_column(PlaceAirfield.note, value):
        raise PlaceError(_("Use a getting-there note of at most 2,000 characters."))
    return value or None


def _touch(place: Place, user_id: int) -> None:
    place.updated_by_user_id = user_id
    place.updated_at = datetime.now(UTC)


def add_link(
    tenant_id: int,
    user_id: int,
    place_id: int,
    airfield_id: int,
    *,
    public_catalogue: bool = False,
    note: str = "",
    preferred: bool = False,
) -> PlaceAirfield:
    """Link an identity the editor may share; never grant private data by ID."""
    place = place_access(tenant_id, user_id, place_id, editing=True)
    field, public = _metadata(tenant_id, user_id, airfield_id, public_catalogue)
    text = _note(note)
    link = db.session.get(PlaceAirfield, (place_id, field.id))
    if link is None:
        link = PlaceAirfield(
            tenant_id=tenant_id,
            collection_id=place.collection_id,
            place_id=place.id,
            airfield_id=field.id,
            public_catalogue=public,
            note=text,
            preferred=preferred,
        )
        db.session.add(link)
        _touch(place, user_id)
        db.session.flush()
    return link


def update_link(
    tenant_id: int,
    user_id: int,
    place_id: int,
    airfield_id: int,
    *,
    note: str = "",
    preferred: bool = False,
) -> PlaceAirfield:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    link = db.session.get(PlaceAirfield, (place_id, airfield_id))
    if link is None:
        raise PlaceError(_("Airfield not found."))
    link.note = _note(note)
    link.preferred = preferred
    _touch(place, user_id)
    return link


def remove_link(tenant_id: int, user_id: int, place_id: int, airfield_id: int) -> None:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    link = db.session.get(PlaceAirfield, (place_id, airfield_id))
    if link is None:
        raise PlaceError(_("Airfield not found."))
    db.session.delete(link)
    _touch(place, user_id)


def linked_airfields(
    tenant_id: int,
    user_id: int,
    collection_ids: Sequence[int] | None = None,
    *,
    include_visited: bool = True,
    place_ids: Sequence[int] | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Return member-visible links, without flights, photos or others' visits.

    One row per place/airfield pair. Filters only narrow membership-scoped
    results; even an administrator cannot use collection IDs to widen access.
    The default source is active, non-binned places. Maps can explicitly include
    archived places and narrow to the place IDs their filtered list displays.
    """
    actor(tenant_id, user_id)
    visited = db.exists().where(
        PlaceVisit.place_id == Place.id, PlaceVisit.user_id == user_id
    )
    query = (
        db.session.query(PlaceAirfield, Place, PassportAirfieldIdentity, visited)
        .select_from(PlaceAirfield)
        .join(Place, Place.id == PlaceAirfield.place_id)
        .join(
            PassportAirfieldIdentity,
            PassportAirfieldIdentity.id == PlaceAirfield.airfield_id,
        )
        .join(
            PlaceCollectionMember,
            PlaceCollectionMember.collection_id == Place.collection_id,
        )
        .filter(
            PlaceAirfield.tenant_id == tenant_id,
            PlaceCollectionMember.tenant_id == tenant_id,
            PlaceCollectionMember.user_id == user_id,
            Place.deleted_at.is_(None),
        )
    )
    if not include_archived:
        query = query.filter(Place.archived.is_(False))
    if collection_ids is not None:
        query = query.filter(Place.collection_id.in_(collection_ids))
    if place_ids is not None:
        query = query.filter(Place.id.in_(place_ids))
    if not include_visited:
        query = query.filter(~visited)
    result = []
    for link, place, field, seen in query.order_by(Place.id, PlaceAirfield.airfield_id):
        data = (
            catalogue.public_airfield(field)
            if link.public_catalogue
            else catalogue.effective_airfield(field)
        )
        latitude = float(data["latitude"]) if data["latitude"] is not None else None
        longitude = float(data["longitude"]) if data["longitude"] is not None else None
        distance = None
        if (
            place.latitude is not None
            and place.longitude is not None
            and latitude is not None
            and longitude is not None
        ):
            distance = (
                distance_nm(place.latitude, place.longitude, latitude, longitude)
                * 1.852
            )
        result.append(
            {
                "place_id": place.id,
                "place_name": place_label(place),
                "collection_id": place.collection_id,
                "place_latitude": place.latitude,
                "place_longitude": place.longitude,
                "airfield_id": field.id,
                "key": f"field:{field.id}",
                "name": data["name"],
                "ident": data["ident"],
                "icao_code": data["icao_code"],
                "latitude": latitude,
                "longitude": longitude,
                "airfield_type": data["airfield_type"],
                "is_closed": data["is_closed"],
                "distance_km": distance,
                "note": link.note or "",
                "preferred": bool(link.preferred),
                "visited": bool(seen),
                "public_catalogue": bool(link.public_catalogue),
            }
        )
    return result
