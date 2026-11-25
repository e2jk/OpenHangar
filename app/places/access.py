"""Explicit tenant and membership checks shared by all Places entry points."""

from typing import Any

from flask_babel import gettext as _
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionMember,
    Tenant,
    TenantUser,
    User,
    db,
)
from utils import is_view_only  # pyright: ignore[reportMissingImports]


class PlaceError(ValueError):
    """A safe, translated explanation for a refused Places operation."""


def actor(tenant_id: int, user_id: int, *, writing: bool = False) -> User:
    user: User | None = (
        User.query.join(TenantUser)
        .join(Tenant, Tenant.id == TenantUser.tenant_id)
        .filter(
            User.id == user_id,
            User.is_active.is_(True),
            TenantUser.tenant_id == tenant_id,
            Tenant.is_active.is_(True),
        )
        .first()
    )
    if user is None or (writing and is_view_only(user_id, tenant_id)):
        raise PlaceError(_("You cannot make this change."))
    return user


def collections_for_user(tenant_id: int, user_id: int) -> Any:
    """Even administrators see destinations only through collection membership."""
    actor(tenant_id, user_id)
    return PlaceCollection.query.join(
        PlaceCollectionMember, PlaceCollectionMember.collection_id == PlaceCollection.id
    ).filter(
        PlaceCollection.tenant_id == tenant_id,
        PlaceCollectionMember.tenant_id == tenant_id,
        PlaceCollectionMember.user_id == user_id,
    )


def collection_access(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    *,
    editing: bool = False,
    managing: bool = False,
) -> PlaceCollection:
    actor(tenant_id, user_id, writing=editing or managing)
    collection = db.session.get(PlaceCollection, collection_id)
    if collection is None or collection.tenant_id != tenant_id:
        raise PlaceError(_("Collection not found."))
    member = db.session.get(PlaceCollectionMember, (collection_id, user_id))
    if managing:
        # Only the collection's owner manages it: an administrator (instance
        # or hangar) sees and changes other people's collections only when
        # they were shared with them, like anyone else.
        # A collection whose owner's account was removed is managed by its
        # members with edit rights.
        permitted = collection.created_by_user_id == user_id or (
            collection.created_by_user_id is None
            and member is not None
            and member.can_edit
        )
    else:
        permitted = member is not None and (not editing or member.can_edit)
    if not permitted:
        # Readers and non-members receive no confirmation of other collections.
        raise PlaceError(_("Collection not found."))
    return collection


def place_access(
    tenant_id: int,
    user_id: int,
    place_id: int,
    *,
    editing: bool = False,
    include_deleted: bool = False,
) -> Place:
    place = db.session.get(Place, place_id)
    if place is None or (place.deleted_at is not None and not include_deleted):
        raise PlaceError(_("Place not found."))
    collection_access(tenant_id, user_id, place.collection_id, editing=editing)
    return place
