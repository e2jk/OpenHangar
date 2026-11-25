"""Collection membership changes never grant fleet or logbook permissions."""

from flask_babel import gettext as _
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionMember,
    PlacePreference,
    TenantUser,
    db,
    fits_column,
)
from sqlalchemy.exc import IntegrityError

from places.access import PlaceError, actor, collection_access, collections_for_user


def collection_name(value: str) -> str:
    name = value.strip()
    if not name or not fits_column(PlaceCollection.name, name):
        raise PlaceError(_("Enter a collection name of at most 128 characters."))
    return name


def create_collection(
    tenant_id: int,
    user_id: int,
    name: str,
    *,
    personal: bool = False,
) -> PlaceCollection:
    actor(tenant_id, user_id, writing=True)
    collection = PlaceCollection(
        tenant_id=tenant_id,
        name=collection_name(name),
        created_by_user_id=user_id,
        is_personal=personal,
    )
    db.session.add(collection)
    db.session.flush()
    db.session.add(
        PlaceCollectionMember(
            collection_id=collection.id,
            tenant_id=tenant_id,
            user_id=user_id,
            can_edit=True,
        )
    )
    db.session.flush()
    return collection


def personal_collection(tenant_id: int, user_id: int) -> PlaceCollection:
    """Lazy creation uses a savepoint and a unique index for concurrent first use."""
    actor(tenant_id, user_id, writing=True)
    collection: PlaceCollection | None = PlaceCollection.query.filter_by(
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        is_personal=True,
    ).first()
    if collection is None:
        try:
            with db.session.begin_nested():
                collection = create_collection(
                    tenant_id, user_id, _("My places"), personal=True
                )
        except IntegrityError:
            collection = PlaceCollection.query.filter_by(
                tenant_id=tenant_id,
                created_by_user_id=user_id,
                is_personal=True,
            ).one()
    assert collection is not None
    return collection


def default_collection(tenant_id: int, user_id: int) -> PlaceCollection:
    """The user's chosen default; an obsolete choice (left/deleted
    collection) falls back to My places, or — when the user deleted My
    places — to a collection they can edit, before recreating My places."""
    actor(tenant_id, user_id)
    preference = db.session.get(PlacePreference, (user_id, tenant_id))
    if preference is not None and preference.default_collection_id is not None:
        collection: PlaceCollection | None = (
            collections_for_user(tenant_id, user_id)
            .filter(
                PlaceCollection.id == preference.default_collection_id,
            )
            .first()
        )
        if collection is not None:
            member = db.session.get(PlaceCollectionMember, (collection.id, user_id))
            if member is not None and member.can_edit:
                return collection
    personal = PlaceCollection.query.filter_by(
        tenant_id=tenant_id, created_by_user_id=user_id, is_personal=True
    ).first()
    if personal is None:
        editable: PlaceCollection | None = (
            collections_for_user(tenant_id, user_id)
            .filter(PlaceCollectionMember.can_edit.is_(True))
            .order_by(PlaceCollection.id)
            .first()
        )
        if editable is not None:
            return editable
    return personal_collection(tenant_id, user_id)


def set_default(tenant_id: int, user_id: int, collection_id: int) -> None:
    collection_access(tenant_id, user_id, collection_id, editing=True)
    preference = db.session.get(PlacePreference, (user_id, tenant_id))
    if preference is None:
        preference = PlacePreference(user_id=user_id, tenant_id=tenant_id)
        db.session.add(preference)
    preference.default_collection_id = collection_id


def rename(tenant_id: int, user_id: int, collection_id: int, name: str) -> None:
    collection = collection_access(tenant_id, user_id, collection_id, managing=True)
    collection.name = collection_name(name)


def set_member(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    target_user_id: int,
    *,
    can_edit: bool = True,
) -> PlaceCollectionMember:
    """Trusted ID-based lane for organisation accounts; personal UI uses exact email."""
    collection = collection_access(tenant_id, user_id, collection_id, managing=True)
    member = db.session.get(PlaceCollectionMember, (collection_id, target_user_id))
    membership = db.session.get(TenantUser, (user_id, tenant_id))
    assert membership is not None
    if member is None and membership.access_scope != "full":
        raise PlaceError(_("Add friends by their exact email address."))
    actor(tenant_id, target_user_id)
    if target_user_id == collection.created_by_user_id and not can_edit:
        raise PlaceError(_("The collection creator must remain an editor."))
    member = db.session.get(PlaceCollectionMember, (collection_id, target_user_id))
    if member is None:
        member = PlaceCollectionMember(
            collection_id=collection_id,
            tenant_id=tenant_id,
            user_id=target_user_id,
        )
        db.session.add(member)
    member.can_edit = can_edit
    return member


def transfer_places(collection_id: int, user_id: int) -> None:
    """Membership removal retains content and visits, with ownership inherited."""
    collection = db.session.get(PlaceCollection, collection_id)
    assert collection is not None
    for place in Place.query.filter_by(
        collection_id=collection_id, owner_user_id=user_id
    ):
        place.owner_user_id = collection.created_by_user_id


def remove_member(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    target_user_id: int,
) -> None:
    leaving = user_id == target_user_id
    collection = collection_access(
        tenant_id,
        user_id,
        collection_id,
        managing=not leaving,
    )
    if target_user_id == collection.created_by_user_id:
        raise PlaceError(
            _("The creator must transfer or delete the collection before leaving.")
        )
    member = db.session.get(PlaceCollectionMember, (collection_id, target_user_id))
    if member is not None:
        transfer_places(collection_id, target_user_id)
        db.session.delete(member)
        preference = db.session.get(PlacePreference, (target_user_id, tenant_id))
        if preference is not None and preference.default_collection_id == collection_id:
            preference.default_collection_id = None


def delete_collection(tenant_id: int, user_id: int, collection_id: int) -> None:
    collection = collection_access(tenant_id, user_id, collection_id, managing=True)
    db.session.delete(collection)


def transfer_collection(
    tenant_id: int, user_id: int, collection_id: int, target_user_id: int
) -> None:
    collection = collection_access(tenant_id, user_id, collection_id, managing=True)
    member = db.session.get(PlaceCollectionMember, (collection_id, target_user_id))
    if member is None:
        raise PlaceError(_("Choose a member of this collection."))
    actor(tenant_id, target_user_id)
    member.can_edit = True
    collection.created_by_user_id = target_user_id
    # A transferred collection is shared; the recipient retains their own My places.
    collection.is_personal = False
