"""
Tests for login rate limiting (security audit finding #8 / CWE-307).

Uses a dedicated app fixture with rate limiting *enabled* and a very tight
limit so the test can trigger it quickly without 20 real POST requests.
"""

import tempfile

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


@pytest.fixture()
def rl_app():
    """App with rate limiting enabled and a tight 3-per-minute login limit for testing."""
    upload_dir = tempfile.mkdtemp()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["RATELIMIT_ENABLED"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["UPLOAD_FOLDER"] = upload_dir
    # Tighten the login limit to 3/minute so tests don't need 20 requests
    app.config["LOGIN_RATE_LIMIT"] = "3 per minute"
    with app.app_context():
        db.create_all()
        tenant = Tenant(name="RL Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="rl@test.com",
            password_hash=_pw_hash.hash("TestPassword1!"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
    yield app
    with app.app_context():
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def rl_client(rl_app):
    return rl_app.test_client()


class TestLoginRateLimit:
    def test_repeated_failures_eventually_get_429(self, rl_client):
        """Exceeding the per-IP login limit returns 429 Too Many Requests."""
        responses = [
            rl_client.post(
                "/login", data={"email": "rl@test.com", "password": "wrongpassword"}
            ).status_code
            for _ in range(5)
        ]
        assert 429 in responses, f"Expected a 429 among: {responses}"

    def test_valid_login_before_limit_succeeds(self, rl_client):
        """A single correct login under the limit is not blocked."""
        resp = rl_client.post(
            "/login", data={"email": "rl@test.com", "password": "TestPassword1!"}
        )
        assert resp.status_code != 429
