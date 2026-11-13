"""Non-bearer browser-storage namespaces for an authenticated account/login."""

from __future__ import annotations

import hashlib
import hmac

from flask import current_app, has_request_context, session
from models import TenantUser

OWNER_HEADER = "X-OH-Offline-Owner"
SESSION_HEADER = "X-OH-Offline-Session"


def _namespace(value: str) -> str:
    return hmac.new(
        str(current_app.config["SECRET_KEY"]).encode(),
        ("offline-storage:" + value).encode(),
        hashlib.sha256,
    ).hexdigest()


def identity() -> tuple[str, str]:
    """Return an account namespace and login epoch, never an authentication token.

    The stable owner names the account's browser database; cached pages and
    logbook snapshots additionally belong to one login epoch.
    Neither value is accepted by the authentication protocol as a bearer token.
    """
    if not has_request_context():
        return "", ""
    uid = session.get("user_id")
    token = session.get("auth_session")
    if not uid or not isinstance(token, str):
        return "", ""
    membership = TenantUser.query.filter_by(user_id=uid).first()
    tenant_id = membership.tenant_id if membership else None
    return _namespace(f"owner:{uid}:{tenant_id}"), _namespace(f"login:{token}")
