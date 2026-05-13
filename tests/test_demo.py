"""
Tests for Phase 6 — demo mode.

Covers:
  - demo/routes.py   : /demo/enter slot assignment and restoration
  - init.py          : demo index override and context processor extras
  - auth/routes.py   : setup blocked and demo_slot_id preserved on logout
  - models.py        : DemoSlot model
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from flask import template_rendered  # pyright: ignore[reportMissingImports]

from init import create_app  # pyright: ignore[reportMissingImports]
from models import DemoSlot, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def demo_app():
    """App fixture with FLASK_ENV=demo."""
    old = os.environ.get("FLASK_ENV")
    os.environ["FLASK_ENV"] = "demo"
    try:
        app = create_app()
        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        with app.app_context():
            db.create_all()
        yield app
        with app.app_context():
            db.drop_all()
    finally:
        if old is None:
            os.environ.pop("FLASK_ENV", None)
        else:
            os.environ["FLASK_ENV"] = old


@pytest.fixture()
def demo_client(demo_app):
    return demo_app.test_client()


@pytest.fixture()
def demo_captured_templates(demo_app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, demo_app)
    yield recorded
    template_rendered.disconnect(record, demo_app)


def _make_demo_slot(app, slot_id=1, last_activity=None):
    """Create a tenant, user, and DemoSlot in the test DB."""
    with app.app_context():
        tenant = Tenant(name=f"Demo Hangar #{slot_id}")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=f"demo-{slot_id}@openhangar.demo",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            totp_secret=None,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
        slot = DemoSlot(id=slot_id, tenant_id=tenant.id, user_id=user.id,
                        last_activity_at=last_activity)
        db.session.add(slot)
        db.session.commit()
        return slot.id, user.id


# ── DemoSlot model ────────────────────────────────────────────────────────────

class TestDemoSlotModel:
    def test_create_slot(self, demo_app):
        with demo_app.app_context():
            slot_id, user_id = _make_demo_slot(demo_app)
            slot = db.session.get(DemoSlot, slot_id)
            assert slot is not None
            assert slot.user_id == user_id

    def test_slot_last_activity_nullable(self, demo_app):
        with demo_app.app_context():
            slot_id, _ = _make_demo_slot(demo_app)
            slot = db.session.get(DemoSlot, slot_id)
            assert slot.last_activity_at is None

    def test_slot_last_activity_set(self, demo_app):
        ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        with demo_app.app_context():
            slot_id, _ = _make_demo_slot(demo_app, last_activity=ts)
            slot = db.session.get(DemoSlot, slot_id)
            assert slot.last_activity_at is not None


# ── /demo/enter — slot assignment ─────────────────────────────────────────────

class TestDemoEnter:
    def test_enter_assigns_slot_and_redirects_to_dashboard(self, demo_app, demo_client):
        _make_demo_slot(demo_app, slot_id=1)
        response = demo_client.post("/demo/enter")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/") or "/" in response.headers["Location"]

    def test_enter_sets_user_id_in_session(self, demo_app, demo_client):
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess.get("user_id") == user_id

    def test_enter_sets_demo_slot_id_in_session(self, demo_app, demo_client):
        _make_demo_slot(demo_app, slot_id=1)
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess.get("demo_slot_id") == 1

    def test_enter_touches_last_activity(self, demo_app, demo_client):
        _make_demo_slot(demo_app, slot_id=1)
        demo_client.post("/demo/enter")
        with demo_app.app_context():
            slot = db.session.get(DemoSlot, 1)
            assert slot.last_activity_at is not None

    def test_enter_restores_existing_slot(self, demo_app, demo_client):
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        # First enter — gets slot 1
        demo_client.post("/demo/enter")
        # Log out (clears user_id, preserves demo_slot_id)
        demo_client.get("/logout")
        # Enter again — should restore same slot
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess.get("demo_slot_id") == 1
            assert sess.get("user_id") == user_id

    def test_enter_with_stale_slot_id_assigns_new_slot(self, demo_app, demo_client):
        _make_demo_slot(demo_app, slot_id=1)
        # Pre-load a non-existent slot_id into the session
        with demo_client.session_transaction() as sess:
            sess["demo_slot_id"] = 9999
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            # Falls back to slot 1 (the only existing one)
            assert sess.get("demo_slot_id") == 1

    def test_enter_with_no_slots_redirects_to_index(self, demo_app, demo_client):
        # No slots in DB at all
        response = demo_client.post("/demo/enter")
        assert response.status_code == 302

    def test_enter_prefers_least_recently_used_slot(self, demo_app, demo_client):
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        new_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        _make_demo_slot(demo_app, slot_id=1, last_activity=new_ts)
        _make_demo_slot(demo_app, slot_id=2, last_activity=old_ts)
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            # Slot 2 has older activity — should be chosen
            assert sess.get("demo_slot_id") == 2

    def test_enter_returns_503_when_all_slots_busy(self, demo_app, demo_client, monkeypatch):
        monkeypatch.setenv("DEMO_BUSY_WINDOW_MINUTES", "30")
        recent = datetime.utcnow() - timedelta(minutes=5)
        _make_demo_slot(demo_app, slot_id=1, last_activity=recent)
        response = demo_client.post("/demo/enter")
        assert response.status_code == 503

    def test_enter_503_invalid_env_var_falls_back_to_default(self, demo_app, demo_client, monkeypatch):
        monkeypatch.setenv("DEMO_BUSY_WINDOW_MINUTES", "not-a-number")
        recent = datetime.utcnow() - timedelta(minutes=5)
        _make_demo_slot(demo_app, slot_id=1, last_activity=recent)
        response = demo_client.post("/demo/enter")
        assert response.status_code == 503

    def test_enter_route_not_registered_in_normal_mode(self, app, client):
        """Outside demo mode, the demo blueprint is not registered — 404."""
        response = client.post("/demo/enter")
        assert response.status_code == 404


# ── demo_has_recent_activity ───────────────────────────────────────────────────

class TestDemoHasRecentActivity:
    def test_no_activity_returns_false(self, demo_app):
        _make_demo_slot(demo_app, slot_id=1, last_activity=None)
        with demo_app.app_context():
            from demo.routes import demo_has_recent_activity
            assert demo_has_recent_activity() is False

    def test_recent_activity_returns_true(self, demo_app):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        _make_demo_slot(demo_app, slot_id=1, last_activity=recent)
        with demo_app.app_context():
            from demo.routes import demo_has_recent_activity
            assert demo_has_recent_activity() is True

    def test_old_activity_returns_false(self, demo_app):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        _make_demo_slot(demo_app, slot_id=1, last_activity=old)
        with demo_app.app_context():
            from demo.routes import demo_has_recent_activity
            assert demo_has_recent_activity() is False


# ── Demo index: unauthenticated always sees landing page ──────────────────────

class TestDemoIndex:
    def test_unauthenticated_sees_landing_not_welcome(self, demo_app, demo_client):
        """In demo mode, even with users in DB, unauthenticated → landing page."""
        _make_demo_slot(demo_app, slot_id=1)
        response = demo_client.get("/")
        assert response.status_code == 200
        # Welcome page has "Welcome back"; landing page has "OpenHangar"
        assert b"Welcome back" not in response.data

    def test_demo_landing_has_try_demo_buttons(self, demo_app, demo_client):
        response = demo_client.get("/")
        assert b"Try as Owner" in response.data
        assert b"Try as Renter" in response.data

    def test_demo_landing_has_no_get_started_link(self, demo_app, demo_client):
        """The 'Get Started' label is replaced by 'Try as Owner'/'Try as Renter' in demo mode."""
        response = demo_client.get("/")
        assert b"Get Started" not in response.data

    def test_logged_in_sees_dashboard(self, demo_app, demo_client):
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        with demo_client.session_transaction() as sess:
            sess["user_id"] = user_id
        response = demo_client.get("/")
        assert response.status_code == 200
        assert b"Dashboard" in response.data


# ── Context processor extras in demo mode ────────────────────────────────────

class TestDemoContextProcessor:
    def test_is_demo_true(self, demo_app, demo_client, demo_captured_templates):
        demo_client.get("/")
        _, context = demo_captured_templates[0]
        assert context["is_demo"] is True

    def test_is_demo_false_in_normal_mode(self, app, client, captured_templates):
        client.get("/")
        _, context = captured_templates[0]
        assert context["is_demo"] is False

    def test_demo_next_wipe_utc_from_env(self, demo_app, demo_client, demo_captured_templates):
        old = os.environ.get("DEMO_NEXT_WIPE_UTC")
        os.environ["DEMO_NEXT_WIPE_UTC"] = "2099-01-01T00:00:00Z"
        try:
            demo_client.get("/")
            _, context = demo_captured_templates[0]
            assert context["demo_next_wipe_utc"] == "2099-01-01T00:00:00Z"
        finally:
            if old is None:
                os.environ.pop("DEMO_NEXT_WIPE_UTC", None)
            else:
                os.environ["DEMO_NEXT_WIPE_UTC"] = old

    def test_demo_next_wipe_utc_none_when_not_set(self, demo_app, demo_client, demo_captured_templates):
        os.environ.pop("DEMO_NEXT_WIPE_UTC", None)
        demo_client.get("/")
        _, context = demo_captured_templates[0]
        assert context["demo_next_wipe_utc"] is None

    def test_demo_site_url_from_env(self, app, client, captured_templates):
        old = os.environ.get("DEMO_SITE_URL")
        os.environ["DEMO_SITE_URL"] = "https://demo.openhangar.aero"
        try:
            client.get("/")
            _, context = captured_templates[0]
            assert context["demo_site_url"] == "https://demo.openhangar.aero"
        finally:
            if old is None:
                os.environ.pop("DEMO_SITE_URL", None)
            else:
                os.environ["DEMO_SITE_URL"] = old

    def test_demo_site_url_none_when_not_set(self, app, client, captured_templates):
        os.environ.pop("DEMO_SITE_URL", None)
        client.get("/")
        _, context = captured_templates[0]
        assert context["demo_site_url"] is None


# ── Landing page DEMO_SITE_URL button logic ───────────────────────────────────

class TestLandingDemoSiteUrl:
    def test_get_started_shown_without_demo_site_url(self, client):
        os.environ.pop("DEMO_SITE_URL", None)
        assert b"Get Started" in client.get("/").data

    def test_try_demo_link_shown_with_demo_site_url(self, app, client):
        old = os.environ.get("DEMO_SITE_URL")
        os.environ["DEMO_SITE_URL"] = "https://demo.openhangar.aero"
        try:
            data = client.get("/").data
            assert b"Try as Owner" in data
            assert b"Try as Renter" in data
        finally:
            if old is None:
                os.environ.pop("DEMO_SITE_URL", None)
            else:
                os.environ["DEMO_SITE_URL"] = old


# ── Auth: setup blocked in demo mode ─────────────────────────────────────────

class TestDemoSetupBlocked:
    def test_get_setup_redirects_in_demo_mode(self, demo_client):
        response = demo_client.get("/setup")
        assert response.status_code == 302

    def test_post_setup_redirects_in_demo_mode(self, demo_client):
        response = demo_client.post("/setup", data={
            "step": "account",
            "email": "hacker@example.com",
            "password": "validpassword123",
        })
        assert response.status_code == 302

    def test_setup_flash_message_in_demo_mode(self, demo_app, demo_client):
        demo_client.get("/setup", follow_redirects=True)
        with demo_app.test_request_context():
            pass  # flash is checked via response content
        response = demo_client.get("/setup", follow_redirects=True)
        assert b"demo" in response.data.lower()


# ── Auth: logout preserves demo_slot_id ──────────────────────────────────────

class TestDemoLogout:
    def test_logout_preserves_demo_slot_id(self, demo_app, demo_client):
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        with demo_client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["demo_slot_id"] = 1
        demo_client.get("/logout")
        with demo_client.session_transaction() as sess:
            assert "user_id" not in sess
            assert sess.get("demo_slot_id") == 1

    def test_logout_without_demo_slot_does_not_set_slot(self, app, client):
        """Normal logout (no demo_slot_id) leaves demo_slot_id absent."""
        from tests.test_routes import _create_user, _login_session  # pyright: ignore[reportMissingImports]
        _create_user(app)
        _login_session(app, client)
        client.get("/logout")
        with client.session_transaction() as sess:
            assert "demo_slot_id" not in sess


# ── Language handling in demo mode ───────────────────────────────────────────

class TestDemoLanguage:
    def test_enter_stores_accept_language_in_session(self, demo_app, demo_client):
        """Accept-Language header is captured into the session on demo entry."""
        _make_demo_slot(demo_app, slot_id=1)
        demo_client.post("/demo/enter", headers={"Accept-Language": "fr"})
        with demo_client.session_transaction() as sess:
            assert sess.get("language") == "fr"

    def test_enter_stores_manual_language_in_session(self, demo_app, demo_client):
        """A language chosen on the landing page is preserved through demo entry."""
        _make_demo_slot(demo_app, slot_id=1)
        # Simulate visitor manually switching to Dutch before entering the demo
        with demo_client.session_transaction() as sess:
            sess["language"] = "nl"
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess.get("language") == "nl"

    def test_enter_falls_back_to_english_without_accept_language(self, demo_app, demo_client):
        """With no language signal, session language defaults to 'en'."""
        _make_demo_slot(demo_app, slot_id=1)
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess.get("language") == "en"

    def test_locale_uses_session_over_user_language_in_demo(self, demo_app, demo_client, demo_captured_templates):
        """After demo entry, current_locale matches the visitor's locale, not the
        demo user's stored 'en' default."""
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        # Inject French as the visitor language then enter the demo
        with demo_client.session_transaction() as sess:
            sess["language"] = "fr"
        demo_client.post("/demo/enter")
        demo_client.get("/")
        assert any(ctx.get("current_locale") == "fr" for _, ctx in demo_captured_templates)

    def test_set_language_updates_session_not_user_in_demo(self, demo_app, demo_client):
        """set_language() writes to session (not user.language) for demo sessions."""
        _, user_id = _make_demo_slot(demo_app, slot_id=1)
        with demo_client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["demo_slot_id"] = 1
        demo_client.get("/set-language/nl")
        # Session must have the new language
        with demo_client.session_transaction() as sess:
            assert sess.get("language") == "nl"
        # User record must NOT have been mutated
        with demo_app.app_context():
            user = db.session.get(User, user_id)
            assert user.language != "nl"


# ── Context processor: demo_display_id ───────────────────────────────────────

class TestDemoDisplayId:
    def test_display_id_injected_when_slot_in_session(self, demo_app, demo_client):
        with demo_app.app_context():
            from models import DemoSlot, Tenant, TenantUser, User, Role, db
            import bcrypt
            tenant = Tenant(name="Demo Hangar #4242")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email="demo-disp@openhangar.demo",
                password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
            slot = DemoSlot(id=99, display_id=4242, tenant_id=tenant.id, user_id=user.id)
            db.session.add(slot)
            db.session.commit()

        with demo_client.session_transaction() as sess:
            sess["demo_slot_id"] = 99

        rv = demo_client.get("/")
        assert b"4242" in rv.data
