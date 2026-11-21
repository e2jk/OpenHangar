"""Retain shared content on departure; clean files only after a committed purge."""

import math
import re
from pathlib import Path
from typing import Any, cast

from flask import current_app
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionMember,
    PlacePreference,
    Tenant,
    TenantUser,
    User,
)
from services import file_cleanup
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


def photo_cascade_filter(obj: Any) -> ColumnElement[bool] | None:
    if isinstance(obj, PlaceCollection):
        return cast(ColumnElement[bool], Place.collection_id == obj.id)
    if isinstance(obj, Tenant):
        return cast(
            ColumnElement[bool],
            Place.collection_id.in_(
                select(PlaceCollection.id).where(PlaceCollection.tenant_id == obj.id)
            ),
        )
    return None


file_cleanup.register(
    Place,
    "photo_filename",
    lambda: Path(current_app.config["UPLOAD_FOLDER"]) / "place_photos",
    cascade_filter=photo_cascade_filter,
    error_label="place photo",
)


@event.listens_for(Session, "before_flush")
def prepare_places(session: Session, _context: Any, _instances: Any) -> None:
    for obj in (*session.new, *session.dirty):
        if isinstance(obj, Place):
            if not (
                isinstance(obj.latitude, int | float)
                and math.isfinite(obj.latitude)
                and -90 <= obj.latitude <= 90
                and isinstance(obj.longitude, int | float)
                and math.isfinite(obj.longitude)
                and -180 <= obj.longitude <= 180
            ):
                raise ValueError("Invalid place coordinates")
            if obj.photo_filename is not None and not re.fullmatch(
                r"[a-f0-9]{32}\.jpg", obj.photo_filename
            ):
                raise ValueError("Invalid place photo filename")
        if isinstance(obj, PlacePreference) and obj.default_collection_id is not None:
            collection = session.get(PlaceCollection, obj.default_collection_id)
            if collection is None or collection.tenant_id != obj.tenant_id:
                raise ValueError("Default collection belongs to another tenant")

    # ORM deletions of users, their tenant membership, or collection membership
    # must retain destinations. SET NULL handles audit fields and visit authors;
    # the owner is transferred before the departing membership is deleted.
    departures: set[tuple[int, int]] = set()
    for obj in session.deleted:
        if isinstance(obj, PlaceCollectionMember):
            departures.add((obj.collection_id, obj.user_id))
        elif isinstance(obj, User | TenantUser):
            uid = obj.id if isinstance(obj, User) else obj.user_id
            query = select(PlaceCollectionMember.collection_id).where(
                PlaceCollectionMember.user_id == uid
            )
            if isinstance(obj, TenantUser):
                query = query.where(PlaceCollectionMember.tenant_id == obj.tenant_id)
            departures.update((cid, uid) for cid in session.scalars(query))
    for collection_id, uid in departures:
        collection = session.get(PlaceCollection, collection_id)
        assert collection is not None
        if collection.created_by_user_id == uid:
            collection.created_by_user_id = None
        for place in session.scalars(
            select(Place).where(
                Place.collection_id == collection_id, Place.owner_user_id == uid
            )
        ):
            place.owner_user_id = collection.created_by_user_id
