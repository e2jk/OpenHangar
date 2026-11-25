"""Wrong authenticator codes are limited per user.

Once the password is known, the six-digit code is the only barrier left. Five
wrong codes lock the codes for that user for 30 minutes, whatever the
address, and entering the password again does not reset the count; the
profile step-up (which can remove the authenticator) shares the same limit.
"""

import logging
import time

import pyotp
from models import User, db

from tests.test_auth_challenges import PASSWORD, begin, make_user

_WRONG = "000000"


def _code(secret, offset=0):
    return pyotp.TOTP(secret).at(time.time() + offset)


def _secret(app, uid):
    with app.app_context():
        return db.session.get(User, uid).totp_secret


def _wrong_code(client):
    return client.post("/login", data={"step": "totp", "totp_code": _WRONG})


def _signed_in(app, client, secret):
    begin(client)
    client.post("/login", data={"step": "totp", "totp_code": _code(secret)})


def test_five_wrong_codes_lock_the_second_factor(app, client, caplog):
    uid, _ = make_user(app, totp=True, required=False)
    secret = _secret(app, uid)
    begin(client)
    for _attempt in range(4):
        assert b"Invalid authenticator code" in _wrong_code(client).data
    with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
        locked = _wrong_code(client)
    assert locked.status_code == 429
    assert b"Too many wrong authenticator codes" in locked.data
    assert "auth.totp.locked" in caplog.text

    # The challenge is gone: the right code alone no longer signs in.
    client.post("/login", data={"step": "totp", "totp_code": _code(secret)})
    assert client.get("/profile").status_code == 302

    # Entering the password again starts a new challenge, but the codes stay
    # locked: even the right one is refused.
    begin(client)
    refused = client.post(
        "/login", data={"step": "totp", "totp_code": _code(secret, 30)}
    )
    assert refused.status_code == 429
    assert client.get("/profile").status_code == 302


def test_a_new_password_entry_does_not_reset_the_count(app, client):
    make_user(app, totp=True, required=False)
    begin(client)
    for _attempt in range(3):
        _wrong_code(client)
    begin(client)
    _wrong_code(client)
    assert _wrong_code(client).status_code == 429


def test_a_right_code_resets_the_count(app, client):
    uid, _ = make_user(app, totp=True, required=False)
    secret = _secret(app, uid)
    begin(client)
    for _attempt in range(4):
        _wrong_code(client)
    client.post("/login", data={"step": "totp", "totp_code": _code(secret)})
    assert client.get("/profile").status_code == 200

    other = app.test_client()
    begin(other)
    for _attempt in range(4):
        assert _wrong_code(other).status_code == 200


def test_the_profile_step_up_shares_the_limit(app, client):
    uid, _ = make_user(app, totp=True, required=False)
    secret = _secret(app, uid)
    _signed_in(app, client, secret)
    data = {
        "action": "disable_totp",
        "current_password": PASSWORD,
        "current_totp_code": _WRONG,
    }
    for _attempt in range(4):
        page = client.post("/profile", data=data, follow_redirects=True)
        assert b"Invalid authenticator code" in page.data
    page = client.post("/profile", data=data, follow_redirects=True)
    assert b"Too many wrong authenticator codes" in page.data
    # Locked: even a right code cannot remove the authenticator now.
    page = client.post(
        "/profile",
        data={**data, "current_totp_code": _code(secret, 30)},
        follow_redirects=True,
    )
    assert b"Too many wrong authenticator codes" in page.data
    assert _secret(app, uid) == secret


def test_a_right_step_up_code_resets_the_count(app, client):
    uid, _ = make_user(app, totp=True, required=False)
    secret = _secret(app, uid)
    _signed_in(app, client, secret)
    wrong = {
        "action": "setup_totp",
        "current_password": PASSWORD,
        "current_totp_code": _WRONG,
    }
    for _attempt in range(4):
        client.post("/profile", data=wrong)
    right = {**wrong, "current_totp_code": _code(secret, 30)}
    assert client.post("/profile", data=right).status_code == 200
    with client.session_transaction() as sess:
        sess.pop("profile_challenge", None)
    for _attempt in range(4):
        page = client.post("/profile", data=wrong, follow_redirects=True)
        assert b"Invalid authenticator code" in page.data
