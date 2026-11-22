"""Place edits are governed by collections; visits never move without their people."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app
from flask_babel import gettext as _
from models import (
    Place,
    PlaceCollectionMember,
    PlaceTag,
    PlaceTagAssignment,
    PlaceVisit,
    db,
    fits_column,
)
from pwa.shared_links import clean_link
from services import images
from werkzeug.datastructures import FileStorage

from places.access import PlaceError, collection_access, place_access
from places.coordinates import coordinate_pair, parse_coordinates


def photo_path(filename: str) -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]) / "place_photos" / filename


def place_values(data: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, maximum in (
        ("title", 200),
        ("description", 8000),
        ("address", 500),
        ("external_url", 2048),
    ):
        text = data.get(key, "").strip()
        if len(text) > maximum or not fits_column(getattr(Place, key), text):
            raise PlaceError(_("A place field is too long."))
        result[key] = text or None
    if result["external_url"]:
        cleaned = clean_link(result["external_url"])
        if cleaned is None:
            raise PlaceError(_("Enter a full http or https website address."))
        result["external_url"] = cleaned
    if data.get("coordinates", "").strip():
        lat, lon = parse_coordinates(data["coordinates"])
    elif data.get("latitude", "").strip() or data.get("longitude", "").strip():
        lat, lon = coordinate_pair(data.get("latitude", ""), data.get("longitude", ""))
    else:
        lat, lon = None, None
    result.update(latitude=lat, longitude=lon)
    return result


def set_tags(tenant_id: int, user_id: int, place_id: int, names: Sequence[str]) -> None:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    place.updated_by_user_id = user_id
    place.updated_at = datetime.now(UTC)
    clean = sorted({name.strip() for name in names if name.strip()})
    if any(len(name) > 80 for name in clean):
        raise PlaceError(_("Use tag names of at most 80 characters."))
    PlaceTagAssignment.query.filter_by(place_id=place.id).delete(
        synchronize_session="fetch"
    )
    for name in clean:
        tag = PlaceTag.query.filter_by(
            collection_id=place.collection_id, name=name
        ).first()
        if tag is None:
            tag = PlaceTag(collection_id=place.collection_id, name=name)
            db.session.add(tag)
            db.session.flush()
        db.session.add(
            PlaceTagAssignment(
                collection_id=place.collection_id, place_id=place.id, tag_id=tag.id
            )
        )


def tag_names(place_id: int) -> list[str]:
    """Trusted helper: caller has already checked access to the place."""
    return [
        tag.name
        for tag in PlaceTag.query.join(
            PlaceTagAssignment, PlaceTagAssignment.tag_id == PlaceTag.id
        )
        .filter(PlaceTagAssignment.place_id == place_id)
        .order_by(PlaceTag.name)
    ]


def save_place(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    data: Mapping[str, str],
    *,
    place_id: int | None = None,
    tags: Sequence[str] = (),
    photo: FileStorage | None = None,
    remove_photo: bool = False,
) -> Place:
    collection_access(tenant_id, user_id, collection_id, editing=True)
    values = place_values(data)
    if place_id is None:
        place = Place(
            collection_id=collection_id,
            owner_user_id=user_id,
            created_by_user_id=user_id,
        )
        db.session.add(place)
    else:
        place = place_access(tenant_id, user_id, place_id, editing=True)
        if place.collection_id != collection_id:
            raise PlaceError(_("Use Move to change a place's collection."))
    for key, value in values.items():
        setattr(place, key, value)
    place.updated_by_user_id = user_id
    new_file: Path | None = None
    try:
        if photo is not None and photo.filename:
            try:
                cleaned = images.clean_photo(photo)
            except images.PhotoError as exc:
                raise PlaceError(str(exc)) from None
            place.photo_filename = images.store(photo_path(""), cleaned)
            new_file = photo_path(place.photo_filename)
        elif remove_photo:
            place.photo_filename = None
        db.session.flush()
        set_tags(tenant_id, user_id, place.id, tags)
        db.session.flush()
    except Exception:
        # Old files are kept by the transaction-aware cleanup registry. The new
        # file has no committed owner if saving failed, so remove only that one.
        if new_file is not None:
            new_file.unlink(missing_ok=True)
        raise
    return place


def copy_place(tenant_id: int, user_id: int, place_id: int, target_id: int) -> Place:
    source = place_access(tenant_id, user_id, place_id, editing=True)
    collection_access(tenant_id, user_id, target_id, editing=True)
    copy = save_place(
        tenant_id,
        user_id,
        target_id,
        {
            "title": source.title or "",
            "description": source.description or "",
            "address": source.address or "",
            "external_url": source.external_url or "",
            "latitude": str(source.latitude) if source.latitude is not None else "",
            "longitude": str(source.longitude) if source.longitude is not None else "",
        },
        tags=tag_names(source.id),
    )
    if source.photo_filename:
        try:
            data = photo_path(source.photo_filename).read_bytes()
        except OSError:
            raise PlaceError(_("The place photo could not be copied.")) from None
        copy.photo_filename = images.store(photo_path(""), data)
        path = photo_path(copy.photo_filename)
        try:
            db.session.flush()
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return copy


def move_place(tenant_id: int, user_id: int, place_id: int, target_id: int) -> Place:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    collection_access(tenant_id, user_id, target_id, editing=True)
    if place.owner_user_id != user_id:
        raise PlaceError(_("Only the place owner can move it."))
    if place.collection_id == target_id:
        return place
    visitors = {row.user_id for row in PlaceVisit.query.filter_by(place_id=place.id)}
    members = {
        row.user_id
        for row in PlaceCollectionMember.query.filter_by(collection_id=target_id)
    }
    if visitors - members:
        raise PlaceError(
            _(
                "Every visitor must belong to the destination collection. Copy this place instead."
            )
        )
    names = tag_names(place.id)
    PlaceTagAssignment.query.filter_by(place_id=place.id).delete(
        synchronize_session="fetch"
    )
    db.session.flush()
    place.collection_id = target_id
    place.updated_by_user_id = user_id
    db.session.flush()
    set_tags(tenant_id, user_id, place.id, names)
    return place


def archive_place(tenant_id: int, user_id: int, place_id: int, archived: bool) -> None:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    place.archived = archived
    place.updated_by_user_id = user_id
    place.updated_at = datetime.now(UTC)


def trash_place(tenant_id: int, user_id: int, place_id: int) -> None:
    place = place_access(tenant_id, user_id, place_id, editing=True)
    place.deleted_at = datetime.now(UTC)
    place.deleted_by_user_id = user_id
    place.updated_by_user_id = user_id
    place.updated_at = datetime.now(UTC)


def restore_place(tenant_id: int, user_id: int, place_id: int) -> None:
    place = place_access(
        tenant_id, user_id, place_id, editing=True, include_deleted=True
    )
    place.deleted_at = None
    place.deleted_by_user_id = None
    place.updated_by_user_id = user_id
    place.updated_at = datetime.now(UTC)


def purge_place(tenant_id: int, user_id: int, place_id: int) -> None:
    place = place_access(
        tenant_id, user_id, place_id, editing=True, include_deleted=True
    )
    collection = collection_access(tenant_id, user_id, place.collection_id)
    if user_id not in {place.owner_user_id, collection.created_by_user_id}:
        raise PlaceError(
            _("Only the place owner or collection creator can permanently delete it.")
        )
    db.session.delete(place)


def purge_expired(*, now: datetime | None = None) -> int:
    """Trusted maintenance job; ORM deletion preserves committed-file cleanup."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=30)
    places = (
        Place.query.filter(Place.deleted_at <= cutoff)
        .with_for_update(skip_locked=True)
        .all()
    )
    for place in places:
        db.session.delete(place)
    return len(places)
