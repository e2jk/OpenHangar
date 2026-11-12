"""Database-backed, purpose-bound authentication challenges.

Consumption is a conditional DELETE in the same transaction as the resulting
credential/account write. Clearing a browser cookie alone cannot revoke a saved
copy, and process-local caches cannot coordinate multiple workers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from flask import current_app, session
from models import AuthChallenge, Tenant, TenantUser, User, db

CHALLENGE_SECONDS = 600
SETUP_SECONDS = 1800


def credential_binding(user: User) -> str:
    """Bind to current credentials without exposing their hashes in cookies."""
    value = json.dumps(
        [user.password_hash, user.totp_secret, user.is_active, user.auth_version]
    )
    return hmac.new(
        str(current_app.config["SECRET_KEY"]).encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def user_can_authenticate(user: User) -> bool:
    return bool(
        user.is_active
        and (
            user.is_instance_admin
            or TenantUser.query.join(Tenant)
            .filter(TenantUser.user_id == user.id, Tenant.is_active.is_(True))
            .first()
            is not None
        )
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(
    key: str,
    purpose: str,
    *,
    user: User | None = None,
    payload: dict[str, Any] | None = None,
    lifetime: int = CHALLENGE_SECONDS,
) -> None:
    """Replace this browser's challenge and discard expired server-side state."""
    now = int(time.time())
    AuthChallenge.query.filter(AuthChallenge.expires_at <= now).delete()
    old = session.pop(key, None)
    if old:
        AuthChallenge.query.filter_by(token_hash=_digest(old)).delete()
    token = secrets.token_urlsafe(32)
    db.session.add(
        AuthChallenge(
            token_hash=_digest(token),
            purpose=purpose,
            user_id=user.id if user else None,
            credential_binding=credential_binding(user) if user else None,
            expires_at=now + lifetime,
            payload=payload or {},
        )
    )
    db.session.commit()
    session[key] = token


def get(key: str, purpose: str, *, user_id: int | None = None) -> AuthChallenge | None:
    token = session.get(key)
    if not isinstance(token, str):
        return None
    challenge = db.session.get(AuthChallenge, _digest(token))
    if (
        challenge is None
        or challenge.purpose != purpose
        or challenge.expires_at <= int(time.time())
        or (user_id is not None and challenge.user_id != user_id)
    ):
        return None
    if challenge.user_id is not None:
        user = (
            User.query.filter_by(id=challenge.user_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if (
            user is None
            or not user_can_authenticate(user)
            or not hmac.compare_digest(
                challenge.credential_binding, credential_binding(user)
            )
        ):
            return None
    return challenge


def consume(challenge: AuthChallenge) -> bool:
    """Exactly one transaction can consume a live challenge; caller commits."""
    return bool(
        AuthChallenge.query.filter(
            AuthChallenge.token_hash == challenge.token_hash,
            AuthChallenge.expires_at > int(time.time()),
        ).delete(synchronize_session=False)
    )


def requires_totp(user: User) -> bool:
    membership = TenantUser.query.filter_by(user_id=user.id).first()
    return bool(membership and membership.tenant.require_totp)
