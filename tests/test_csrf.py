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
from datetime import date

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    FlightEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


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
            password_hash=_pw_hash.hash("TestPassword1!"),
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


class TestCSRFOfflineSync:
    """The offline sync endpoint (Phase 38b) is JSON-only — verify its CSRF
    failures return JSON (via the blueprint's CSRFError handler) rather than
    the default HTML error page, and that a valid X-CSRFToken header works."""

    @pytest.fixture()
    def flight_id(self, csrf_app):
        with csrf_app.app_context():
            tenant = Tenant.query.filter_by(name="CSRF Test Hangar").first()
            ac = Aircraft(
                tenant_id=tenant.id, registration="OO-CSRF", make="Cessna", model="172S"
            )
            db.session.add(ac)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=ac.id,
                date=date(2024, 1, 15),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.commit()
            return fe.id

    def _login(self, csrf_client):
        with csrf_client.session_transaction() as sess:
            with csrf_client.application.app_context():
                uid = User.query.filter_by(email="csrf@test.com").first().id
            sess["user_id"] = uid

    def test_sync_without_csrf_token_returns_json_400(self, csrf_client, flight_id):
        self._login(csrf_client)
        resp = csrf_client.post(
            f"/api/offline/flights/{flight_id}/sync",
            json={"fields": {}, "base": {}},
        )
        assert resp.status_code == 400
        errors = resp.get_json()["errors"]
        assert any("csrf" in e.lower() for e in errors), errors

    def test_sync_with_valid_header_token_passes_csrf_check(
        self, csrf_client, flight_id
    ):
        self._login(csrf_client)
        token = csrf_client.get("/api/offline/csrf").get_json()["csrf_token"]

        resp = csrf_client.post(
            f"/api/offline/flights/{flight_id}/sync",
            json={"fields": {}, "base": {}},
            headers={"X-CSRFToken": token},
        )
        # A valid token clears the CSRF layer; the empty body is then
        # rejected by the view's own malformed-body check instead — proven
        # by the distinct, non-CSRF error message (unlike the no-token case
        # above, which never reaches the view).
        assert resp.status_code == 400
        assert resp.get_json() == {
            "status": "invalid",
            "errors": ["Malformed request."],
        }


class TestCSRFTimeLimit:
    def test_csrf_tokens_never_expire_independently_of_the_session(self):
        # Some nav pages (sw.js SWR_ROUTES) are cached client-side, so a page
        # containing a form can be served well after it was first fetched.
        # Flask-WTF's 1-hour default would make that form's embedded token
        # fail validation out of nowhere; the app must disable that separate
        # clock and rely solely on session validity instead.
        app = create_app()
        assert app.config["WTF_CSRF_TIME_LIMIT"] is None
