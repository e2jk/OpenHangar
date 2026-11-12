"""Central validation of revocable, credential-bound authenticated sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from flask import current_app, session
from models import AuthSession, TenantUser, User, db

from services.auth_challenges import credential_binding


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _membership(user: User) -> TenantUser | None:
    membership: TenantUser | None = TenantUser.query.filter_by(user_id=user.id).first()
    return membership


def issue(user: User) -> str:
    """Create a login record; callers install its opaque token in the cookie."""
    now = int(time.time())
    AuthSession.query.filter(AuthSession.expires_at <= now).delete()
    token = secrets.token_urlsafe(32)
    membership = _membership(user)
    db.session.add(
        AuthSession(
            token_hash=_digest(token),
            user_id=user.id,
            tenant_id=None
            if user.is_instance_admin
            else membership.tenant_id
            if membership
            else None,
            credential_binding=credential_binding(user),
            expires_at=now
            + int(current_app.permanent_session_lifetime.total_seconds()),
        )
    )
    db.session.commit()
    return token


def start(user: User) -> None:
    """Start/renew this browser login without retaining its old bearer token."""
    revoke_current()
    session["user_id"] = user.id
    session["auth_session"] = issue(user)
    session.permanent = True


def revoke_current() -> None:
    token = session.pop("auth_session", None)
    if isinstance(token, str):
        AuthSession.query.filter_by(token_hash=_digest(token)).delete()
        db.session.commit()


def validate() -> bool:
    """Fail closed for legacy cookies, changed credentials, or inactive membership."""
    uid = session.get("user_id")
    if not uid:
        return True
    token = session.get("auth_session")
    record = (
        db.session.get(AuthSession, _digest(token)) if isinstance(token, str) else None
    )
    user = db.session.get(User, uid)
    if (
        record is None
        or record.user_id != uid
        or user is None
        or not user.is_active
        or record.expires_at <= int(time.time())
        or not hmac.compare_digest(record.credential_binding, credential_binding(user))
    ):
        _clear_login()
        return False
    if not user.is_instance_admin:
        membership = _membership(user)
        if (
            membership is None
            or membership.tenant_id != record.tenant_id
            or not membership.tenant.is_active
        ):
            _clear_login()
            return False
    return True


def _clear_login() -> None:
    # A demo slot is a public sandbox selector, not an authenticated identity.
    slot = session.get("demo_slot_id")
    session.clear()
    if slot is not None:
        session["demo_slot_id"] = slot
