"""
Tests for N-21: per-tenant require_totp enforcement.

Covers:
- Login redirects to totp-enrol when tenant.require_totp=True and user has no TOTP
- totp-enrol GET is gated (requires totp_must_enrol session key)
- totp-enrol POST with invalid code keeps user on enrolment page
- totp-enrol POST with valid code saves totp_secret and completes login
- [SECURITY] auth.totp.enrolment_forced is logged on successful enrolment
- Disabling TOTP is blocked when tenant.require_totp=True
- Users with an existing TOTP secret bypass enrolment and go to normal totp step
- Settings page toggle saves require_totp to tenant
"""

import logging

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]

from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]

PASSWORD = "SecurePass1!"


def _make_tenant_and_user(app, require_totp=False, with_totp=False):
    with app.app_context():
        tenant = Tenant(name="MFA Hangar", is_active=True, require_totp=require_totp)
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="pilot@mfa.test",
            password_hash=_pw_hash.hash(PASSWORD),
            totp_secret=pyotp.random_base32() if with_totp else None,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


class TestRequireTotpLogin:
    def test_redirects_to_enrolment_when_required_and_no_totp(self, app, client):
        _make_tenant_and_user(app, require_totp=True)
        r = client.post(
            "/login", data={"email": "pilot@mfa.test", "password": PASSWORD}
        )
        assert r.status_code == 302
        assert "/login?step=totp-enrol" in r.headers["Location"]

    def test_no_redirect_when_not_required(self, app, client):
        _make_tenant_and_user(app, require_totp=False)
        r = client.post(
            "/login", data={"email": "pilot@mfa.test", "password": PASSWORD}
        )
        # Should complete login (302 to index), not to totp-enrol
        assert r.status_code == 302
        assert "totp-enrol" not in r.headers["Location"]

    def test_existing_totp_bypasses_enrolment(self, app, client):
        _make_tenant_and_user(app, require_totp=True, with_totp=True)
        r = client.post(
            "/login", data={"email": "pilot@mfa.test", "password": PASSWORD}
        )
        assert r.status_code == 302
        # Should go to the normal totp step, not totp-enrol
        assert "totp" in r.headers["Location"]
        assert "totp-enrol" not in r.headers["Location"]


class TestRequireTotpEnrolPage:
    def test_get_blocked_without_session(self, app, client):
        # Must create a user so _no_users() doesn't short-circuit before line 86
        _make_tenant_and_user(app, require_totp=True)
        r = client.get("/login?step=totp-enrol")
        assert r.status_code == 302
        assert "totp-enrol" not in r.headers["Location"]

    def test_post_enrol_without_session_redirects(self, app, client):
        # Covers _login_totp_enrol() guard (line 210): POST without session keys
        _make_tenant_and_user(app, require_totp=True)
        r = client.post("/login", data={"step": "totp-enrol", "totp_code": "123456"})
        assert r.status_code == 302
        assert "totp-enrol" not in r.headers["Location"]

    def test_post_enrol_nonexistent_user_redirects(self, app, client):
        # Covers lines 214-215: session has pending_id but user no longer in DB.
        # Use a non-existent user ID (999999) — no user creation needed.
        _make_tenant_and_user(app, require_totp=True)  # ensures _no_users() is False
        with client.session_transaction() as sess:
            sess["login_pending_user_id"] = 999999
            sess["totp_must_enrol"] = True
            sess["enrol_totp_secret"] = pyotp.random_base32()
            sess["enrol_totp_uri"] = "otpauth://totp/test"
        r = client.post("/login", data={"step": "totp-enrol", "totp_code": "000000"})
        assert r.status_code == 302

    def test_get_renders_when_session_set(self, app, client):
        _make_tenant_and_user(app, require_totp=True)
        # Reach the enrolment page via the login flow
        client.post("/login", data={"email": "pilot@mfa.test", "password": PASSWORD})
        r = client.get("/login?step=totp-enrol")
        assert r.status_code == 200
        assert b"qr-container" in r.data

    def test_post_invalid_code_stays_on_page(self, app, client):
        _make_tenant_and_user(app, require_totp=True)
        client.post("/login", data={"email": "pilot@mfa.test", "password": PASSWORD})
        r = client.post("/login", data={"step": "totp-enrol", "totp_code": "000000"})
        assert r.status_code == 200
        assert b"qr-container" in r.data

    def test_post_valid_code_completes_login(self, app, client):
        uid, _ = _make_tenant_and_user(app, require_totp=True)
        client.post("/login", data={"email": "pilot@mfa.test", "password": PASSWORD})
        with client.session_transaction() as sess:
            secret = sess.get("enrol_totp_secret")
        assert secret is not None
        code = pyotp.TOTP(secret).now()
        r = client.post("/login", data={"step": "totp-enrol", "totp_code": code})
        assert r.status_code == 302
        assert r.headers["Location"] in ("/", "http://localhost/")
        # User should now have a totp_secret saved
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret == secret

    def test_post_valid_code_logs_security_event(self, app, client, caplog):
        _make_tenant_and_user(app, require_totp=True)
        client.post("/login", data={"email": "pilot@mfa.test", "password": PASSWORD})
        with client.session_transaction() as sess:
            secret = sess.get("enrol_totp_secret")
        code = pyotp.TOTP(secret).now()
        with caplog.at_level(logging.WARNING, logger="openhangar.auth"):
            client.post("/login", data={"step": "totp-enrol", "totp_code": code})
        assert any(
            "[SECURITY]" in r.message and "auth.totp.enrolment_forced" in r.message
            for r in caplog.records
        )

    def test_session_cleared_after_enrolment(self, app, client):
        _make_tenant_and_user(app, require_totp=True)
        client.post("/login", data={"email": "pilot@mfa.test", "password": PASSWORD})
        with client.session_transaction() as sess:
            secret = sess.get("enrol_totp_secret")
        code = pyotp.TOTP(secret).now()
        client.post("/login", data={"step": "totp-enrol", "totp_code": code})
        with client.session_transaction() as sess:
            assert "totp_must_enrol" not in sess
            assert "enrol_totp_secret" not in sess


class TestRequireTotpDisableGuard:
    def _login(self, client, uid):
        with client.session_transaction() as sess:
            sess["user_id"] = uid

    def test_disable_blocked_when_required(self, app, client):
        uid, _ = _make_tenant_and_user(app, require_totp=True, with_totp=True)
        self._login(client, uid)
        r = client.post(
            "/profile",
            data={"action": "disable_totp", "current_password": PASSWORD},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"administrator requires" in r.data
        # TOTP secret must remain
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret is not None

    def test_disable_allowed_when_not_required(self, app, client):
        uid, _ = _make_tenant_and_user(app, require_totp=False, with_totp=True)
        self._login(client, uid)
        r = client.post(
            "/profile",
            data={"action": "disable_totp", "current_password": PASSWORD},
            follow_redirects=True,
        )
        assert r.status_code == 200
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret is None


class TestRequireTotpSettingsToggle:
    def _login(self, client, uid):
        with client.session_transaction() as sess:
            sess["user_id"] = uid

    def test_save_require_totp_true(self, app, client):
        uid, tid = _make_tenant_and_user(app, require_totp=False)
        self._login(client, uid)
        client.post(
            "/config/profile",
            data={"operating_model": "sole_operator", "require_totp": "on"},
        )
        with app.app_context():
            tenant = db.session.get(Tenant, tid)
            assert tenant.require_totp is True

    def test_save_require_totp_false(self, app, client):
        uid, tid = _make_tenant_and_user(app, require_totp=True)
        self._login(client, uid)
        client.post(
            "/config/profile",
            data={"operating_model": "sole_operator"},
            # no require_totp key → checkbox unchecked
        )
        with app.app_context():
            tenant = db.session.get(Tenant, tid)
            assert tenant.require_totp is False
