"""
Tests for the "Environment" / "Service worker" technical info rows on the
config/settings page (System section) — surfaces app.debug, OPENHANGAR_ENV,
and whether the PWA service worker is registered server-side.
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


def _setup_admin(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="admin-tech-info@test.com",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


class TestTechnicalInfoPanel:
    def test_debug_off_shows_service_worker_enabled(self, app, client, monkeypatch):
        uid = _setup_admin(app)
        _login(client, uid)
        monkeypatch.setattr(app, "debug", False)
        resp = client.get("/config/")
        assert b"Debug: off" in resp.data
        assert b"Disabled in dev mode" not in resp.data

    def test_debug_on_shows_service_worker_disabled_with_hint(
        self, app, client, monkeypatch
    ):
        uid = _setup_admin(app)
        _login(client, uid)
        monkeypatch.setattr(app, "debug", True)
        monkeypatch.delenv("OPENHANGAR_SW_ENABLED", raising=False)
        resp = client.get("/config/")
        assert b"Debug: on" in resp.data
        assert b"Disabled in dev mode" in resp.data
        assert b"OPENHANGAR_SW_ENABLED" in resp.data

    def test_debug_on_but_sw_forced_on_shows_enabled(self, app, client, monkeypatch):
        uid = _setup_admin(app)
        _login(client, uid)
        monkeypatch.setattr(app, "debug", True)
        monkeypatch.setenv("OPENHANGAR_SW_ENABLED", "true")
        resp = client.get("/config/")
        assert b"Debug: on" in resp.data
        assert b"Disabled in dev mode" not in resp.data

    def test_environment_label_development(self, app, client, monkeypatch):
        uid = _setup_admin(app)
        _login(client, uid)
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        resp = client.get("/config/")
        assert "Development".encode() in resp.data

    def test_environment_label_test(self, app, client, monkeypatch):
        uid = _setup_admin(app)
        _login(client, uid)
        monkeypatch.setenv("OPENHANGAR_ENV", "test")
        resp = client.get("/config/")
        assert b'text-muted">\n        Test\n' in resp.data
