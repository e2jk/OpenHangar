"""Places in the account data export, and GPX/KML downloads of places (42g).

The export holds what the account may take with it: the places it owns
(with their tags and photo), its own visits (with each place's name), and the names of the collections it belongs to. Other
members' places and visits in a shared collection stay with them.
"""

from __future__ import annotations

from typing import Any

from flask import Response, abort, request, session
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionMember,
    PlaceTag,
    PlaceTagAssignment,
    PlaceVisit,
    db,
)
from services import account_exports, waypoints
from services.account_exports import ExportContext, ExportFile, ExportSection
from utils import current_membership, db_id, login_required

from places.access import PlaceError, collection_access
from places.airfields import linked_airfields
from places.management import photo_path
from places.queries import list_places
from places.routes import places_bp


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def places_section(tenant_id: int, user_id: int) -> ExportSection:
    collections = (
        db.session.query(PlaceCollection, PlaceCollectionMember.can_edit)
        .join(
            PlaceCollectionMember,
            PlaceCollectionMember.collection_id == PlaceCollection.id,
        )
        .filter(
            PlaceCollection.tenant_id == tenant_id,
            PlaceCollectionMember.tenant_id == tenant_id,
            PlaceCollectionMember.user_id == user_id,
        )
        .order_by(PlaceCollection.id)
        .all()
    )
    places = (
        Place.query.join(PlaceCollection, PlaceCollection.id == Place.collection_id)
        .filter(
            PlaceCollection.tenant_id == tenant_id,
            Place.owner_user_id == user_id,
            Place.deleted_at.is_(None),
        )
        .order_by(Place.id)
        .all()
    )
    tags: dict[int, list[str]] = {}
    for place_id, name in (
        db.session.query(PlaceTagAssignment.place_id, PlaceTag.name)
        .join(PlaceTag, PlaceTag.id == PlaceTagAssignment.tag_id)
        .filter(PlaceTagAssignment.place_id.in_([p.id for p in places]))
        .order_by(PlaceTag.name)
    ):
        tags.setdefault(place_id, []).append(name)
    visits = (
        db.session.query(PlaceVisit, Place.title)
        .join(Place, Place.id == PlaceVisit.place_id)
        .join(PlaceCollection, PlaceCollection.id == Place.collection_id)
        .filter(PlaceCollection.tenant_id == tenant_id, PlaceVisit.user_id == user_id)
        .order_by(PlaceVisit.visit_date, PlaceVisit.id)
        .all()
    )
    document: dict[str, Any] = {
        "collections": [
            {
                "id": c.id,
                "name": c.name,
                "personal": bool(c.is_personal),
                "can_edit": bool(can_edit),
            }
            for c, can_edit in collections
        ],
        "places": [],
        "visits": [
            {
                "place_id": visit.place_id,
                "place": title,
                "date": _iso(visit.visit_date),
                "note": visit.note,
                "flight_id": visit.flight_id,
            }
            for visit, title in visits
        ],
    }
    links: dict[int, list[dict[str, Any]]] = {}
    if places:
        for link in linked_airfields(
            tenant_id,
            user_id,
            place_ids=[p.id for p in places],
            include_archived=True,
        ):
            links.setdefault(link["place_id"], []).append(
                {
                    key: link[key]
                    for key in (
                        "name",
                        "ident",
                        "icao_code",
                        "latitude",
                        "longitude",
                        "distance_km",
                        "note",
                        "preferred",
                    )
                }
            )
    files: list[ExportFile] = []
    for place in places:
        entry: dict[str, Any] = {
            "id": place.id,
            "collection_id": place.collection_id,
            "title": place.title,
            "description": place.description,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "address": place.address,
            "external_url": place.external_url,
            "archived": bool(place.archived),
            "tags": tags.get(place.id, []),
            "airfields": links.get(place.id, []),
            "created_at": _iso(place.created_at),
            "updated_at": _iso(place.updated_at),
        }
        if place.photo_filename:
            entry["photo"] = f"place_photos/{place.id}.jpg"
            files.append(
                ExportFile(entry["photo"], photo_path(place.photo_filename), entry)
            )
        document["places"].append(entry)
    return ExportSection(document, files)


def _account_section(context: ExportContext) -> ExportSection:
    return places_section(context.tenant_id, context.user_id)


account_exports.register("places", _account_section)


@places_bp.route("/export.<fmt>")
@login_required
def export_places(fmt: str) -> ResponseReturnValue:
    """The located places of the list as filtered (collection, tag, visited,
    state), as GPX or KML waypoints: names and coordinates only."""
    if fmt not in waypoints.FORMATS:
        abort(404)
    membership = current_membership()
    if membership is None:
        abort(403)
    uid = int(session["user_id"])
    collection_id = request.args.get("collection", type=db_id)
    try:
        if collection_id is not None:
            collection_access(membership.tenant_id, uid, collection_id)
        listing = list_places(membership.tenant_id, uid, request.args, collection_id)
    except PlaceError:
        return abort(404)
    unnamed = _("Unnamed place")
    selection = waypoints.select(
        {
            "name": place.title or unnamed,
            "latitude": place.latitude,
            "longitude": place.longitude,
        }
        for place, _count, _last in listing["rows"]
        if place.latitude is not None
    )
    response = Response(
        waypoints.document(selection, fmt, label=_("Places")),
        mimetype=waypoints.FORMATS[fmt],
    )
    response.headers["Content-Disposition"] = f'attachment; filename="places.{fmt}"'
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
