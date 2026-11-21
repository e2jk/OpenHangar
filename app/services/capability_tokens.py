"""Copyable capability URLs with only a nonce and digest stored in the database.

A database snapshot alone cannot reconstruct a URL: recovery also requires the
server signing key. These tokens are admission/publication capabilities, never
account identities. Different purposes and resources have different tokens.
"""

# Keep the original signing namespace: moving the implementation must not
# invalidate existing invitation or public URLs, or stored email digests.

import base64
import hashlib
import hmac
import re
import secrets

from flask import current_app


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_format(token: str) -> bool:
    return len(token) == 43 and re.fullmatch(r"[A-Za-z0-9_-]+", token) is not None


def derive(purpose: str, resource_id: int, nonce: str) -> str:
    value = f"passport-capability:v1:{purpose}:{resource_id}:{nonce}".encode()
    raw = hmac.digest(str(current_app.config["SECRET_KEY"]).encode(), value, "sha256")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def create(purpose: str, resource_id: int) -> tuple[str, str, str]:
    nonce = secrets.token_urlsafe(32)
    token = derive(purpose, resource_id, nonce)
    return nonce, token, digest(token)


def recover(purpose: str, resource_id: int, nonce: str, token_hash: str) -> str | None:
    token = derive(purpose, resource_id, nonce)
    return token if hmac.compare_digest(digest(token), token_hash) else None


def email_digest(email: str) -> str:
    return hmac.new(
        str(current_app.config["SECRET_KEY"]).encode(),
        ("passport-removal:v1:" + email.strip().lower()).encode(),
        hashlib.sha256,
    ).hexdigest()
