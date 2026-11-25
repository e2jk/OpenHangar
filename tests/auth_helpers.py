"""Inspect server-side challenges in tests without copying secrets into cookies."""

import hashlib

from models import AuthChallenge, db


def challenge_payload(client, key):
    with client.session_transaction() as sess:
        token = sess[key]
    with client.application.app_context():
        challenge = db.session.get(
            AuthChallenge, hashlib.sha256(token.encode()).hexdigest()
        )
        assert challenge is not None
        return dict(challenge.payload)


def set_membership_flags(user_id, **flags):
    """Set capability flags (is_pilot, is_maintenance, view_only) on every
    hangar membership of the user: the flags live on TenantUser, not on the
    account. The caller commits."""
    from models import TenantUser

    for membership in TenantUser.query.filter_by(user_id=user_id):
        for name, value in flags.items():
            setattr(membership, name, value)
