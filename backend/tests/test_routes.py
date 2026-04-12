import bcrypt
import pyotp
from models import Role, Tenant, TenantUser, User, db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_user(app, email="admin@example.com", password="testpassword123"):
    """Insert a fully-formed user + tenant into the test DB."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            totp_secret=pyotp.random_base32(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()

        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
        db.session.commit()


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
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            uid = user.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
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
        assert b"Log in" in response.data

    def test_post_valid_credentials_redirects(self, app, client):
        _create_user(app)
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            valid_code = pyotp.TOTP(user.totp_secret).now()
        response = client.post("/login", data={
            "email": "admin@example.com",
            "password": "testpassword123",
            "totp_code": valid_code,
        })
        assert response.status_code == 302

    def test_post_wrong_password_rejected(self, app, client):
        _create_user(app)
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            valid_code = pyotp.TOTP(user.totp_secret).now()
        response = client.post("/login", data={
            "email": "admin@example.com",
            "password": "wrongpassword",
            "totp_code": valid_code,
        })
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_post_wrong_totp_rejected(self, app, client):
        _create_user(app)
        response = client.post("/login", data={
            "email": "admin@example.com",
            "password": "testpassword123",
            "totp_code": "000000",
        })
        assert response.status_code == 200
        assert b"Invalid" in response.data

    def test_post_unknown_email_rejected(self, app, client):
        _create_user(app)
        response = client.post("/login", data={
            "email": "nobody@example.com",
            "password": "testpassword123",
            "totp_code": "000000",
        })
        assert response.status_code == 200
        assert b"Invalid" in response.data


# ── Logout ────────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_clears_session_and_redirects(self, app, client):
        _create_user(app)
        with app.app_context():
            uid = User.query.filter_by(email="admin@example.com").first().id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        response = client.get("/logout")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
        with client.session_transaction() as sess:
            assert "user_id" not in sess


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
        response = client.post("/setup", data={
            "step": "account",
            "email": "admin@example.com",
            "password": "short",
            "confirm_password": "short",
        })
        assert response.status_code == 200
        assert b"12 characters" in response.data

    def test_step1_validation_rejects_mismatched_passwords(self, client):
        response = client.post("/setup", data={
            "step": "account",
            "email": "admin@example.com",
            "password": "validpassword123",
            "confirm_password": "differentpassword123",
        })
        assert response.status_code == 200
        assert b"do not match" in response.data

    def test_step1_valid_redirects_to_step2(self, client):
        response = client.post("/setup", data={
            "step": "account",
            "email": "admin@example.com",
            "password": "validpassword123",
            "confirm_password": "validpassword123",
        })
        assert response.status_code == 302
        assert "step=totp" in response.headers["Location"]

    def test_step2_invalid_totp_shows_error(self, client):
        # Complete step 1 first to populate the session
        client.post("/setup", data={
            "step": "account",
            "email": "admin@example.com",
            "password": "validpassword123",
            "confirm_password": "validpassword123",
        })
        response = client.post("/setup", data={
            "step": "totp",
            "totp_code": "000000",
        })
        assert response.status_code == 200
        assert b"Invalid code" in response.data

    def test_full_setup_creates_user_and_redirects_to_login(self, app, client):
        # Step 1
        client.post("/setup", data={
            "step": "account",
            "email": "admin@example.com",
            "password": "validpassword123",
            "confirm_password": "validpassword123",
        })

        # Retrieve the TOTP secret from the session to generate a valid code
        with client.session_transaction() as sess:
            totp_secret = sess["setup_totp_secret"]

        valid_code = pyotp.TOTP(totp_secret).now()

        response = client.post("/setup", data={
            "step": "totp",
            "totp_code": valid_code,
        })
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

        with app.app_context():
            assert User.query.filter_by(email="admin@example.com").count() == 1
            assert Tenant.query.count() == 1
