"""Connect private Places collections with the current user's passport airfields."""

from collections import defaultdict
from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    Place,
    PlaceAirfield,
    PlaceCollection,
    PlaceCollectionMember,
    db,
)
from passports import catalogue
from passports.access import passports_for_user
from services.destination_search import DestinationError, positive
from utils import is_view_only

from places.candidates import distance_to
from places.presentation import place_label


def passport_fields(tenant_id: int, user_id: int) -> dict[int, dict[str, Any]]:
    """Resolve airfields only through passports accessible to this account."""
    accessible = passports_for_user(tenant_id, user_id).with_entities(Passport.id)
    rows = (
        db.session.query(PassportAirfieldIdentity, PassportAirfield, Passport.title)
        .join(
            PassportAirfield,
            PassportAirfield.airfield_id == PassportAirfieldIdentity.id,
        )
        .join(Passport, Passport.id == PassportAirfield.passport_id)
        .filter(PassportAirfield.tenant_id == tenant_id, Passport.id.in_(accessible))
        .order_by(Passport.title, Passport.id)
        .all()
    )
    titles: dict[int, set[str]] = defaultdict(set)
    fields: dict[int, dict[str, Any]] = {}
    for identity, target, title in rows:
        titles[identity.id].add(title)
        if identity.id not in fields or not target.public_catalogue:
            data = (
                catalogue.public_airfield(identity)
                if target.public_catalogue
                else catalogue.effective_airfield(identity)
            )
            fields[identity.id] = {**data, "id": identity.id}
    for field_id, field in fields.items():
        field["passports"] = sorted(titles[field_id], key=str.casefold)
    return fields


def search_radius(value: str) -> tuple[float, str | None]:
    try:
        return positive(value, _("search radius"), maximum=200) or 15, None
    except DestinationError as exc:
        return 15, str(exc)


def nearby_places(
    tenant_id: int, user_id: int, fields: dict[int, dict[str, Any]], radius: float
) -> list[dict[str, Any]]:
    if not fields or is_view_only(user_id, tenant_id):
        return []
    places = (
        db.session.query(Place, PlaceCollection.name)
        .join(PlaceCollection, PlaceCollection.id == Place.collection_id)
        .join(
            PlaceCollectionMember,
            PlaceCollectionMember.collection_id == Place.collection_id,
        )
        .filter(
            PlaceCollection.tenant_id == tenant_id,
            PlaceCollectionMember.tenant_id == tenant_id,
            PlaceCollectionMember.user_id == user_id,
            PlaceCollectionMember.can_edit.is_(True),
            Place.deleted_at.is_(None),
            Place.archived.is_(False),
            Place.latitude.is_not(None),
            Place.longitude.is_not(None),
        )
        .all()
    )
    links = set(
        db.session.query(PlaceAirfield.place_id, PlaceAirfield.airfield_id)
        .filter(
            PlaceAirfield.tenant_id == tenant_id, PlaceAirfield.airfield_id.in_(fields)
        )
        .all()
    )
    suggestions = []
    for place, collection_name in places:
        for airfield_id, field in fields.items():
            if (place.id, airfield_id) in links:
                continue
            distance = distance_to(place, field)
            if distance is not None and distance <= radius:
                suggestions.append(
                    {
                        "place_id": place.id,
                        "place_name": place_label(place),
                        "collection_name": collection_name,
                        "field": field,
                        "distance": distance,
                    }
                )
    return sorted(
        suggestions,
        key=lambda row: (row["distance"], row["place_id"], row["field"]["id"]),
    )
