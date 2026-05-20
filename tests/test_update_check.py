"""
Tests for the daily version-check feature (AppSetting, _fetch_latest_version,
_run_version_check, _version_check_loop, _start_version_check_thread, and the
config/settings page version display).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from init import create_app  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    AppSetting,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _setup_admin(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="admin@test.com",
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
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


# ── _fetch_latest_version ─────────────────────────────────────────────────────


class TestFetchLatestVersion:
    def test_returns_version_without_v_prefix(self):
        from init import _fetch_latest_version

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"tag_name": "v0.16.0"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_version()
        assert result == "0.16.0"

    def test_returns_version_already_without_prefix(self):
        from init import _fetch_latest_version

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"tag_name": "0.16.0"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_version()
        assert result == "0.16.0"

    def test_returns_none_when_tag_name_empty(self):
        from init import _fetch_latest_version

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"tag_name": ""}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_latest_version()
        assert result is None

    def test_returns_none_on_network_error(self):
        from init import _fetch_latest_version

        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            result = _fetch_latest_version()
        assert result is None


# ── _upsert_app_setting ───────────────────────────────────────────────────────


class TestUpsertAppSetting:
    def test_inserts_new_key(self, app):
        from init import _upsert_app_setting

        with app.app_context():
            _upsert_app_setting(db.session, "test_key", "test_value")
            db.session.commit()
            setting = db.session.get(AppSetting, "test_key")
        assert setting.value == "test_value"

    def test_updates_existing_key(self, app):
        from init import _upsert_app_setting

        with app.app_context():
            db.session.add(AppSetting(key="test_key", value="old"))
            db.session.commit()
            _upsert_app_setting(db.session, "test_key", "new")
            db.session.commit()
            setting = db.session.get(AppSetting, "test_key")
        assert setting.value == "new"


# ── _run_version_check ────────────────────────────────────────────────────────


class TestRunVersionCheck:
    def test_fetches_and_stores_version_on_first_run(self, app):
        from init import _run_version_check

        with patch("init._fetch_latest_version", return_value="0.16.0"):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"

    def test_stores_last_checked_timestamp(self, app):
        from init import _run_version_check

        before = datetime.now(timezone.utc)
        with patch("init._fetch_latest_version", return_value="0.16.0"):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "version_last_checked_at")
        assert setting is not None
        checked_at = datetime.fromisoformat(setting.value)
        assert checked_at >= before

    def test_skips_when_checked_recently(self, app):
        from init import _run_version_check

        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with app.app_context():
            db.session.add(AppSetting(key="version_last_checked_at", value=recent))
            db.session.commit()
        with patch("init._fetch_latest_version") as mock_fetch:
            _run_version_check(app)
            mock_fetch.assert_not_called()

    def test_reruns_when_checked_long_ago(self, app):
        from init import _run_version_check

        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with app.app_context():
            db.session.add(AppSetting(key="version_last_checked_at", value=old))
            db.session.commit()
        with patch("init._fetch_latest_version", return_value="0.17.0"):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.17.0"

    def test_handles_malformed_timestamp(self, app):
        from init import _run_version_check

        with app.app_context():
            db.session.add(
                AppSetting(key="version_last_checked_at", value="not-a-datetime")
            )
            db.session.commit()
        with patch("init._fetch_latest_version", return_value="0.16.0"):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"

    def test_does_not_store_latest_version_when_fetch_fails(self, app):
        from init import _run_version_check

        with patch("init._fetch_latest_version", return_value=None):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting is None

    def test_updates_existing_latest_version(self, app):
        from init import _run_version_check

        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.15.0"))
            db.session.commit()
        with patch("init._fetch_latest_version", return_value="0.16.0"):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"


# ── _version_check_loop ───────────────────────────────────────────────────────


class TestVersionCheckLoop:
    def test_initial_sleep_then_check_then_24h_sleep(self, app):
        from init import _version_check_loop

        sleep_calls = []

        def fake_sleep(n):
            sleep_calls.append(n)
            if len(sleep_calls) >= 2:
                raise SystemExit()

        with patch("init._run_version_check"):
            with pytest.raises(SystemExit):
                _version_check_loop(app, _sleep_fn=fake_sleep)
        assert sleep_calls[1] == 24 * 3600

    def test_loop_catches_check_exception(self, app):
        from init import _version_check_loop

        call_count = [0]

        def fake_sleep(n):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise SystemExit()

        with patch("init._run_version_check", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                _version_check_loop(app, _sleep_fn=fake_sleep)


# ── _start_version_check_thread ───────────────────────────────────────────────


class TestStartVersionCheckThread:
    def test_starts_daemon_thread(self, app):
        from init import _start_version_check_thread, _version_check_loop

        with patch("threading.Thread") as MockThread:
            mock_t = MagicMock()
            MockThread.return_value = mock_t
            _start_version_check_thread(app)
        MockThread.assert_called_once_with(
            target=_version_check_loop,
            args=(app,),
            daemon=True,
            name="version-check",
        )
        mock_t.start.assert_called_once()


# ── Config page version display ───────────────────────────────────────────────


class TestConfigVersionDisplay:
    def test_shows_current_version(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"0.15.0" in resp.data

    def test_shows_update_available_badge(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"0.16.0" in resp.data
        assert b"Update available" in resp.data
        assert b"How to upgrade" in resp.data

    def test_shows_up_to_date_badge(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.15.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"Up to date" in resp.data
        assert b"Update available" not in resp.data

    def test_no_badge_when_no_version_check_yet(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"Update available" not in resp.data
        assert b"Up to date" not in resp.data

    def test_no_update_badge_for_development_version(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "development"}):
            resp = client.get("/config/")
        assert b"Update available" not in resp.data

    def test_thread_not_started_with_sqlite(self):
        # SQLite URI (dev/test) must never start the background thread.
        with patch("init._start_version_check_thread") as mock_start:
            create_app()
            mock_start.assert_not_called()

    def test_thread_started_with_postgres(self):
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://u:p@h/db"}):
            with patch("init._start_version_check_thread") as mock_start:
                create_app()
                mock_start.assert_called_once()
