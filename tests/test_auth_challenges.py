"""Security regressions for replay, expiry and purpose-bound auth state."""

from unittest.mock import patch

import pw_hash
import pyotp
import pytest
from models import AuthChallenge, Role, Tenant, TenantUser, User, db
from services import auth_challenges as challenges

from tests.auth_helpers import challenge_payload

PASSWORD = "ChallengePassword123!"


def make_user(app, *, totp=False, required=True):
    with app.app_context():
        tenant = Tenant(name="Challenges", require_totp=required)
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="challenge@example.com",
            password_hash=pw_hash.hash(PASSWORD),
            totp_secret=pyotp.random_base32() if totp else None,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def begin(client):
    return client.post(
        "/login", data={"email": "challenge@example.com", "password": PASSWORD}
    )


def saved_cookie(client):
    return client.get_cookie("session").value


def replay(app, cookie):
    client = app.test_client()
    client.set_cookie("session", cookie)
    return client


def test_enrolment_is_single_use_even_after_credentials_change(app, client):
    uid, _ = make_user(app)
    begin(client)
    cookie = saved_cookie(client)
    secret = challenge_payload(client, "login_challenge")["secret"]
    assert (
        secret
        not in app.session_interface.get_signing_serializer(app).loads(cookie).values()
    )
    client.post(
        "/login", data={"step": "totp-enrol", "totp_code": pyotp.TOTP(secret).now()}
    )
    with app.app_context():
        user = db.session.get(User, uid)
        replacement = pyotp.random_base32()
        user.totp_secret = replacement
        user.password_hash = pw_hash.hash("ReplacementPassword123!")
        db.session.commit()
    attacker = replay(app, cookie)
    response = attacker.post(
        "/login", data={"step": "totp-enrol", "totp_code": pyotp.TOTP(secret).now()}
    )
    assert response.location.endswith("/login")
    with attacker.session_transaction() as sess:
        assert "user_id" not in sess
    with app.app_context():
        assert db.session.get(User, uid).totp_secret == replacement


@pytest.mark.parametrize(
    "change", ["password", "totp", "inactive", "tenant", "expired", "policy"]
)
def test_pending_enrolment_revalidates_current_state(app, client, change):
    uid, tid = make_user(app)
    begin(client)
    secret = challenge_payload(client, "login_challenge")["secret"]
    with app.app_context():
        user = db.session.get(User, uid)
        if change == "password":
            user.password_hash = pw_hash.hash("ChangedPassword123!")
        elif change == "totp":
            user.totp_secret = pyotp.random_base32()
        elif change == "inactive":
            user.is_active = False
        elif change == "tenant":
            db.session.get(Tenant, tid).is_active = False
        elif change == "policy":
            db.session.get(Tenant, tid).require_totp = False
        else:
            AuthChallenge.query.update({"expires_at": 0})
        db.session.commit()
    response = client.post(
        "/login", data={"step": "totp-enrol", "totp_code": pyotp.TOTP(secret).now()}
    )
    assert response.location.endswith("/login")
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_normal_totp_challenge_is_consumed_and_bound_to_credentials(app, client):
    uid, _ = make_user(app, totp=True)
    begin(client)
    cookie = saved_cookie(client)
    with app.app_context():
        secret = db.session.get(User, uid).totp_secret
    response = client.post(
        "/login", data={"step": "totp", "totp_code": pyotp.TOTP(secret).now()}
    )
    assert "_swr_fresh" in response.location
    assert (
        replay(app, cookie)
        .post("/login", data={"step": "totp", "totp_code": pyotp.TOTP(secret).now()})
        .location.endswith("/login")
    )
    begin(replay(app, cookie))


def test_challenge_service_purpose_owner_replacement_and_atomic_consumption(app):
    uid, _ = make_user(app)
    with app.test_request_context():
        user = db.session.get(User, uid)
        challenges.issue("test", "first", user=user)
        assert challenges.get("test", "wrong") is None
        assert challenges.get("test", "first", user_id=uid + 1) is None
        challenges.issue("test", "first", user=user)
        assert AuthChallenge.query.count() == 1
        challenge = challenges.get("test", "first", user_id=uid)
        assert challenge is not None
        assert challenges.consume(challenge)
        assert not challenges.consume(challenge)
        db.session.commit()
        assert challenges.get("test", "first") is None
        challenges.issue("test", "expired", lifetime=-1)
        assert challenges.get("test", "expired") is None
        challenges.issue("another", "live")
        assert AuthChallenge.query.count() == 1


def test_setup_secrets_stay_server_side_and_expire(app, client):
    client.post(
        "/setup",
        data={"step": "account", "email": "setup@example.com", "password": PASSWORD},
    )
    with client.session_transaction() as sess:
        assert "setup_challenge" in sess
        assert (
            not {"setup_password_hash", "setup_totp_secret", "setup_provisioning_uri"}
            & sess.keys()
        )
    client.post("/setup", data={"step": "totp", "action": "skip"})
    with app.app_context():
        AuthChallenge.query.update({"expires_at": 0})
        db.session.commit()
    response = client.post(
        "/setup", data={"step": "operating_model", "operating_model": "sole_pilot"}
    )
    assert response.location.endswith("/setup")
    with app.app_context():
        assert User.query.count() == 0


def test_profile_challenge_cannot_be_replayed_or_used_for_login(app, client):
    uid, _ = make_user(app, required=False)
    begin(client)
    client.post("/profile", data={"action": "setup_totp"})
    payload = challenge_payload(client, "profile_challenge")
    cookie = saved_cookie(client)
    client.get(
        "/profile"
    )  # viewing the profile must not silently consume the challenge
    client.post(
        "/profile",
        data={
            "action": "confirm_totp",
            "totp_code": pyotp.TOTP(payload["secret"]).now(),
        },
    )
    attacker = replay(app, cookie)
    with attacker.session_transaction() as sess:
        sess["login_challenge"] = sess["profile_challenge"]
    assert (
        attacker.post(
            "/profile",
            data={
                "action": "confirm_totp",
                "totp_code": pyotp.TOTP(payload["secret"]).now(),
            },
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(User, uid).totp_secret == payload["secret"]


@pytest.mark.parametrize("purpose", ["totp", "totp-enrol", "profile-totp", "setup"])
def test_losing_a_consumption_race_never_authenticates_or_changes_credentials(
    app, client, purpose
):
    if purpose == "setup":
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "setup@example.com",
                "password": PASSWORD,
            },
        )
        client.post("/setup", data={"step": "totp", "action": "skip"})
        url = "/setup"
        data = {"step": "operating_model", "operating_model": "sole_pilot"}
    else:
        uid, _ = make_user(
            app, totp=purpose == "totp", required=purpose != "profile-totp"
        )
        begin(client)
        if purpose == "profile-totp":
            client.post("/profile", data={"action": "setup_totp"})
            secret = challenge_payload(client, "profile_challenge")["secret"]
            url = "/profile"
            data = {"action": "confirm_totp", "totp_code": pyotp.TOTP(secret).now()}
        else:
            with app.app_context():
                secret = db.session.get(User, uid).totp_secret
            if purpose == "totp-enrol":
                secret = challenge_payload(client, "login_challenge")["secret"]
            url = "/login"
            data = {"step": purpose, "totp_code": pyotp.TOTP(secret).now()}
    with patch("services.auth_challenges.consume", return_value=False):
        response = client.post(url, data=data)
    assert response.status_code == 302
    with app.app_context():
        if purpose == "setup":
            assert User.query.count() == 0
        elif purpose != "totp":
            assert db.session.get(User, uid).totp_secret is None


def test_two_connections_can_consume_a_challenge_only_once(monkeypatch, tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from init import create_app

    monkeypatch.setenv(
        "OPENHANGAR_DATABASE_URL", f"sqlite:///{tmp_path / 'challenges.db'}"
    )
    isolated = create_app()
    token_hash = "a" * 64
    with isolated.app_context():
        db.create_all()
        db.session.add(
            AuthChallenge(
                token_hash=token_hash,
                purpose="setup",
                expires_at=int(time.time()) + 600,
                payload={},
            )
        )
        db.session.commit()
    barrier = Barrier(2)

    def consume_once():
        with isolated.app_context():
            challenge = db.session.get(AuthChallenge, token_hash)
            barrier.wait(timeout=10)
            won = challenges.consume(challenge)
            db.session.commit()
            return won

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume_once(), range(2)))
        assert sorted(results) == [False, True]
        with isolated.app_context():
            assert AuthChallenge.query.count() == 0
    finally:
        with isolated.app_context():
            db.session.remove()
            db.engine.dispose()
