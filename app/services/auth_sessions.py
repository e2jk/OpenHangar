"""Central validation of revocable, credential-bound authenticated sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from flask import Flask, Response, current_app, g, session
from flask.sessions import SecureCookieSessionInterface, SessionMixin
from models import (
    SAME_PASSWORD_REHASH,
    AuthChallenge,
    AuthSession,
    TenantUser,
    User,
    db,
)

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


# A login replaced by a renewal in the same browser (credential change, sign-in)
# stays recognisable this long, so requests still in flight with its cookie can
# be told apart from a revoked login: see _clear_login and validate.
RENEWED = "renewed"
RENEWED_GRACE_SECONDS = 60


def start(user: User) -> None:
    """Start/renew this browser login without retaining its old bearer token.

    The replaced token is retired, not just deleted: it no longer authenticates
    anything, but for a short while it is known to have been renewed, so a
    response to a request that was in flight with it does not clear the cookie
    the browser has meanwhile received.
    """
    token = session.pop("auth_session", None)
    if isinstance(token, str):
        AuthSession.query.filter_by(token_hash=_digest(token)).update(
            {
                "credential_binding": RENEWED,
                "expires_at": int(time.time()) + RENEWED_GRACE_SECONDS,
            },
            synchronize_session=False,
        )
    session["user_id"] = user.id
    session["auth_session"] = issue(user)
    session.permanent = True


def rehash_password(user: User, new_hash: str) -> None:
    """Store a stronger hash of the same, just-verified password.

    Not a credential change: logins and challenges valid under the old hash stay
    valid. Only records bound to the old credentials are re-bound, so anything
    already revoked stays revoked. Real password changes must not use this.
    """
    old_binding = credential_binding(user)
    user.password_hash = new_hash
    # Read (and cleared) by models._rotate_auth_version during the flush.
    db.session.info.setdefault(SAME_PASSWORD_REHASH, set()).add(user.id)
    db.session.flush()
    new_binding = credential_binding(user)
    for model in (AuthSession, AuthChallenge):
        model.query.filter_by(user_id=user.id, credential_binding=old_binding).update(
            {"credential_binding": new_binding}, synchronize_session=False
        )
    db.session.commit()


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
        record is not None
        and record.credential_binding == RENEWED
        and record.expires_at > int(time.time())
    ):
        _clear_login(keep_cookie=True)
        return False
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


def _clear_login(*, keep_cookie: bool = False) -> None:
    # A demo slot is a public sandbox selector, not an authenticated identity.
    slot = session.get("demo_slot_id")
    session.clear()
    if slot is not None:
        session["demo_slot_id"] = slot
    if keep_cookie:
        # A just-renewed login: handle this request as signed out, but do not
        # write that back. The browser already holds the newer cookie, and a
        # cleared one in this response could overwrite it (a request in flight
        # during a password change would sign the browser out).
        g.renewed_login_cookie = True


def _renewed(token: str) -> bool:
    record = db.session.get(AuthSession, _digest(token), populate_existing=True)
    return record is not None and record.credential_binding == RENEWED


class RenewedLoginSafeSessions(SecureCookieSessionInterface):
    """Cookie sessions where a just-renewed login cannot overwrite its successor.

    Flask re-sends the session cookie on every authenticated response. When this
    browser's login is renewed (e.g. its password changed) while other requests
    are still in flight, their responses must not put the old cookie back, nor a
    cleared one: whichever arrives last would win and sign the browser out.
    So no cookie is written by a request that was handled as a renewed login, or
    whose token has been renewed meanwhile (checked at save time). Revoked,
    logged-out or expired logins still get a cleared cookie, so the browser can
    sign in again; a request that started a new login carries the new token.
    """

    def save_session(
        self, app: Flask, session: SessionMixin, response: Response
    ) -> None:
        token = session.get("auth_session")
        if g.get("renewed_login_cookie") and not token:
            return
        if isinstance(token, str) and _renewed(token):
            return
        super().save_session(app, session, response)
