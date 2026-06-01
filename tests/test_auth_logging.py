"""
Tests for authentication failure logging (CWE-778), timing-safe login
(CWE-208 account enumeration fix), and the pw_hash Argon2id layer (N-19).
"""

import logging
from unittest.mock import patch

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]

import pw_hash as _pw  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


def _make_user(
    app,
    email="pilot@test.com",
    password="TestPassword1!",
    with_totp=False,
    tenant_active=True,
):
    with app.app_context():
        tenant = Tenant(name="Log Test Hangar", is_active=tenant_active)
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw.hash(password),
            totp_secret=pyotp.random_base32() if with_totp else None,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id


class TestAuthFailureLogging:
    def test_wrong_password_emits_warning(self, app, client, caplog):
        _make_user(app)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login", data={"email": "pilot@test.com", "password": "wrongpassword"}
            )
        assert any(
            "[SECURITY]" in r.message and "auth.credentials.failed" in r.message
            for r in caplog.records
        )

    def test_unknown_email_emits_warning(self, app, client, caplog):
        _make_user(app)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login", data={"email": "nobody@test.com", "password": "anything"}
            )
        assert any(
            "[SECURITY]" in r.message and "auth.credentials.failed" in r.message
            for r in caplog.records
        )

    def test_warning_includes_email(self, app, client, caplog):
        _make_user(app, email="tracked@test.com")
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login", data={"email": "tracked@test.com", "password": "wrong"}
            )
        assert any("tracked@test.com" in r.message for r in caplog.records)

    def test_deactivated_tenant_emits_warning(self, app, client, caplog):
        # User is_active=True but their only tenant is deactivated
        _make_user(app, email="inactive@test.com", tenant_active=False)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login",
                data={"email": "inactive@test.com", "password": "TestPassword1!"},
            )
        assert any(
            "[SECURITY]" in r.message and "auth.credentials.deactivated" in r.message
            for r in caplog.records
        )

    def test_wrong_totp_emits_warning(self, app, client, caplog):
        _make_user(app, email="totp@test.com", with_totp=True)
        # Get past the credentials step
        client.post(
            "/login", data={"email": "totp@test.com", "password": "TestPassword1!"}
        )
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post("/login", data={"step": "totp", "totp_code": "000000"})
        assert any(
            "[SECURITY]" in r.message and "auth.totp.failed" in r.message
            for r in caplog.records
        )

    def test_successful_login_emits_no_security_warning(self, app, client, caplog):
        _make_user(app, email="ok@test.com", with_totp=False)
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login", data={"email": "ok@test.com", "password": "TestPassword1!"}
            )
        assert not any("[SECURITY]" in r.message for r in caplog.records)


class TestTOTPAntiReplay:
    def test_totp_replay_rejected_and_logged(self, app, client, caplog):
        """A valid TOTP code already consumed must be rejected on second use (CWE-294)."""
        uid = _make_user(app, email="replay@test.com", with_totp=True)

        with app.app_context():
            user = db.session.get(User, uid)
            valid_code = pyotp.TOTP(user.totp_secret).now()

        # First use: full login flow → succeeds, code stored in cache.
        client.post(
            "/login", data={"email": "replay@test.com", "password": "TestPassword1!"}
        )
        client.post("/login", data={"step": "totp", "totp_code": valid_code})

        # Second use (new session, same code): must be rejected as replay.
        client2 = app.test_client()
        client2.post(
            "/login", data={"email": "replay@test.com", "password": "TestPassword1!"}
        )
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            resp = client2.post(
                "/login", data={"step": "totp", "totp_code": valid_code}
            )

        assert resp.status_code == 200
        assert any(
            "[SECURITY]" in r.message and "auth.totp.replay" in r.message
            for r in caplog.records
        )


class TestTimingSafeLogin:
    def test_verify_always_called_for_unknown_email(self, app, client):
        """pw_hash.verify must be called even when the email is not in the DB,
        preventing timing-based account enumeration (CWE-208)."""
        _make_user(app)  # create at least one user so DB is initialised
        with patch("pw_hash.verify", return_value=False) as mock_verify:
            client.post(
                "/login",
                data={"email": "nobody@nowhere.com", "password": "anything"},
            )
        mock_verify.assert_called_once()

    def test_verify_always_called_for_known_email(self, app, client):
        """pw_hash.verify is called for known emails too (baseline sanity check)."""
        _make_user(app, email="known@test.com")
        with patch("pw_hash.verify", return_value=False) as mock_verify:
            client.post(
                "/login",
                data={"email": "known@test.com", "password": "wrongpassword"},
            )
        mock_verify.assert_called_once()


# ── pw_hash unit tests (N-19: Argon2id migration) ────────────────────────────


class TestPwHash:
    def test_produces_argon2id_prefix(self):
        assert _pw.hash("secret").startswith("$argon2id$")

    def test_two_hashes_differ(self):
        assert _pw.hash("same") != _pw.hash("same")


class TestPwVerify:
    def test_argon2id_correct(self):
        assert _pw.verify("hunter2", _pw.hash("hunter2")) is True

    def test_argon2id_wrong(self):
        assert _pw.verify("wrong", _pw.hash("hunter2")) is False

    def test_bcrypt_correct(self):
        h = bcrypt.hashpw(b"legacy", bcrypt.gensalt()).decode()
        assert _pw.verify("legacy", h) is True

    def test_bcrypt_wrong(self):
        h = bcrypt.hashpw(b"legacy", bcrypt.gensalt()).decode()
        assert _pw.verify("wrong", h) is False

    def test_garbage_hash_returns_false(self):
        assert _pw.verify("anything", "notahash") is False

    def test_malformed_bcrypt_hash_returns_false(self):
        # Starts with $2b$ so _is_bcrypt() is True, but checkpw raises
        assert _pw.verify("anything", "$2b$12$tooshort") is False


class TestPwNeedsRehash:
    def test_fresh_argon2id_does_not_need_rehash(self):
        assert _pw.needs_rehash(_pw.hash("pw")) is False

    def test_bcrypt_always_needs_rehash(self):
        h = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        assert _pw.needs_rehash(h) is True


class TestRehashOnLogin:
    """On login with a legacy bcrypt hash, the hash is upgraded to Argon2id."""

    def test_bcrypt_hash_upgraded_on_login(self, app, client):
        old_hash = bcrypt.hashpw(b"OldPass1234!", bcrypt.gensalt()).decode()
        with app.app_context():
            tenant = Tenant(name="Rehash Test")
            db.session.add(tenant)
            db.session.flush()
            user = User(email="rehash@test.com", password_hash=old_hash, is_active=True)
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
            )
            db.session.commit()
            uid = user.id

        client.post(
            "/login", data={"email": "rehash@test.com", "password": "OldPass1234!"}
        )

        with app.app_context():
            user = db.session.get(User, uid)
            assert user.password_hash.startswith("$argon2id$")
            assert _pw.verify("OldPass1234!", user.password_hash)


# ── IP backoff + account lockout tests (N-17) ─────────────────────────────────
# time.sleep is always patched — we test the logging/logic, not the actual delay.


class TestIPBackoff:
    def test_backoff_logged_at_third_failure(self, app, client, caplog):
        _make_user(app, email="backoff@test.com")
        with patch("auth.routes.time.sleep"):
            for _ in range(2):
                client.post(
                    "/login", data={"email": "backoff@test.com", "password": "wrong"}
                )
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
                client.post(
                    "/login", data={"email": "backoff@test.com", "password": "wrong"}
                )
        assert any(
            "auth.login.backoff" in r.message and "delay=2s" in r.message
            for r in caplog.records
        )

    def test_backoff_cleared_on_success(self, app, client, caplog):
        _make_user(app, email="clearip@test.com")
        with patch("auth.routes.time.sleep"):
            for _ in range(3):
                client.post(
                    "/login", data={"email": "clearip@test.com", "password": "wrong"}
                )
            client.post(
                "/login",
                data={"email": "clearip@test.com", "password": "TestPassword1!"},
            )
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
                client.post(
                    "/login", data={"email": "clearip@test.com", "password": "wrong"}
                )
        assert not any("auth.login.backoff" in r.message for r in caplog.records)


class TestAccountLockout:
    def _fail_n(self, client, email, n):
        with patch("auth.routes.time.sleep"):
            for _ in range(n):
                client.post("/login", data={"email": email, "password": "wrong"})

    def test_account_locked_after_threshold(self, app, client, caplog):
        _make_user(app, email="lockme@test.com")
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            self._fail_n(client, "lockme@test.com", 10)
        assert any("auth.login.account_locked" in r.message for r in caplog.records)

    def test_locked_account_blocked_with_flash(self, app, client):
        _make_user(app, email="blocked@test.com")
        self._fail_n(client, "blocked@test.com", 10)
        resp = client.post(
            "/login",
            data={"email": "blocked@test.com", "password": "wrong"},
            follow_redirects=True,
        )
        assert b"temporarily locked" in resp.data.lower()

    def test_locked_account_blocks_correct_password(self, app, client):
        _make_user(app, email="lockpw@test.com")
        self._fail_n(client, "lockpw@test.com", 10)
        resp = client.post(
            "/login",
            data={"email": "lockpw@test.com", "password": "TestPassword1!"},
            follow_redirects=True,
        )
        assert b"temporarily locked" in resp.data.lower()

    def test_blocked_attempt_logged(self, app, client, caplog):
        _make_user(app, email="logblock@test.com")
        self._fail_n(client, "logblock@test.com", 10)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post(
                "/login", data={"email": "logblock@test.com", "password": "wrong"}
            )
        assert any("auth.login.account_blocked" in r.message for r in caplog.records)

    def test_expired_lock_is_cleared_and_login_proceeds(self, app, client):
        # Covers lines 95-97: lock exists in cache but timestamp has already passed
        from datetime import datetime, timedelta, timezone
        from extensions import cache as _cache  # pyright: ignore[reportMissingImports]

        _make_user(app, email="explock@test.com")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with app.app_context():
            _cache.set("login_lock_acct:explock@test.com", expired, timeout=60)
        # Expired lock must not block a valid login
        resp = client.post(
            "/login",
            data={"email": "explock@test.com", "password": "TestPassword1!"},
        )
        assert resp.status_code == 302
        assert b"temporarily locked" not in resp.data
