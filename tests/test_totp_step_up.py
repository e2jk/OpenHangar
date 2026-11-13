"""Adding, replacing or removing an authenticator needs recent re-authentication.

A session cookie plus its CSRF token must not be enough to change the second
factor: the current password is always required and, once an authenticator
exists, a code from it, used at most once.
"""

import logging
import time

import pyotp
from models import AuthChallenge, User, db

from tests.auth_helpers import challenge_payload
from tests.test_auth_challenges import PASSWORD, begin, make_user, replay, saved_cookie


def _code(secret, offset=0):
    return pyotp.TOTP(secret).at(time.time() + offset)


def _signed_in_with_totp(app):
    """A real two-factor login; returns (client, user id, current secret)."""
    uid, _ = make_user(app, totp=True, required=False)
    with app.app_context():
        secret = db.session.get(User, uid).totp_secret
    client = app.test_client()
    begin(client)
    client.post("/login", data={"step": "totp", "totp_code": _code(secret)})
    assert client.get("/profile").status_code == 200
    return client, uid, secret


def _signed_in_without_totp(app):
    uid, _ = make_user(app, required=False)
    client = app.test_client()
    begin(client)
    assert client.get("/profile").status_code == 200
    return client, uid


def _no_profile_challenge(client):
    with client.session_transaction() as sess:
        assert "profile_challenge" not in sess
    with client.application.app_context():
        assert AuthChallenge.query.filter_by(purpose="profile-totp").count() == 0


def _secret(app, uid):
    with app.app_context():
        return db.session.get(User, uid).totp_secret


class TestFirstEnrolment:
    def test_session_alone_cannot_start_enrolment(self, app):
        client, uid = _signed_in_without_totp(app)
        response = client.post("/profile", data={"action": "setup_totp"})
        assert response.status_code == 302
        _no_profile_challenge(client)
        assert _secret(app, uid) is None

    def test_wrong_password_cannot_start_enrolment(self, app, caplog):
        client, uid = _signed_in_without_totp(app)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            response = client.post(
                "/profile",
                data={"action": "setup_totp", "current_password": "wrong-password-1"},
                follow_redirects=True,
            )
        assert b"Current password is incorrect" in response.data
        assert "auth.totp.step_up_failed" in caplog.text
        assert "purpose=setup" in caplog.text
        _no_profile_challenge(client)
        assert _secret(app, uid) is None

    def test_password_starts_enrolment_without_asking_for_a_code(self, app):
        client, uid = _signed_in_without_totp(app)
        response = client.post(
            "/profile", data={"action": "setup_totp", "current_password": PASSWORD}
        )
        assert response.status_code == 200
        secret = challenge_payload(client, "profile_challenge")["secret"]
        client.post(
            "/profile", data={"action": "confirm_totp", "totp_code": _code(secret)}
        )
        assert _secret(app, uid) == secret

    def test_profile_offers_enrolment_behind_the_password(self, app):
        client, _ = _signed_in_without_totp(app)
        page = client.get("/profile").data
        assert b'value="setup_totp"' in page
        assert b'id="totp-setup-password"' in page
        assert b"current_totp_code" not in page


class TestReplacement:
    def test_password_alone_cannot_replace_the_authenticator(self, app, caplog):
        client, uid, secret = _signed_in_with_totp(app)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            response = client.post(
                "/profile",
                data={"action": "setup_totp", "current_password": PASSWORD},
                follow_redirects=True,
            )
        assert b"Invalid authenticator code" in response.data
        assert "auth.totp.step_up_failed" in caplog.text
        _no_profile_challenge(client)
        assert _secret(app, uid) == secret

    def test_code_from_another_authenticator_is_refused(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        client.post(
            "/profile",
            data={
                "action": "setup_totp",
                "current_password": PASSWORD,
                "current_totp_code": _code(pyotp.random_base32(), 30),
            },
        )
        _no_profile_challenge(client)
        assert _secret(app, uid) == secret

    def test_current_code_without_password_is_refused(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        client.post(
            "/profile",
            data={"action": "setup_totp", "current_totp_code": _code(secret, 30)},
        )
        _no_profile_challenge(client)
        assert _secret(app, uid) == secret

    def test_password_and_current_code_replace_it_and_end_other_logins(
        self, app, caplog
    ):
        client, uid, secret = _signed_in_with_totp(app)
        other = replay(app, saved_cookie(client))
        client.post(
            "/profile",
            data={
                "action": "setup_totp",
                "current_password": PASSWORD,
                "current_totp_code": _code(secret, 30),
            },
        )
        replacement = challenge_payload(client, "profile_challenge")["secret"]
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/profile",
                data={"action": "confirm_totp", "totp_code": _code(replacement)},
            )
        assert _secret(app, uid) == replacement
        assert "auth.totp.replaced" in caplog.text
        assert client.get("/profile").status_code == 200
        assert other.get("/profile").status_code == 302

    def test_a_current_code_proves_presence_only_once(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        data = {
            "action": "setup_totp",
            "current_password": PASSWORD,
            "current_totp_code": _code(secret, 30),
        }
        assert client.post("/profile", data=data).status_code == 200
        client.get("/profile")
        with client.session_transaction() as sess:
            sess.pop("profile_challenge")
        assert client.post("/profile", data=data).status_code == 302
        with client.session_transaction() as sess:
            assert "profile_challenge" not in sess
        assert _secret(app, uid) == secret

    def test_step_up_challenge_expires(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        client.post(
            "/profile",
            data={
                "action": "setup_totp",
                "current_password": PASSWORD,
                "current_totp_code": _code(secret, 30),
            },
        )
        replacement = challenge_payload(client, "profile_challenge")["secret"]
        with app.app_context():
            AuthChallenge.query.filter_by(purpose="profile-totp").update(
                {"expires_at": int(time.time()) - 1}
            )
            db.session.commit()
        response = client.post(
            "/profile",
            data={"action": "confirm_totp", "totp_code": _code(replacement)},
        )
        assert response.status_code == 302
        assert _secret(app, uid) == secret

    def test_step_up_challenge_cannot_complete_a_login(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        client.post(
            "/profile",
            data={
                "action": "setup_totp",
                "current_password": PASSWORD,
                "current_totp_code": _code(secret, 30),
            },
        )
        attacker = replay(app, saved_cookie(client))
        with attacker.session_transaction() as sess:
            sess.pop("user_id")
            sess.pop("auth_session")
            sess["login_challenge"] = sess["profile_challenge"]
        response = attacker.post(
            "/login", data={"step": "totp", "totp_code": _code(secret, -30)}
        )
        assert response.location.endswith("/login")
        with attacker.session_transaction() as sess:
            assert "user_id" not in sess
        assert _secret(app, uid) == secret

    def test_profile_offers_replacement_and_removal_behind_both_factors(self, app):
        client, _, _ = _signed_in_with_totp(app)
        page = client.get("/profile").data
        assert b'id="totp-replace-password"' in page
        assert b'id="totp-replace-code"' in page
        assert b'id="totp-disable-code"' in page


class TestDisable:
    def test_password_alone_cannot_disable(self, app, caplog):
        client, uid, secret = _signed_in_with_totp(app)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/profile",
                data={"action": "disable_totp", "current_password": PASSWORD},
            )
        assert "purpose=disable" in caplog.text
        assert _secret(app, uid) == secret

    def test_password_and_current_code_disable(self, app):
        client, uid, secret = _signed_in_with_totp(app)
        client.post(
            "/profile",
            data={
                "action": "disable_totp",
                "current_password": PASSWORD,
                "current_totp_code": _code(secret, 30),
            },
        )
        assert _secret(app, uid) is None
