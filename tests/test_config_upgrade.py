"""
Tests for the one-click upgrade feature in app/config/routes.py:
  - index() upgrade_active context flag (line 312)
  - trigger_upgrade() POST route (lines 544-568)
  - upgrade_status() GET route (lines 573-601)
"""

import json
import os
import tempfile
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _setup_admin(app, is_instance_admin=True):
    """Create a tenant ADMIN; instance-admin by default since trigger-upgrade
    and upgrade-status are instance-admin-only (N-27)."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="admin@upgrade.test",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
            is_instance_admin=is_instance_admin,
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


# ── index() upgrade_active flag ───────────────────────────────────────────────


class TestUpgradeActiveFlag:
    def test_upgrade_active_false_when_dir_empty(self, app, client, captured_templates):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/")
        assert resp.status_code == 200
        ctx = captured_templates[-1][1]
        assert ctx["upgrade_active"] is False
        assert ctx["upgrade_dir_enabled"] is True

    def test_upgrade_active_true_when_trigger_exists(
        self, app, client, captured_templates
    ):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/")
        assert resp.status_code == 200
        ctx = captured_templates[-1][1]
        assert ctx["upgrade_active"] is True

    def test_upgrade_active_true_when_running_exists(
        self, app, client, captured_templates
    ):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger.running"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/")
        assert resp.status_code == 200
        ctx = captured_templates[-1][1]
        assert ctx["upgrade_active"] is True


# ── trigger_upgrade() ─────────────────────────────────────────────────────────


class TestTriggerUpgrade:
    def test_requires_login(self, app, client):
        resp = client.post("/config/trigger-upgrade", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_forbidden_for_non_instance_admin(self, app, client):
        """Tenant ADMIN without instance-admin flag cannot trigger a global upgrade (N-27)."""
        uid = _setup_admin(app, is_instance_admin=False)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.post("/config/trigger-upgrade")
        assert resp.status_code == 403

    def test_returns_404_when_upgrade_dir_not_set(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENHANGAR_UPGRADE_DIR", None)
            resp = client.post("/config/trigger-upgrade")
        assert resp.status_code == 404

    def test_flashes_warning_when_already_running(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger.running"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.post("/config/trigger-upgrade")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/config/")
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("already in progress" in msg for _, msg in flashes)

    def test_flashes_info_when_already_triggered(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.post("/config/trigger-upgrade")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("already triggered" in msg for _, msg in flashes)

    def test_creates_trigger_file_on_happy_path(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.post("/config/trigger-upgrade")
            trigger_path = os.path.join(tmpdir, "trigger")
            assert resp.status_code == 302
            assert os.path.exists(trigger_path)
            with open(trigger_path) as fh:
                data = json.load(fh)
        assert data["triggered_by"] == "admin@upgrade.test"
        assert "triggered_at" in data

    def test_flashes_success_on_happy_path(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                client.post("/config/trigger-upgrade")
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("restart shortly" in msg for _, msg in flashes)


# ── upgrade_status() ──────────────────────────────────────────────────────────


class TestUpgradeStatus:
    def test_requires_login(self, app, client):
        resp = client.get("/config/upgrade-status", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_forbidden_for_non_instance_admin(self, app, client):
        """Tenant ADMIN without instance-admin flag cannot poll upgrade status (N-27)."""
        uid = _setup_admin(app, is_instance_admin=False)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
        assert resp.status_code == 403

    def test_returns_404_when_upgrade_dir_not_set(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENHANGAR_UPGRADE_DIR", None)
            resp = client.get("/config/upgrade-status")
        assert resp.status_code == 404

    def test_done_returns_status_and_removes_file(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            done_path = os.path.join(tmpdir, "trigger.done")
            open(done_path, "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
            assert resp.status_code == 200
            assert resp.get_json() == {"status": "done"}
            assert not os.path.exists(done_path)

    def test_failed_returns_status_message_and_removes_file(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            failed_path = os.path.join(tmpdir, "trigger.failed")
            with open(failed_path, "w") as fh:
                fh.write("docker pull failed")
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "failed"
            assert data["message"] == "docker pull failed"
            assert not os.path.exists(failed_path)

    def test_running_returns_in_progress(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger.running"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "in-progress"}

    def test_trigger_returns_triggered(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "trigger"), "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "triggered"}

    def test_no_files_returns_idle(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                resp = client.get("/config/upgrade-status")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "idle"}

    def test_done_oserror_on_remove_still_returns_done(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            done_path = os.path.join(tmpdir, "trigger.done")
            open(done_path, "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                with patch("os.remove", side_effect=OSError("permission denied")):
                    resp = client.get("/config/upgrade-status")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "done"}

    def test_failed_oserror_on_read_returns_empty_message(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with tempfile.TemporaryDirectory() as tmpdir:
            failed_path = os.path.join(tmpdir, "trigger.failed")
            open(failed_path, "w").close()
            with patch.dict("os.environ", {"OPENHANGAR_UPGRADE_DIR": tmpdir}):
                with patch("builtins.open", side_effect=OSError("permission denied")):
                    resp = client.get("/config/upgrade-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "failed"
        assert data["message"] == ""
