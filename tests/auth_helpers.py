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
