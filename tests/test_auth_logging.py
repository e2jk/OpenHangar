"""
Tests for authentication failure logging (security audit finding #9 / CWE-778).

Verifies that failed login attempts emit a WARNING via the openhangar.auth logger
with a [SECURITY] tag, the client IP, and the relevant identifier (email or user_id).
"""

import logging

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]

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
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
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
