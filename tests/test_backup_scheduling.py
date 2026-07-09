"""
Tests for built-in backup scheduling and retention:
  - OPENHANGAR_BACKUP_TIME / OPENHANGAR_BACKUP_KEEP parsing + startup validation
  - retention pruning (count-based, success-only, failed rows untouched,
    stranded files keep their record)
  - the scheduled run (lock skip, backup failure skips pruning)
  - the daily loop and scheduler thread start
  - Configuration page schedule status and staleness warning
"""

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest  # pyright: ignore[reportMissingImports]
import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    BackupRecord,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from services.backup_scheduler import (  # pyright: ignore[reportMissingImports]
    _backup_daily_loop,
    parse_backup_keep,
    parse_backup_time,
    prune_old_backups,
    run_scheduled_backup,
    start_backup_scheduler,
)


def _login_admin(app, client, email="admin@backup.test"):
    with app.app_context():
        tenant = Tenant(name="Backup Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_record(app, tmp_path, name, status="ok", age_days=0, with_file=True):
    with app.app_context():
        path = str(tmp_path / name)
        if with_file:
            (tmp_path / name).write_bytes(b"backup-bytes")
        record = BackupRecord(
            filename=name,
            path=path,
            status=status,
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        )
        db.session.add(record)
        db.session.commit()
        return record.id


class TestParsing:
    def test_unset_means_disabled(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_TIME", raising=False)
        assert parse_backup_time() is None

    def test_valid_time(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "03:30")
        assert parse_backup_time() == (3, 30)

    @pytest.mark.parametrize("raw", ["3h30", "25:00", "12:60", "abc", "12", "aa:bb"])
    def test_invalid_time_raises(self, monkeypatch, raw):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", raw)
        with pytest.raises(ValueError, match="OPENHANGAR_BACKUP_TIME"):
            parse_backup_time()

    def test_keep_default(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_KEEP", raising=False)
        assert parse_backup_keep() == 30

    def test_keep_custom(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_KEEP", "7")
        assert parse_backup_keep() == 7

    @pytest.mark.parametrize("raw", ["0", "-1", "many"])
    def test_keep_invalid_raises(self, monkeypatch, raw):
        monkeypatch.setenv("OPENHANGAR_BACKUP_KEEP", raw)
        with pytest.raises(ValueError, match="OPENHANGAR_BACKUP_KEEP"):
            parse_backup_keep()

    def test_startup_validation_rejects_bad_values(self, monkeypatch):
        from init import create_app  # pyright: ignore[reportMissingImports]

        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "nope")
        monkeypatch.setenv("OPENHANGAR_BACKUP_KEEP", "zero")
        with pytest.raises(RuntimeError) as excinfo:
            create_app()
        assert "OPENHANGAR_BACKUP_TIME" in str(excinfo.value)
        assert "OPENHANGAR_BACKUP_KEEP" in str(excinfo.value)


class TestRetention:
    def test_prunes_oldest_beyond_keep(self, app, tmp_path):
        ids = [
            _add_record(app, tmp_path, f"b{i}.zip.enc", age_days=10 - i)
            for i in range(4)  # b0 oldest … b3 newest
        ]
        with app.app_context():
            removed = prune_old_backups(keep=2)
            assert removed == 2
            remaining = {r.id for r in BackupRecord.query.all()}
            assert remaining == {ids[2], ids[3]}
        assert not (tmp_path / "b0.zip.enc").exists()
        assert not (tmp_path / "b1.zip.enc").exists()
        assert (tmp_path / "b3.zip.enc").exists()

    def test_failed_records_never_pruned(self, app, tmp_path):
        _add_record(app, tmp_path, "old-fail.zip.enc", status="failed", age_days=99)
        _add_record(app, tmp_path, "ok.zip.enc", age_days=1)
        with app.app_context():
            assert prune_old_backups(keep=1) == 0
            assert BackupRecord.query.count() == 2

    def test_missing_file_still_prunes_record(self, app, tmp_path):
        _add_record(app, tmp_path, "gone.zip.enc", age_days=9, with_file=False)
        _add_record(app, tmp_path, "new.zip.enc", age_days=0)
        with app.app_context():
            assert prune_old_backups(keep=1) == 1
            assert BackupRecord.query.count() == 1

    def test_undeletable_file_keeps_record(self, app, tmp_path):
        _add_record(app, tmp_path, "stuck.zip.enc", age_days=9)
        _add_record(app, tmp_path, "new.zip.enc", age_days=0)
        with app.app_context():
            with patch(
                "services.backup_scheduler.os.remove", side_effect=OSError("nope")
            ):
                assert prune_old_backups(keep=1) == 0
            assert BackupRecord.query.count() == 2

    def test_keep_defaults_from_env(self, app, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_KEEP", "1")
        _add_record(app, tmp_path, "a.zip.enc", age_days=2)
        _add_record(app, tmp_path, "b.zip.enc", age_days=0)
        with app.app_context():
            assert prune_old_backups() == 1


class TestScheduledRun:
    def test_success_prunes(self, app):
        fake_record = type("R", (), {"filename": "x.zip.enc"})()
        with (
            patch("config.routes.run_backup", return_value=fake_record) as mock_backup,
            patch(
                "services.backup_scheduler.prune_old_backups", return_value=3
            ) as mock_prune,
        ):
            run_scheduled_backup(app)
        mock_backup.assert_called_once()
        mock_prune.assert_called_once()

    def test_failure_skips_pruning(self, app):
        with (
            patch("config.routes.run_backup", side_effect=RuntimeError("boom")),
            patch("services.backup_scheduler.prune_old_backups") as mock_prune,
        ):
            run_scheduled_backup(app)
        mock_prune.assert_not_called()

    def test_lock_not_acquired_skips(self, app):
        @contextlib.contextmanager
        def _no_lock(_db, _lock_id):
            yield False

        with (
            patch("services.advisory_lock.advisory_lock_scope", _no_lock),
            patch("config.routes.run_backup") as mock_backup,
        ):
            run_scheduled_backup(app)
        mock_backup.assert_not_called()

    def test_unexpected_error_is_caught(self, app):
        with patch("config.routes.run_backup", side_effect=KeyError("unexpected")):
            run_scheduled_backup(app)  # must not raise


class TestSchedulerThread:
    def test_disabled_without_env(self, app, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_TIME", raising=False)
        with patch("threading.Thread") as mock_thread:
            start_backup_scheduler(app)
        mock_thread.assert_not_called()

    def test_started_with_env(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "02:30")
        with patch("threading.Thread") as mock_thread:
            start_backup_scheduler(app)
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs["args"] == (app, 2, 30)

    def test_daily_loop_sleeps_and_runs(self, app):
        sleep_calls = []

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise StopIteration("exit loop")

        with (
            patch("time.sleep", side_effect=mock_sleep),
            patch("services.backup_scheduler.run_scheduled_backup") as mock_run,
        ):
            with contextlib.suppress(StopIteration):
                _backup_daily_loop(app, 0, 0)

        # run_hour=0 has always passed → the +1 day branch gives a positive sleep
        assert sleep_calls and sleep_calls[0] > 0
        assert mock_run.call_count == 1


class TestConfigPageStatus:
    def test_schedule_and_last_backup_shown(self, app, client, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "02:30")
        _add_record(app, tmp_path, "fresh.zip.enc", age_days=0)
        _login_admin(app, client, email="admin@bk-fresh.test")
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"02:30" in resp.data
        assert b"Last successful backup" in resp.data
        assert b"older than 2 days" not in resp.data

    def test_stale_backup_warns(self, app, client, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "02:30")
        _add_record(app, tmp_path, "old.zip.enc", age_days=5)
        _login_admin(app, client, email="admin@bk-stale.test")
        resp = client.get("/config/")
        assert b"older than 2 days" in resp.data

    def test_no_backup_yet_warns_when_scheduled(self, app, client, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "02:30")
        _login_admin(app, client, email="admin@bk-none.test")
        resp = client.get("/config/")
        assert b"No successful backup yet" in resp.data

    def test_not_scheduled_hint_without_env(self, app, client, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_BACKUP_TIME", raising=False)
        _login_admin(app, client, email="admin@bk-off.test")
        resp = client.get("/config/")
        assert b"OPENHANGAR_BACKUP_TIME" in resp.data

    def test_invalid_schedule_treated_as_not_scheduled(self, app, client, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_BACKUP_TIME", "not-a-time")
        monkeypatch.setenv("OPENHANGAR_BACKUP_KEEP", "bogus")
        _login_admin(app, client, email="admin@bk-bad.test")
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"No successful backup yet" not in resp.data
