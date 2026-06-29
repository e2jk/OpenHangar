"""
Tests for the daily version-check feature (AppSetting, fetch_versions,
fetch_latest_version, run_version_check, version_check_loop,
start_version_check_thread, and the config/settings page version display).
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


# ── fetch_versions ────────────────────────────────────────────────────────────


def _mock_opener(
    response_body: bytes | None = None, side_effect: Exception | None = None
) -> MagicMock:
    """Build a mock urllib opener whose .open() returns a fake response or raises."""
    mock_resp = MagicMock()
    if response_body is not None:
        mock_resp.read.return_value = response_body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    mock_opener = MagicMock()
    if side_effect is not None:
        mock_opener.open.side_effect = side_effect
    else:
        mock_opener.open.return_value = mock_resp
    return mock_opener


class TestFetchVersions:
    def test_returns_versions_list(self):
        from services.version_service import (
            fetch_versions as _fetch_versions,
        )  # pyright: ignore[reportMissingImports]

        opener = _mock_opener(json.dumps(["0.17.0", "0.16.0", "0.15.0"]).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            result = _fetch_versions()
        assert result == ["0.17.0", "0.16.0", "0.15.0"]

    def test_returns_empty_for_non_list_response(self):
        from services.version_service import (
            fetch_versions as _fetch_versions,
        )  # pyright: ignore[reportMissingImports]

        opener = _mock_opener(json.dumps({"tag_name": "v0.16.0"}).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            result = _fetch_versions()
        assert result == []

    def test_returns_empty_on_empty_array(self):
        from services.version_service import (
            fetch_versions as _fetch_versions,
        )  # pyright: ignore[reportMissingImports]

        opener = _mock_opener(json.dumps([]).encode())
        with patch("urllib.request.build_opener", return_value=opener):
            result = _fetch_versions()
        assert result == []

    def test_returns_empty_on_network_error(self):
        from services.version_service import (
            fetch_versions as _fetch_versions,
        )  # pyright: ignore[reportMissingImports]

        opener = _mock_opener(side_effect=OSError("network error"))
        with patch("urllib.request.build_opener", return_value=opener):
            result = _fetch_versions()
        assert result == []

    def test_strict_redirect_blocks_off_domain(self):
        # Covers version_service.py — _StrictRedirect raises URLError for
        # redirects to a host other than e2jk.github.io.
        import urllib.error
        import urllib.request

        from services.version_service import (
            fetch_versions as _fetch_versions,
        )  # pyright: ignore[reportMissingImports]
        from services.version_service import _VERSION_CHECK_HOST  # pyright: ignore[reportMissingImports]

        # Capture the _StrictRedirect class passed to build_opener.
        captured_class = []

        def capturing_build_opener(handler_cls):
            captured_class.append(handler_cls)
            m = MagicMock()
            m.open.side_effect = OSError("no network")
            return m

        with patch("urllib.request.build_opener", side_effect=capturing_build_opener):
            _fetch_versions()

        assert captured_class, "build_opener was not called"
        strict = captured_class[0]()  # instantiate the captured class

        # Off-domain redirect must raise URLError.
        import pytest as _pytest

        with _pytest.raises(urllib.error.URLError, match="blocked"):
            strict.redirect_request(
                None, None, 301, "Moved", {}, "https://evil.com/path"
            )

        # Same-domain redirect must pass through (no exception).
        req = urllib.request.Request(f"https://{_VERSION_CHECK_HOST}/")
        strict.redirect_request(
            req,
            None,
            301,
            "Moved",
            {},
            f"https://{_VERSION_CHECK_HOST}/new-path",
        )


# ── fetch_latest_version ──────────────────────────────────────────────────────


class TestFetchLatestVersion:
    def test_returns_first_version_from_list(self):
        from services.version_service import (
            fetch_latest_version as _fetch_latest_version,
        )  # pyright: ignore[reportMissingImports]

        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.17.0", "0.16.0"],
        ):
            result = _fetch_latest_version()
        assert result == "0.17.0"

    def test_returns_none_when_list_empty(self):
        from services.version_service import (
            fetch_latest_version as _fetch_latest_version,
        )  # pyright: ignore[reportMissingImports]

        with patch("services.version_service.fetch_versions", return_value=[]):
            result = _fetch_latest_version()
        assert result is None


# ── _upsert_app_setting ───────────────────────────────────────────────────────


class TestUpsertAppSetting:
    def test_inserts_new_key(self, app):
        from services.version_service import upsert_app_setting as _upsert_app_setting  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _upsert_app_setting(db.session, "test_key", "test_value")
            db.session.commit()
            setting = db.session.get(AppSetting, "test_key")
        assert setting.value == "test_value"

    def test_updates_existing_key(self, app):
        from services.version_service import upsert_app_setting as _upsert_app_setting  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(AppSetting(key="test_key", value="old"))
            db.session.commit()
            _upsert_app_setting(db.session, "test_key", "new")
            db.session.commit()
            setting = db.session.get(AppSetting, "test_key")
        assert setting.value == "new"


# ── _persist_update_flag ─────────────────────────────────────────────────────


class TestPersistUpdateFlag:
    def test_writes_true_when_newer_version_available(self, app):
        from services.version_service import _persist_update_flag as _persist  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _persist(db.session, "0.15.0", "0.16.0")
            db.session.commit()
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "true"

    def test_writes_false_when_up_to_date(self, app):
        from services.version_service import _persist_update_flag as _persist  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _persist(db.session, "0.16.0", "0.16.0")
            db.session.commit()
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "false"

    def test_writes_false_for_development_build(self, app):
        from services.version_service import _persist_update_flag as _persist  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _persist(db.session, "development", "0.16.0")
            db.session.commit()
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "false"

    def test_writes_false_on_malformed_version(self, app):
        from services.version_service import _persist_update_flag as _persist  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _persist(db.session, "0.15.0", "not-a-version")
            db.session.commit()
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "false"


# ── startup_recompute_update_flag ─────────────────────────────────────────────


class TestStartupRecomputeUpdateFlag:
    def test_sets_true_when_newer_version_stored(self, app):
        from services.version_service import startup_recompute_update_flag as _recompute  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            _recompute(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "true"

    def test_sets_false_when_no_latest_version_stored(self, app):
        from services.version_service import startup_recompute_update_flag as _recompute  # pyright: ignore[reportMissingImports]

        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            _recompute(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "false"

    def test_sets_false_after_upgrade_to_latest(self, app):
        from services.version_service import startup_recompute_update_flag as _recompute  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.16.0"}):
            _recompute(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "update_available")
        assert setting.value == "false"


# ── _run_version_check ────────────────────────────────────────────────────────


class TestRunVersionCheck:
    def test_fetches_and_stores_version_on_first_run(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0", "0.15.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"

    def test_stores_all_versions(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0", "0.15.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "all_versions")
        assert json.loads(setting.value) == ["0.16.0", "0.15.0"]

    def test_stores_last_checked_timestamp(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        before = datetime.now(timezone.utc)
        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "version_last_checked_at")
        assert setting is not None
        checked_at = datetime.fromisoformat(setting.value)
        assert checked_at >= before

    def test_skips_when_checked_recently(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with app.app_context():
            db.session.add(AppSetting(key="version_last_checked_at", value=recent))
            db.session.commit()
        with patch("services.version_service.fetch_versions") as mock_fetch:
            _run_version_check(app)
            mock_fetch.assert_not_called()

    def test_reruns_when_checked_long_ago(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with app.app_context():
            db.session.add(AppSetting(key="version_last_checked_at", value=old))
            db.session.commit()
        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.17.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.17.0"

    def test_handles_malformed_timestamp(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(
                AppSetting(key="version_last_checked_at", value="not-a-datetime")
            )
            db.session.commit()
        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"

    def test_does_not_store_latest_version_when_fetch_fails(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        with patch("services.version_service.fetch_versions", return_value=[]):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting is None

    def test_updates_existing_latest_version(self, app):
        from services.version_service import run_version_check as _run_version_check  # pyright: ignore[reportMissingImports]

        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.15.0"))
            db.session.commit()
        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0", "0.15.0"],
        ):
            _run_version_check(app)
        with app.app_context():
            setting = db.session.get(AppSetting, "latest_version")
        assert setting.value == "0.16.0"


# ── _version_check_loop ───────────────────────────────────────────────────────


class TestVersionCheckLoop:
    def test_initial_sleep_then_check_then_24h_sleep(self, app):
        from services.version_service import version_check_loop as _version_check_loop  # pyright: ignore[reportMissingImports]

        sleep_calls = []

        def fake_sleep(n):
            sleep_calls.append(n)
            if len(sleep_calls) >= 2:
                raise SystemExit()

        with patch("services.version_service.run_version_check"):
            with pytest.raises(SystemExit):
                _version_check_loop(app, _sleep_fn=fake_sleep)
        assert sleep_calls[1] == 24 * 3600

    def test_loop_catches_check_exception(self, app):
        from services.version_service import version_check_loop as _version_check_loop  # pyright: ignore[reportMissingImports]

        call_count = [0]

        def fake_sleep(n):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise SystemExit()

        with patch(
            "services.version_service.run_version_check",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(SystemExit):
                _version_check_loop(app, _sleep_fn=fake_sleep)


# ── _start_version_check_thread ───────────────────────────────────────────────


class TestStartVersionCheckThread:
    def test_starts_daemon_thread(self, app):
        from services.version_service import (
            start_version_check_thread as _start_version_check_thread,
            startup_recompute_update_flag as _startup_recompute,
            version_check_loop as _version_check_loop,
        )  # pyright: ignore[reportMissingImports]

        with patch("threading.Thread") as MockThread:
            mock_t = MagicMock()
            MockThread.return_value = mock_t
            _start_version_check_thread(app)
        assert MockThread.call_count == 2
        MockThread.assert_any_call(
            target=_startup_recompute,
            args=(app,),
            daemon=True,
            name="version-flag-startup",
        )
        MockThread.assert_any_call(
            target=_version_check_loop,
            args=(app,),
            daemon=True,
            name="version-check",
        )
        assert mock_t.start.call_count == 2


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

    def test_shows_versions_behind_count(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.17.0"))
            db.session.add(
                AppSetting(
                    key="all_versions",
                    value=json.dumps(["0.17.0", "0.16.0", "0.15.0"]),
                )
            )
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"2 versions behind" in resp.data

    def test_no_versions_behind_when_one_behind(self, app, client):
        # "1 version behind" uses singular form
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.add(
                AppSetting(
                    key="all_versions",
                    value=json.dumps(["0.16.0", "0.15.0"]),
                )
            )
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"1 version behind" in resp.data

    def test_no_versions_behind_when_current_not_in_list(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.add(
                AppSetting(
                    key="all_versions",
                    value=json.dumps(["0.16.0"]),
                )
            )
            db.session.commit()
        # 0.15.0 not in list — no "behind" count shown
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"Update available" in resp.data
        assert b"behind" not in resp.data

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

    def test_no_update_badge_when_latest_is_older(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.14.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"Update available" not in resp.data
        assert b"Up to date" in resp.data

    def test_no_update_badge_when_version_malformed(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="not-a-version"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert b"Update available" not in resp.data

    def test_no_update_badge_for_development_version(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "development"}):
            resp = client.get("/config/")
        assert b"Update available" not in resp.data

    def test_no_versions_behind_when_all_versions_invalid_json(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(AppSetting(key="latest_version", value="0.16.0"))
            db.session.add(AppSetting(key="all_versions", value="not-valid-json"))
            db.session.commit()
        with patch.dict("os.environ", {"OPENHANGAR_VERSION": "0.15.0"}):
            resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"version behind" not in resp.data

    def test_refresh_button_present(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        resp = client.get("/config/")
        assert b"check-version" in resp.data

    def test_check_version_route_refreshes_and_redirects(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch(
            "services.version_service.fetch_versions",
            return_value=["0.16.0", "0.15.0"],
        ):
            resp = client.post("/config/check-version")
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(AppSetting, "latest_version").value == "0.16.0"
            assert db.session.get(AppSetting, "version_last_checked_at") is not None
            assert json.loads(db.session.get(AppSetting, "all_versions").value) == [
                "0.16.0",
                "0.15.0",
            ]

    def test_check_version_requires_login(self, app, client):
        resp = client.post("/config/check-version")
        assert resp.status_code == 403

    def test_thread_not_started_with_sqlite(self):
        # SQLite URI (dev/test) must never start the background thread.
        with patch("services.version_service.start_version_check_thread") as mock_start:
            create_app()
            mock_start.assert_not_called()

    def test_thread_started_with_postgres(self):
        with patch.dict(
            "os.environ", {"OPENHANGAR_DATABASE_URL": "postgresql://u:p@h/db"}
        ):
            with patch(
                "services.version_service.start_version_check_thread"
            ) as mock_start:
                create_app()
                mock_start.assert_called_once()


# ── Config page system info ────────────────────────────────────────────────────


class TestConfigSystemInfo:
    def test_db_size_shown_when_query_succeeds(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch.object(db.session, "execute") as mock_exec:
            mock_exec.return_value.scalar.return_value = "8192 bytes"
            resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"8192 bytes" in resp.data

    def test_upload_size_exception_is_swallowed(self, app, client):
        uid = _setup_admin(app)
        _login(client, uid)
        with patch("os.scandir", side_effect=OSError("permission denied")):
            resp = client.get("/config/")
        assert resp.status_code == 200
