"""
Tests for CSRF protection.

Verifies that:
- The csrf-token meta tag is present and non-empty on every HTML page.
- POST requests without a CSRF token, or with a forged/mismatched one, are
  refused: sign-in forms are shown again, other forms get an explanation page.
- POST requests with a valid token are not rejected by the CSRF layer.
- The X-CSRFToken header (used by AJAX fetch() calls) is also accepted.
"""

import re
import tempfile

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
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


def _login(client, password="TestPassword1!"):
    token = _token_from_html(client.get("/login").data)
    resp = client.post(
        "/login",
        data={"email": "csrf@test.com", "password": password, "csrf_token": token},
    )
    return token, resp


class TestCSRFEnforcement:
    @pytest.mark.parametrize("token", [None, "not-a-real-token"])
    def test_sign_in_without_a_valid_token_is_refused_and_shown_again(
        self, csrf_client, token
    ):
        """Even with correct credentials: the form is shown again, with a reason."""
        data = {"email": "csrf@test.com", "password": "TestPassword1!"}
        if token:
            data["csrf_token"] = token
        resp = csrf_client.post("/login", data=data)
        assert resp.status_code == 302
        assert resp.location.endswith("/login")
        with csrf_client.session_transaction() as sess:
            assert "user_id" not in sess
        page = csrf_client.get("/login").data
        assert b"The sign-in form had expired" in page

    def test_other_forms_without_a_valid_token_are_refused(self, csrf_client):
        _login(csrf_client)
        resp = csrf_client.post(
            "/profile",
            data={"action": "update_name", "name": "Forged"},
            headers={"X-CSRFToken": "not-a-real-token"},
        )
        assert resp.status_code == 400
        with csrf_client.application.app_context():
            assert User.query.filter_by(email="csrf@test.com").one().name is None

    def test_post_with_valid_form_token_passes_csrf_check(self, csrf_client):
        """A valid token reaches the view: wrong credentials re-render the form."""
        _, resp = _login(csrf_client, password="wrongpassword")
        assert resp.status_code == 200
        assert b"The sign-in form had expired" not in resp.data

    def test_post_with_valid_header_token_passes_csrf_check(self, csrf_client):
        """X-CSRFToken header (used by fetch() AJAX calls) is accepted as an alternative."""
        token = _token_from_html(csrf_client.get("/login").data)
        resp = csrf_client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrongpassword"},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code == 200


class TestExpiredForms:
    """A token that no longer matches the session: say what to do, never a
    bare error page (the installed app has no browser navigation buttons)."""

    def test_sign_in_submitted_again_after_it_succeeded_goes_to_the_dashboard(
        self, csrf_client, caplog
    ):
        # e.g. the TOTP form auto-submitted and the user also pressed Enter:
        # the first submission signed in and replaced the session.
        token, first = _login(csrf_client)
        assert first.status_code == 302
        with caplog.at_level("WARNING", logger="openhangar.auth"):
            again = csrf_client.post(
                "/login",
                data={
                    "email": "csrf@test.com",
                    "password": "TestPassword1!",
                    "csrf_token": token,
                },
            )
        assert again.status_code == 302
        assert again.location == "/"
        assert "[SECURITY] csrf.failed path=/login" in caplog.text
        with csrf_client.session_transaction() as sess:
            assert sess.get("user_id")

    def test_other_forms_get_a_page_with_ways_back(self, csrf_client):
        _login(csrf_client)
        resp = csrf_client.post(
            "/profile",
            data={"action": "update_name", "name": "Forged", "csrf_token": "stale"},
            headers={"Referer": "http://localhost/profile?tab=security"},
        )
        assert resp.status_code == 400
        assert resp.headers["X-OH-Form-Expired"] == "1"
        html = resp.data.decode()
        assert "This form has expired" in html
        assert 'href="/profile?tab=security"' in html
        assert 'href="/"' in html

    @pytest.mark.parametrize(
        "referer", ["https://elsewhere.example/profile", "http://localhost//evil", ""]
    )
    def test_the_way_back_stays_on_this_site(self, csrf_client, referer):
        _login(csrf_client)
        resp = csrf_client.post(
            "/profile",
            data={"action": "update_name", "name": "Forged", "csrf_token": "stale"},
            headers={"Referer": referer} if referer else {},
        )
        html = resp.data.decode()
        assert "elsewhere.example" not in html
        assert 'href="//evil"' not in html
        assert html.count('href="/"') >= 2  # the way back falls back to home


class TestCSRFTimeLimit:
    def test_csrf_tokens_never_expire_independently_of_the_session(self):
        # Some nav pages (sw.js SWR_ROUTES) are cached client-side, so a page
        # containing a form can be served well after it was first fetched.
        # Flask-WTF's 1-hour default would make that form's embedded token
        # fail validation out of nowhere; the app must disable that separate
        # clock and rely solely on session validity instead.
        app = create_app()
        assert app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_boosted_forms_show_the_expired_form_page():
    """HTMX does not swap 4xx responses; ui.js opts this marked one in."""
    from pathlib import Path

    ui = (Path(__file__).parent.parent / "app" / "static" / "js" / "ui.js").read_text()
    assert "xhr.getResponseHeader('X-OH-Form-Expired') === '1'" in ui
    assert "evt.detail.shouldSwap = true;" in ui
