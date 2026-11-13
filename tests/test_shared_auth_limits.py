"""Sign-in limits and TOTP codes shared by every worker (audit S05).

Two app instances on one database stand in for two gunicorn workers (or one
worker before and after a restart): what one accepted or counted, the other
sees. Production Argon2 parameters are checked here too, separately from the
cheap hasher the rest of the test suite installs.
"""

import importlib.util
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pyotp
import pytest
from models import LoginThrottle, Role, Tenant, TenantUser, User, db
from services import login_throttle
from services import totp as totp_codes

PASSWORD = "SharedLimitsPassword123!"


@pytest.fixture
def two_workers(tmp_path, monkeypatch):
    import pw_hash
    from init import create_app

    monkeypatch.setenv("OPENHANGAR_DATABASE_URL", f"sqlite:///{tmp_path / 'shared.db'}")
    monkeypatch.setenv("OPENHANGAR_SKIP_BACKGROUND_THREADS", "1")
    apps = []
    for _ in range(2):
        worker = create_app()
        worker.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            RATELIMIT_ENABLED=False,
            UPLOAD_FOLDER=str(tmp_path),
        )
        apps.append(worker)
    with apps[0].app_context():
        db.create_all()
        tenant = Tenant(name="Shared limits")
        user = User(
            email="shared@example.com",
            password_hash=pw_hash.hash(PASSWORD),
            totp_secret=pyotp.random_base32(),
        )
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        secret = user.totp_secret
    yield apps, secret
    for worker in apps:
        with worker.app_context():
            db.session.remove()
            db.engine.dispose()


def _sign_in(worker, code, ip="198.51.100.20"):
    client = worker.test_client()
    environ = {"REMOTE_ADDR": ip}
    client.post(
        "/login",
        data={"email": "shared@example.com", "password": PASSWORD},
        environ_base=environ,
    )
    client.post(
        "/login", data={"step": "totp", "totp_code": code}, environ_base=environ
    )
    return client.get("/profile").status_code == 200


def test_a_code_accepted_by_one_worker_is_refused_by_the_other(two_workers, caplog):
    (first, second), secret = two_workers
    code = pyotp.TOTP(secret).now()
    assert _sign_in(first, code)
    with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
        assert not _sign_in(second, code)
    assert "auth.totp.replay" in caplog.text


def test_failures_counted_by_one_worker_lock_the_account_on_the_other(two_workers):
    (first, second), _ = two_workers
    for i in range(10):
        first.test_client().post(
            "/login",
            data={"email": "shared@example.com", "password": "wrong"},
            environ_base={"REMOTE_ADDR": f"192.0.2.{i + 1}"},
        )
    resp = second.test_client().post(
        "/login",
        data={"email": "shared@example.com", "password": PASSWORD},
        environ_base={"REMOTE_ADDR": "192.0.2.100"},
    )
    assert resp.status_code == 429
    assert b"temporarily locked" in resp.data.lower()


# ── Single-use codes ───────────────────────────────────────────────────────────


@pytest.fixture
def totp_user(app):
    with app.app_context():
        user = User(
            email="steps@example.com",
            password_hash="unused",
            totp_secret=pyotp.random_base32(),
        )
        db.session.add(user)
        db.session.commit()
        return user.id, user.totp_secret, user.auth_version


def test_matching_step_allows_one_step_of_drift_either_way():
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    now = 1_700_000_000
    current = now // 30
    for offset in (-1, 0, 1):
        code = totp.at(now + 30 * offset)
        assert totp_codes.matching_step(secret, code, now) == current + offset
    assert totp_codes.matching_step(secret, totp.at(now + 60), now) is None
    assert totp_codes.matching_step(secret, "12345", now) is None
    assert totp_codes.matching_step(secret, "abcdef", now) is None


def test_a_code_and_any_older_one_are_refused_once_used(app, totp_user):
    uid, secret, _ = totp_user
    totp = pyotp.TOTP(secret)
    with app.app_context():
        user = db.session.get(User, uid)
        assert totp_codes.use_code(user, totp.at(time.time() + 30)) == "accepted"
        assert totp_codes.use_code(user, totp.at(time.time() + 30)) == "reused"
        assert totp_codes.use_code(user, totp.now()) == "reused"
        assert totp_codes.use_code(user, "000000") in ("invalid", "reused")
        assert totp_codes.use_code(user, "not-a-code") == "invalid"


def test_using_a_code_keeps_the_users_other_logins(app, totp_user):
    uid, secret, auth_version = totp_user
    with app.app_context():
        user = db.session.get(User, uid)
        assert totp_codes.use_code(user, pyotp.TOTP(secret).now()) == "accepted"
        db.session.expire_all()
        assert db.session.get(User, uid).auth_version == auth_version


def test_without_an_authenticator_no_code_is_accepted(app):
    with app.app_context():
        user = User(email="none@example.com", password_hash="unused")
        db.session.add(user)
        db.session.commit()
        assert totp_codes.use_code(user, "123456") == "invalid"


def test_enrolment_codes_cannot_sign_in_again(app, client):
    """The code that confirmed an authenticator is used up."""
    from tests.auth_helpers import challenge_payload
    from tests.test_auth_challenges import PASSWORD as CH_PASSWORD
    from tests.test_auth_challenges import begin, make_user

    uid, _ = make_user(app)  # tenant requires TOTP: enrolment at sign-in
    begin(client)
    secret = challenge_payload(client, "login_challenge")["secret"]
    code = pyotp.TOTP(secret).now()
    client.post("/login", data={"step": "totp-enrol", "totp_code": code})
    client.get("/logout")
    other = app.test_client()
    other.post(
        "/login", data={"email": "challenge@example.com", "password": CH_PASSWORD}
    )
    other.post("/login", data={"step": "totp", "totp_code": code})
    assert other.get("/profile").status_code == 302
    with app.app_context():
        assert db.session.get(User, uid).totp_last_step is not None


def test_setup_wizard_records_the_confirmed_step(app, client):
    from tests.auth_helpers import challenge_payload

    client.post(
        "/setup",
        data={"step": "account", "email": "wizard@example.com", "password": PASSWORD},
    )
    secret = challenge_payload(client, "setup_challenge")["secret"]
    moment = time.time()
    client.post(
        "/setup",
        data={"step": "totp", "totp_code": pyotp.TOTP(secret).at(moment)},
    )
    assert (
        challenge_payload(client, "setup_challenge")["totp_step"] == int(moment) // 30
    )


def test_disabling_or_resetting_clears_the_recorded_step(app, client):
    from tests.test_totp_step_up import _code, _signed_in_with_totp

    client, uid, secret = _signed_in_with_totp(app)
    client.post(
        "/profile",
        data={
            "action": "disable_totp",
            "current_password": "ChallengePassword123!",
            "current_totp_code": _code(secret, 30),
        },
    )
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.totp_secret is None
        assert user.totp_last_step is None


# ── Throttle counters ─────────────────────────────────────────────────────────


def test_counts_restart_after_the_window_and_old_rows_are_purged(app):
    with app.app_context():
        with patch("services.login_throttle._now", return_value=1000):
            assert login_throttle.record_failure("ip:a", 60) == 1
            assert login_throttle.record_failure("ip:a", 60) == 2
            login_throttle.block("ip:a", 30)
            assert login_throttle.blocked_for("ip:a") == 30
        with patch("services.login_throttle._now", return_value=1061):
            assert login_throttle.blocked_for("ip:a") == 0
            assert login_throttle.record_failure("ip:a", 60) == 1  # window over
            assert login_throttle.blocked_for("ip:a") == 0
            login_throttle.record_failure("ip:b", 60)
        with patch("services.login_throttle._now", return_value=2000):
            login_throttle.record_failure("ip:c", 60)
            assert {row.key for row in LoginThrottle.query} == {"ip:c"}
        assert login_throttle.blocked_for("ip:unknown") == 0


def test_a_block_outlasts_the_failure_window(app):
    with app.app_context():
        with patch("services.login_throttle._now", return_value=1000):
            login_throttle.record_failure("acct:x", 60)
            login_throttle.block("acct:x", 600, reset=True)
        with patch("services.login_throttle._now", return_value=1100):
            # Past the 60 s window, still inside the block: not purged, not
            # restarted by another failure.
            login_throttle.record_failure("acct:y", 60)
            assert login_throttle.blocked_for("acct:x") == 500
            assert login_throttle.record_failure("acct:x", 60) == 1
            assert login_throttle.blocked_for("acct:x") == 500


# ── Production password hashing ───────────────────────────────────────────────


def test_production_argon2_parameters_are_unchanged():
    """The suite swaps in a cheap hasher (tests/conftest.py); load a fresh,
    untouched copy of the module to check what production really uses."""
    path = Path(__file__).parent.parent / "app" / "pw_hash.py"
    spec = importlib.util.spec_from_file_location("pw_hash_production", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hasher = module._ph
    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (2, 65536, 2)
    hashed = module.hash("production-parameters")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=2,p=2$")
    assert module.verify("production-parameters", hashed)
    assert not module.needs_rehash(hashed)
