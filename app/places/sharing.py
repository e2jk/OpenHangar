"""Exact-email invitations do not query the user directory or reveal registration."""

from datetime import UTC, datetime, timedelta
from email.errors import HeaderParseError
from email.headerregistry import Address
from typing import Any

from flask_babel import gettext as _
from models import (
    PlaceCollectionInvitation,
    PlaceCollectionMember,
    User,
    db,
    fits_column,
)
from services.capability_tokens import email_digest

from places.access import PlaceError, actor, collection_access


def invite_email(
    tenant_id: int,
    user_id: int,
    collection_id: int,
    value: str,
    *,
    can_edit: bool = True,
) -> PlaceCollectionInvitation:
    collection_access(tenant_id, user_id, collection_id, managing=True)
    email = value.strip().lower()
    try:
        if (
            not email.isascii()
            or any(c.isspace() for c in email)
            or not fits_column(User.email, email)
        ):
            raise ValueError
        address = Address(addr_spec=email)
        if not address.username or "." not in address.domain:
            raise ValueError
    except (ValueError, HeaderParseError):
        raise PlaceError(_("Enter a valid email address.")) from None
    email = address.addr_spec.lower()
    fingerprint = email_digest(email)
    row: PlaceCollectionInvitation | None = PlaceCollectionInvitation.query.filter_by(
        collection_id=collection_id, email_digest=fingerprint
    ).first()
    if row is None:
        row = PlaceCollectionInvitation(
            tenant_id=tenant_id, collection_id=collection_id, email_digest=fingerprint
        )
        db.session.add(row)
    row.email = email
    row.can_edit = can_edit
    row.expires_at = datetime.now(UTC) + timedelta(days=30)
    return row


def pending_for_user(tenant_id: int, user_id: int) -> Any:
    user = actor(tenant_id, user_id)
    return PlaceCollectionInvitation.query.filter_by(
        tenant_id=tenant_id, email_digest=email_digest(user.email)
    ).filter(PlaceCollectionInvitation.expires_at > datetime.now(UTC))


def accept(tenant_id: int, user_id: int, invitation_id: int) -> int:
    invitation: PlaceCollectionInvitation | None = (
        pending_for_user(tenant_id, user_id)
        .filter_by(id=invitation_id)
        .with_for_update()
        .first()
    )
    if invitation is None:
        raise PlaceError(_("Invitation not found or expired."))
    member = db.session.get(PlaceCollectionMember, (invitation.collection_id, user_id))
    if member is None:
        db.session.add(
            PlaceCollectionMember(
                tenant_id=tenant_id,
                collection_id=invitation.collection_id,
                user_id=user_id,
                can_edit=invitation.can_edit,
            )
        )
    collection_id: int = invitation.collection_id
    db.session.delete(invitation)
    return collection_id


def revoke(
    tenant_id: int, user_id: int, collection_id: int, invitation_id: int
) -> None:
    collection_access(tenant_id, user_id, collection_id, managing=True)
    invitation = PlaceCollectionInvitation.query.filter_by(
        id=invitation_id, collection_id=collection_id
    ).first()
    if invitation is None:
        raise PlaceError(_("Invitation not found or expired."))
    db.session.delete(invitation)
