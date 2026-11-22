"""One scoped query drives both the Places list and its map."""

from typing import Any

from models import (
    Place,
    PlaceAirfield,
    PlaceCollection,
    PlaceCollectionMember,
    PlaceTag,
    PlaceTagAssignment,
    PlaceVisit,
    db,
)
from sqlalchemy import func
from werkzeug.datastructures import MultiDict

from places.access import actor, collections_for_user


def list_places(
    tenant_id: int, user_id: int, args: MultiDict[str, str], collection_id: int | None
) -> dict[str, Any]:
    from utils import db_id

    actor(tenant_id, user_id)
    choices = (
        collections_for_user(tenant_id, user_id)
        .order_by(PlaceCollection.name, PlaceCollection.id)
        .all()
    )
    cids = [c.id for c in choices]
    visits = (
        db.session.query(
            PlaceVisit.place_id,
            func.count().label("count"),
            func.max(PlaceVisit.visit_date).label("last_visit"),
        )
        .filter(PlaceVisit.user_id == user_id)
        .group_by(PlaceVisit.place_id)
        .subquery()
    )
    count = func.coalesce(visits.c.count, 0)
    query = (
        db.session.query(Place, count, visits.c.last_visit)
        .outerjoin(visits, visits.c.place_id == Place.id)
        .filter(Place.collection_id.in_(cids))
    )
    if collection_id is not None:
        query = query.filter(Place.collection_id == collection_id)
    state = args.get("state", "active")
    if state == "bin":
        editing = db.select(PlaceCollectionMember.collection_id).where(
            PlaceCollectionMember.tenant_id == tenant_id,
            PlaceCollectionMember.user_id == user_id,
            PlaceCollectionMember.can_edit.is_(True),
        )
        query = query.filter(
            Place.deleted_at.is_not(None), Place.collection_id.in_(editing)
        )
    else:
        query = query.filter(Place.deleted_at.is_(None))
        if state == "archived":
            query = query.filter(Place.archived.is_(True))
        elif state != "all":
            state = "active"
            query = query.filter(Place.archived.is_(False))
    linked = args.get("linked", "all")
    linked_places = db.select(PlaceAirfield.place_id)
    if linked == "linked":
        query = query.filter(Place.id.in_(linked_places))
    elif linked == "unlinked":
        query = query.filter(Place.id.not_in(linked_places))
    else:
        linked = "all"
    location = args.get("location", "all")
    if location == "missing":
        query = query.filter(Place.latitude.is_(None))
    elif location == "located":
        query = query.filter(Place.latitude.is_not(None))
    else:
        location = "all"
    visited = args.get("visited", "all")
    if visited == "visited":
        query = query.filter(count > 0)
    elif visited == "unvisited":
        query = query.filter(count == 0)
    else:
        visited = "all"
    tag_id = args.get("tag", type=db_id)
    if tag_id is not None:
        query = query.filter(
            Place.id.in_(
                db.select(PlaceTagAssignment.place_id).where(
                    PlaceTagAssignment.tag_id == tag_id
                )
            )
        )
    sort = args.get("sort", "name")
    if sort == "added":
        query = query.order_by(Place.created_at.desc(), Place.id.desc())
    elif sort == "visited":
        query = query.order_by(
            visits.c.last_visit.desc().nulls_last(), Place.title, Place.id
        )
    else:
        sort = "name"
        query = query.order_by(Place.title, Place.id)
    rows = query.all()
    tags = (
        PlaceTag.query.filter(PlaceTag.collection_id.in_(cids))
        .order_by(PlaceTag.name, PlaceTag.id)
        .all()
    )
    assignments = (
        db.session.query(PlaceTagAssignment.place_id, PlaceTag.name)
        .join(PlaceTag, PlaceTag.id == PlaceTagAssignment.tag_id)
        .filter(
            PlaceTagAssignment.place_id.in_([place.id for place, _count, _last in rows])
        )
        .order_by(PlaceTag.name)
        .all()
    )
    by_place: dict[int, list[str]] = {}
    for place_id, name in assignments:
        by_place.setdefault(place_id, []).append(name)
    return {
        "rows": rows,
        "linked": linked,
        "linked_place_ids": set(
            db.session.scalars(
                db.select(PlaceAirfield.place_id).where(
                    PlaceAirfield.place_id.in_(
                        [place.id for place, _count, _last in rows]
                    )
                )
            )
        ),
        "location": location,
        "tags": tags,
        "place_tags": by_place,
        "state": state,
        "visited": visited,
        "sort": sort,
        "tag_id": tag_id,
    }
