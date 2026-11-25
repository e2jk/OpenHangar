"""Collection-local tags; merging never duplicates a place's tag assignment."""

from datetime import UTC, datetime

from flask_babel import gettext as _
from models import N_, Place, PlaceTag, PlaceTagAssignment, db

from places.access import PlaceError, collection_access

STARTER_TAGS = (
    N_("Town"),
    N_("Attraction"),
    N_("Restaurant"),
    N_("Nature"),
    N_("Viewpoint"),
    N_("Museum"),
    N_("Beach"),
    N_("Overnight stay"),
    N_("Family"),
)


def rename_tag(
    tenant_id: int, user_id: int, collection_id: int, tag_id: int, name: str
) -> None:
    collection_access(tenant_id, user_id, collection_id, editing=True)
    tag = PlaceTag.query.filter_by(id=tag_id, collection_id=collection_id).first()
    if tag is None:
        raise PlaceError(_("Tag not found."))
    name = name.strip()
    if not name or len(name) > 80:
        raise PlaceError(_("Enter a tag name of at most 80 characters."))
    target = PlaceTag.query.filter_by(collection_id=collection_id, name=name).first()
    if target is not None and target.id == tag.id:
        return
    assignments = PlaceTagAssignment.query.filter_by(tag_id=tag.id).all()
    for assignment in assignments:
        place = db.session.get(Place, assignment.place_id)
        assert place is not None
        place.updated_by_user_id = user_id
        place.updated_at = datetime.now(UTC)
        if target is not None and (
            db.session.get(PlaceTagAssignment, (assignment.place_id, target.id)) is None
        ):
            db.session.add(
                PlaceTagAssignment(
                    collection_id=collection_id,
                    place_id=assignment.place_id,
                    tag_id=target.id,
                )
            )
    if target is None:
        tag.name = name
    else:
        db.session.delete(tag)


def delete_tag(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    tag_id: int,
    *,
    confirmed: bool = False,
) -> None:
    collection_access(tenant_id, user_id, collection_id, editing=True)
    tag = (
        PlaceTag.query.filter_by(id=tag_id, collection_id=collection_id)
        .with_for_update()
        .first()
    )
    if tag is None:
        raise PlaceError(_("Tag not found."))
    assignments = PlaceTagAssignment.query.filter_by(tag_id=tag.id).all()
    if assignments and not confirmed:
        raise PlaceError(_("Confirm removal of this tag from every place using it."))
    for assignment in assignments:
        place = db.session.get(Place, assignment.place_id)
        assert place is not None
        place.updated_by_user_id = user_id
        place.updated_at = datetime.now(UTC)
    db.session.delete(tag)
