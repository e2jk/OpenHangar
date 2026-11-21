"""Generic capability lifecycle, independent of a resource's public projection.

Callers must authorize mutations and validate the live resource on every read.
This module never decides which resource fields may be published.
"""

from datetime import UTC, datetime

from models import PublicShare, db
from passports import tokens
from utils import as_utc


def _purpose(share: PublicShare) -> str:
    return f"public-share:{share.tenant_id}:{share.resource_type}"


def rotate(share: PublicShare, expires_at: datetime | None = None) -> str:
    """Replace the capability without changing any participant's consent.

    The authorized caller owns the transaction and serialization of mutations.
    """
    nonce, token, digest = tokens.create(_purpose(share), share.resource_id)
    share.key_nonce, share.token_digest = nonce, digest
    share.revoked_at = None
    share.expires_at = expires_at
    return token


def revoke(share: PublicShare) -> None:
    share.revoked_at = datetime.now(UTC)


def recover(share: PublicShare) -> str | None:
    if share.revoked_at is not None or (
        share.expires_at is not None and as_utc(share.expires_at) <= datetime.now(UTC)
    ):
        return None
    return tokens.recover(
        _purpose(share), share.resource_id, share.key_nonce, share.token_digest
    )


def lookup(token: str, resource_type: str) -> PublicShare | None:
    """Fresh capability lookup; adapters must then check the live resource.

    populate_existing avoids stale identity-map state when a caller has already
    loaded this record. No response or projection is cached here.
    """
    if not tokens.valid_format(token):
        return None
    share: PublicShare | None = db.session.execute(
        db.select(PublicShare)
        .where(
            PublicShare.token_digest == tokens.digest(token),
            PublicShare.resource_type == resource_type,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if share is None or recover(share) != token:
        return None
    return share


def matches_resource(
    share: PublicShare,
    *,
    tenant_id: int,
    owner_user_id: int,
    created_at: datetime,
) -> bool:
    """Reject ownership changes and deletion followed by integer-ID reuse."""
    return (
        share.tenant_id == tenant_id
        and share.issuer_user_id == owner_user_id
        and as_utc(share.resource_created_at) == as_utc(created_at)
    )
