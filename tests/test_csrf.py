"""
Tests for CSRF protection.

Verifies that:
- The csrf-token meta tag is present and non-empty on every HTML page.
- POST requests without a CSRF token are rejected with 400.
- POST requests with a forged/mismatched token are rejected with 400.
- POST requests with a valid token are not rejected by the CSRF layer.
- The X-CSRFToken header (used by AJAX fetch() calls) is also accepted.
"""

import re
import tempfile

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


@pytest.fixture()
def csrf_app():
    """Minimal app with CSRF protection *enabled* — the opposite of the main suite.

    A single owner user is created so the app is past the "fresh install" redirect
    and routes like /login render a full HTML page rather than redirecting to /setup.
    """
    upload_dir = tempfile.mkdtemp()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["RATELIMIT_ENABLED"] = False
    app.config["WTF_CSRF_TIME_LIMIT"] = None  # no token expiry during tests
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["UPLOAD_FOLDER"] = upload_dir
    with app.app_context():
        db.create_all()
        tenant = Tenant(name="CSRF Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="csrf@test.com",
            password_hash=bcrypt.hashpw(b"TestPassword1!", bcrypt.gensalt()).decode(),
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
    yield app
    with app.app_context():
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def csrf_client(csrf_app):
    return csrf_app.test_client()


def _token_from_html(data: bytes) -> str:
    """Extract the CSRF token value from a rendered page's <meta name='csrf-token'>."""
    match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', data)
    assert match, "csrf-token meta tag not found in response"
    return match.group(1).decode()


class TestCSRFMetaTag:
    def test_meta_tag_present_on_public_page(self, csrf_client):
        """The csrf-token meta tag is rendered on every page served through base.html."""
        resp = csrf_client.get("/login")
        assert resp.status_code == 200
        assert b'<meta name="csrf-token"' in resp.data

    def test_meta_tag_token_is_non_empty(self, csrf_client):
        """The rendered CSRF token is a non-trivial string (not blank or whitespace)."""
        resp = csrf_client.get("/login")
        token = _token_from_html(resp.data)
        assert len(token) > 10


class TestCSRFEnforcement:
    def test_post_without_token_returns_400(self, csrf_client):
        """A POST with no csrf_token field is rejected before any business logic runs."""
        resp = csrf_client.post(
            "/login",
            data={"email": "x@example.com", "password": "wrongpassword"},
        )
        assert resp.status_code == 400

    def test_post_with_forged_token_returns_400(self, csrf_client):
        """A POST with a made-up csrf_token value is rejected."""
        resp = csrf_client.post(
            "/login",
            data={
                "email": "x@example.com",
                "password": "wrongpassword",
                "csrf_token": "not-a-real-token",
            },
        )
        assert resp.status_code == 400

    def test_post_with_valid_form_token_passes_csrf_check(self, csrf_client):
        """A token obtained from the meta tag is accepted; the response is not a CSRF 400."""
        get_resp = csrf_client.get("/login")
        token = _token_from_html(get_resp.data)

        # Auth will fail (no such user), but CSRF must not — so status != 400.
        resp = csrf_client.post(
            "/login",
            data={
                "email": "nobody@example.com",
                "password": "wrongpassword",
                "csrf_token": token,
            },
        )
        assert resp.status_code != 400, "Valid CSRF token should not be rejected"

    def test_post_with_valid_header_token_passes_csrf_check(self, csrf_client):
        """X-CSRFToken header (used by fetch() AJAX calls) is accepted as an alternative."""
        get_resp = csrf_client.get("/login")
        token = _token_from_html(get_resp.data)

        resp = csrf_client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrongpassword"},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code != 400, "X-CSRFToken header should be accepted"
