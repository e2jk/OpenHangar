import os

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user(
    app,
    email="admin@example.com",
    password="testpassword123",
    with_totp=True,
    is_active=True,
):
    """Insert a fully-formed user + tenant into the test DB."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            totp_secret=pyotp.random_base32() if with_totp else None,
            is_active=is_active,
        )
        db.session.add(user)
        db.session.flush()

        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()


def _login_session(app, client):
    """Inject a valid user_id into the session without going through the login flow."""
    with app.app_context():
        uid = User.query.filter_by(email="admin@example.com").first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


# ── Landing / routing ─────────────────────────────────────────────────────────


class TestIndex:
    # Fresh install: no users → salesy landing page
    def test_ok(self, client):
        assert client.get("/").status_code == 200

    def test_contains_brand(self, client):
        assert b"OpenHangar" in client.get("/").data

    def test_contains_cta(self, client):
        assert b"Get Started" in client.get("/").data

    # Initialised but not logged in → welcome-back page
    def test_shows_welcome_when_users_exist(self, app, client):
        _create_user(app)
        response = client.get("/")
        assert response.status_code == 200
        assert b"Welcome back" in response.data
        assert b"Get Started" not in response.data

    # Logged in → dashboard
    def test_shows_dashboard_when_logged_in(self, app, client):
        _create_user(app)
        _login_session(app, client)
        response = client.get("/")
        assert response.status_code == 200
        assert b"Dashboard" in response.data
        assert b"Welcome back" not in response.data

    def test_unknown_route_returns_404(self, client):
        assert client.get("/nonexistent").status_code == 404


# ── Health ────────────────────────────────────────────────────────────────────


class TestHealth:
    def test_ok(self, client):
        assert client.get("/health").status_code == 200

    def test_json_response(self, client):
        assert client.get("/health").get_json() == {"status": "ok"}

    def test_json_content_type(self, client):
        assert "application/json" in client.get("/health").content_type


# ── Not Yet Implemented ───────────────────────────────────────────────────────


class TestNotYetImplemented:
    def test_returns_501(self, client):
        assert client.get("/not-yet-implemented").status_code == 501

    def test_feature_name_in_response(self, client):
        resp = client.get("/not-yet-implemented?feature=Logbook")
        assert b"Logbook" in resp.data


# ── Login ─────────────────────────────────────────────────────────────────────


class TestLogin:
    def test_redirects_to_setup_when_no_users(self, client):
        response = client.get("/login")
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]

    def test_shows_login_form_when_users_exist(self, app, client):
        _create_user(app)
        response = client.get("/login")
        assert response.status_code == 200
        assert b"Log in" in response.data or b"Continue" in response.data

    def test_already_logged_in_redirects_to_dashboard(self, app, client):
        _create_user(app)
        _login_session(app, client)
        response = client.get("/login")
        assert response.status_code == 302
        assert response.headers["Location"] == "/"

    # ── Credentials step ──

    def test_valid_credentials_without_totp_logs_in_directly(self, app, client):
        _create_user(app, with_totp=False)
        response = client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]

    def test_valid_credentials_with_totp_redirects_to_totp_step(self, app, client):
        _create_user(app, with_totp=True)
        response = client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        assert response.status_code == 302
        assert "step=totp" in response.headers["Location"]

    def test_wrong_password_rejected(self, app, client):
        _create_user(app)
        response = client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_unknown_email_rejected(self, app, client):
        _create_user(app)
        response = client.post(
            "/login",
            data={
                "email": "nobody@example.com",
                "password": "testpassword123",
            },
        )
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_inactive_user_rejected(self, app, client):
        _create_user(app, is_active=False)
        response = client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_empty_email_rejected(self, app, client):
        _create_user(app)
        response = client.post(
            "/login",
            data={
                "email": "",
                "password": "testpassword123",
            },
        )
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_session_contains_user_id_after_login_without_totp(self, app, client):
        _create_user(app, with_totp=False)
        with app.app_context():
            uid = User.query.filter_by(email="admin@example.com").first().id
        client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        with client.session_transaction() as sess:
            assert sess.get("user_id") == uid

    # ── TOTP step ──

    def test_totp_step_without_pending_session_redirects(self, app, client):
        _create_user(app)
        response = client.get("/login?step=totp")
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]

    def test_valid_totp_completes_login(self, app, client):
        _create_user(app)
        client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            valid_code = pyotp.TOTP(user.totp_secret).now()
        response = client.post(
            "/login",
            data={
                "step": "totp",
                "totp_code": valid_code,
            },
        )
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]

    def test_wrong_totp_rejected(self, app, client):
        _create_user(app)
        client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        response = client.post(
            "/login",
            data={
                "step": "totp",
                "totp_code": "000000",
            },
        )
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_pending_session_preserved_after_bad_totp(self, app, client):
        """User can retry the TOTP step after entering a wrong code."""
        _create_user(app)
        client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        client.post("/login", data={"step": "totp", "totp_code": "000000"})
        with client.session_transaction() as sess:
            assert "login_pending_user_id" in sess

    def test_pending_session_cleared_after_successful_totp(self, app, client):
        """login_pending_user_id is gone and user_id is set after a successful TOTP."""
        _create_user(app)
        client.post(
            "/login",
            data={
                "email": "admin@example.com",
                "password": "testpassword123",
            },
        )
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            valid_code = pyotp.TOTP(user.totp_secret).now()
            uid = user.id
        client.post("/login", data={"step": "totp", "totp_code": valid_code})
        with client.session_transaction() as sess:
            assert "login_pending_user_id" not in sess
            assert sess.get("user_id") == uid


# ── Profile ───────────────────────────────────────────────────────────────────


class TestProfile:
    def test_profile_redirects_to_logout_when_user_deleted(self, app, client):
        """auth/routes.py:232 — user row deleted while session is still alive."""
        _create_user(app)
        uid = _login_session(app, client)
        with app.app_context():
            TenantUser.query.filter_by(user_id=uid).delete()
            user = db.session.get(User, uid)
            db.session.delete(user)
            db.session.commit()
        response = client.get("/profile")
        assert response.status_code == 302
        assert "/logout" in response.headers["Location"]

    def test_profile_update_name_saves(self, app, client):
        """auth/routes.py:237,254-258 — action=update_name persists the display name."""
        _create_user(app)
        _login_session(app, client)
        resp = client.post(
            "/profile",
            data={"action": "update_name", "name": "Alice Test"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            u = User.query.filter_by(email="admin@example.com").first()
            assert u.name == "Alice Test"

    def test_profile_update_name_empty_clears(self, app, client):
        """auth/routes.py:255 — whitespace-only name is stored as None."""
        _create_user(app)
        _login_session(app, client)
        resp = client.post(
            "/profile",
            data={"action": "update_name", "name": "  "},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            u = User.query.filter_by(email="admin@example.com").first()
            assert u.name is None


# ── Logout ────────────────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_clears_session_and_redirects(self, app, client):
        _create_user(app)
        _login_session(app, client)
        response = client.get("/logout")
        assert response.status_code == 302
        with client.session_transaction() as sess:
            assert "user_id" not in sess

    def test_logout_when_not_logged_in_redirects_gracefully(self, app, client):
        _create_user(app)
        response = client.get("/logout")
        assert response.status_code == 302


# ── Setup ─────────────────────────────────────────────────────────────────────


class TestSetup:
    def test_setup_page_ok_on_fresh_install(self, client):
        assert client.get("/setup").status_code == 200

    def test_setup_redirects_to_login_when_users_exist(self, app, client):
        _create_user(app)
        response = client.get("/setup")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_step1_validation_rejects_short_password(self, client):
        response = client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "short",
            },
        )
        assert response.status_code == 200
        assert b"12 characters" in response.data

    def test_step1_rejects_invalid_email(self, client):
        response = client.post(
            "/setup",
            data={
                "step": "account",
                "email": "notanemail",
                "password": "validpassword123",
            },
        )
        assert response.status_code == 200
        assert b"valid email" in response.data

    def test_step1_valid_redirects_to_step2(self, client):
        response = client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        assert response.status_code == 302
        assert "step=totp" in response.headers["Location"]

    def test_step2_get_without_completing_step1_redirects(self, client):
        """Accessing the TOTP step directly (no session) sends user back to step 1."""
        response = client.get("/setup?step=totp")
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]

    def test_step2_invalid_totp_shows_error(self, client):
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        response = client.post(
            "/setup",
            data={
                "step": "totp",
                "totp_code": "000000",
            },
        )
        assert response.status_code == 200
        assert b"Invalid code" in response.data

    def test_step2_skip_creates_user_without_totp(self, app, client):
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        response = client.post(
            "/setup",
            data={
                "step": "totp",
                "action": "skip",
            },
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            assert user is not None
            assert user.totp_secret is None

    def test_full_setup_creates_user_and_redirects_to_login(self, app, client):
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )

        with client.session_transaction() as sess:
            totp_secret = sess["setup_totp_secret"]

        valid_code = pyotp.TOTP(totp_secret).now()

        response = client.post(
            "/setup",
            data={
                "step": "totp",
                "totp_code": valid_code,
            },
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            assert user is not None
            assert user.totp_secret is not None
            assert Tenant.query.count() == 1

    def test_session_cleaned_up_after_full_setup(self, client):
        """Setup session keys are removed once the account is created."""
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        with client.session_transaction() as sess:
            totp_secret = sess["setup_totp_secret"]
        valid_code = pyotp.TOTP(totp_secret).now()
        client.post("/setup", data={"step": "totp", "totp_code": valid_code})
        with client.session_transaction() as sess:
            for key in (
                "setup_email",
                "setup_password_hash",
                "setup_totp_secret",
                "setup_provisioning_uri",
            ):
                assert key not in sess

    def test_session_cleaned_up_after_skip(self, client):
        """Setup session keys are removed even when TOTP is skipped."""
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        client.post("/setup", data={"step": "totp", "action": "skip"})
        with client.session_transaction() as sess:
            for key in (
                "setup_email",
                "setup_password_hash",
                "setup_totp_secret",
                "setup_provisioning_uri",
            ):
                assert key not in sess


# ── Context processor ─────────────────────────────────────────────────────────


class TestContextProcessor:
    """Verify that the context processor injects the right values into templates."""

    def test_has_users_false_on_fresh_install(self, client, captured_templates):
        client.get("/")
        _, context = captured_templates[0]
        assert context["has_users"] is False

    def test_has_users_true_when_users_exist(self, app, client, captured_templates):
        _create_user(app)
        client.get("/")
        _, context = captured_templates[0]
        assert context["has_users"] is True

    def test_logged_in_false_without_session(self, client, captured_templates):
        client.get("/")
        _, context = captured_templates[0]
        assert context["logged_in"] is False

    def test_logged_in_true_with_session(self, app, client, captured_templates):
        _create_user(app)
        _login_session(app, client)
        client.get("/")
        _, context = captured_templates[0]
        assert context["logged_in"] is True


# ── Navigation ────────────────────────────────────────────────────────────────


class TestNavigation:
    """Verify that the navbar shows the right elements per auth state."""

    def test_fresh_install_has_no_auth_buttons(self, client):
        """Navbar shows no auth button (btn-nav-login) on a fresh install.
        The landing page body may still link to /login in CTAs — that's intentional."""
        data = client.get("/").data
        assert b"btn-nav-login" not in data

    def test_initialized_shows_login_button(self, app, client):
        """Welcome page shows Log In button when users exist but nobody is logged in."""
        _create_user(app)
        assert b"Log In" in client.get("/").data

    def test_logged_in_shows_logout_button_not_login(self, app, client):
        """Dashboard shows Log Out; Log In must not appear."""
        _create_user(app)
        _login_session(app, client)
        data = client.get("/").data
        assert b"Log Out" in data
        assert b"Log In" not in data

    def test_logged_in_shows_nav_items(self, app, client):
        """Nav links only appear when logged in."""
        _create_user(app)
        _login_session(app, client)
        data = client.get("/").data
        assert b"Aircraft" in data
        assert b"Maintenance" in data
        assert b"Configuration" in data

    def test_not_logged_in_hides_nav_items(self, app, client):
        """nav-link CSS class must not appear on the welcome page navbar.
        (The page body mentions 'Logbook' in info cards — that's fine.)"""
        _create_user(app)
        data = client.get("/").data
        assert b"nav-link" not in data


# ── Coverage gap: TOTP POST without pending session ───────────────────────────


class TestLoginTotpEdgeCases:
    def test_post_totp_without_pending_session_redirects(self, app, client):
        """POST step=totp with no login_pending_user_id → redirect back to login."""
        _create_user(app)
        response = client.post("/login", data={"step": "totp", "totp_code": "123456"})
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]

    def test_post_totp_with_deleted_user_redirects(self, app, client):
        """login_pending_user_id points to a user that no longer exists."""
        _create_user(app)
        with app.app_context():
            uid = User.query.filter_by(email="admin@example.com").first().id
        with client.session_transaction() as sess:
            sess["login_pending_user_id"] = uid + 9999  # non-existent
        response = client.post("/login", data={"step": "totp", "totp_code": "123456"})
        assert response.status_code == 302
        assert "step=totp" not in response.headers["Location"]


# ── Coverage gap: GET /setup?step=totp with valid session ─────────────────────


class TestSetupEdgeCases:
    def test_get_setup_totp_with_valid_session_renders_form(self, client):
        """GET /setup?step=totp after completing step 1 renders the TOTP page."""
        client.post(
            "/setup",
            data={
                "step": "account",
                "email": "admin@example.com",
                "password": "validpassword123",
            },
        )
        response = client.get("/setup?step=totp")
        assert response.status_code == 200
        assert (
            b"authenticator" in response.data.lower()
            or b"totp" in response.data.lower()
        )

    def test_post_setup_totp_with_expired_session_redirects(self, client):
        """POST step=totp without first completing step 1 → session expired redirect."""
        response = client.post("/setup", data={"step": "totp", "totp_code": "123456"})
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]


# ── Coverage gap: FLASK_ENV=development sets TEMPLATES_AUTO_RELOAD ────────────


class TestDevelopmentConfig:
    def test_templates_auto_reload_in_development(self):
        from init import create_app  # pyright: ignore[reportMissingImports]

        old = os.environ.get("FLASK_ENV")
        try:
            os.environ["FLASK_ENV"] = "development"
            app = create_app()
            assert app.config.get("TEMPLATES_AUTO_RELOAD") is True
        finally:
            if old is None:
                os.environ.pop("FLASK_ENV", None)
            else:
                os.environ["FLASK_ENV"] = old
