"""
Tests for Phase 10: Backup & Restore.
"""

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]

from init import create_app  # pyright: ignore[reportMissingImports]
from models import BackupRecord, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
    upload_dir = tempfile.mkdtemp()
    backup_dir = tempfile.mkdtemp()
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["WTF_CSRF_ENABLED"] = False
    _app.config["RATELIMIT_ENABLED"] = False
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    _app.config["UPLOAD_FOLDER"] = upload_dir
    _app.config["BACKUP_FOLDER"] = backup_dir
    with _app.app_context():
        db.create_all()
    yield _app
    with _app.app_context():
        db.drop_all()
        db.engine.dispose()
    shutil.rmtree(upload_dir, ignore_errors=True)
    shutil.rmtree(backup_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _restore_app_state(app):
    """Snapshot and restore app.config + clean backup/upload dirs between tests.

    Some backup tests mutate app.config (e.g. set SQLALCHEMY_DATABASE_URI to a
    pg:// URL to test PostgreSQL-only code paths). With module-scoped app those
    mutations would leak to the next test. Taking a shallow snapshot at setup
    and restoring it at teardown prevents that.
    """
    config_snapshot = dict(app.config)
    yield
    # Restore config (handles added, removed, and changed keys).
    app.config.clear()
    app.config.update(config_snapshot)
    # Remove all contents (files and subdirs) from backup / upload folders.
    for folder_key in ("BACKUP_FOLDER", "UPLOAD_FOLDER"):
        folder = app.config.get(folder_key, "")
        if folder and os.path.isdir(folder):
            for name in os.listdir(folder):
                fp = os.path.join(folder, name)
                if os.path.isfile(fp):
                    os.unlink(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)


@pytest.fixture()
def client(app):
    return app.test_client()


def _setup_user(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="pilot@test.com",
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


def _login(app, client):
    uid = _setup_user(app)
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _make_valid_dump() -> bytes:
    """Create a minimal valid SQL dump as bytes."""
    return b"-- pg_dump output\nSELECT 1;\n"


def _make_zip_from_dump(sql: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("openhangar.sql", sql)
    return buf.getvalue()


# ── Unit tests: _derive_key / _encrypt_bytes ──────────────────────────────────


class TestCryptoHelpers:
    def test_derive_key_is_32_bytes(self, app):
        from config.routes import _derive_key  # pyright: ignore[reportMissingImports]

        with app.app_context():
            key = _derive_key("secret")
            assert len(key) == 32

    def test_derive_key_deterministic(self, app):
        from config.routes import _derive_key  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert _derive_key("abc") == _derive_key("abc")

    def test_derive_key_different_passwords_differ(self, app):
        from config.routes import _derive_key  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert _derive_key("abc") != _derive_key("xyz")

    def test_encrypt_decrypt_roundtrip(self, app):
        from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]

        with app.app_context():
            key = _derive_key("testpass")
            plaintext = b"hello backup world"
            ciphertext = _encrypt_bytes(plaintext, key)
            nonce, ct = ciphertext[:12], ciphertext[12:]
            recovered = AESGCM(key).decrypt(nonce, ct, None)
            assert recovered == plaintext

    def test_encrypt_nonce_is_random(self, app):
        from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

        with app.app_context():
            key = _derive_key("testpass")
            c1 = _encrypt_bytes(b"data", key)
            c2 = _encrypt_bytes(b"data", key)
            assert c1[:12] != c2[:12]  # nonces differ


# ── Unit tests: _pg_dump ───────────────────────────────────────────────────────


class TestPgDump:
    def test_raises_on_non_postgresql_url(self):
        from config.routes import _pg_dump  # pyright: ignore[reportMissingImports]

        with pytest.raises(RuntimeError, match="Unsupported"):
            _pg_dump("sqlite:///test.db")

    def test_raises_on_pg_dump_failure(self):
        from config.routes import _pg_dump  # pyright: ignore[reportMissingImports]

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = b"connection refused"
        with patch("config.routes.subprocess.run", return_value=fake_result):
            with pytest.raises(RuntimeError, match="connection refused"):
                _pg_dump("postgresql://user:pw@localhost/db")

    def test_returns_stdout_on_success(self):
        from config.routes import _pg_dump  # pyright: ignore[reportMissingImports]

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = b"SELECT 1;"
        with patch("config.routes.subprocess.run", return_value=fake_result):
            result = _pg_dump("postgresql://user:pw@localhost/db")
        assert result == b"SELECT 1;"


# ── Unit tests: run_backup ────────────────────────────────────────────────────


class TestRunBackup:
    def _mock_pg_dump(self, sql=None):
        sql = sql or _make_valid_dump()
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = sql
        return fake

    def test_creates_record_on_success(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch(
                "config.routes.subprocess.run", return_value=self._mock_pg_dump()
            ):
                record = run_backup()
            assert record.status == "ok"
            assert record.sha256 is not None
            assert record.size_bytes > 0
            assert os.path.exists(record.path)

    def test_backup_file_written_to_backup_folder(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            backup_folder = app.config["BACKUP_FOLDER"]
            with patch(
                "config.routes.subprocess.run", return_value=self._mock_pg_dump()
            ):
                record = run_backup()
            assert record.path.startswith(backup_folder)
            assert record.filename.startswith("openhangar_backup_")
            assert record.filename.endswith(".zip.enc")
            assert "development" in record.filename  # version embedded in filename

    def test_encrypted_when_key_set(self, app):
        from config.routes import _derive_key, run_backup  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch.dict(os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": "mykey"}):
                with patch(
                    "config.routes.subprocess.run", return_value=self._mock_pg_dump()
                ):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            key = _derive_key("mykey")
            nonce, ct = data[:12], data[12:]
            zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                assert "openhangar.sql" in zf.namelist()

    def test_unencrypted_when_no_key(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            env = {
                k: v
                for k, v in os.environ.items()
                if k != "OPENHANGAR_BACKUP_ENCRYPTION_KEY"
            }
            with patch.dict(os.environ, env, clear=True):
                with patch(
                    "config.routes.subprocess.run", return_value=self._mock_pg_dump()
                ):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                assert "openhangar.sql" in zf.namelist()

    def test_sha256_matches_file(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch(
                "config.routes.subprocess.run", return_value=self._mock_pg_dump()
            ):
                record = run_backup()
            with open(record.path, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
            assert record.sha256 == actual

    def test_record_persisted_in_db(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch(
                "config.routes.subprocess.run", return_value=self._mock_pg_dump()
            ):
                record = run_backup()
            record_id = record.id
        with app.app_context():
            r = db.session.get(BackupRecord, record_id)
            assert r is not None
            assert r.status == "ok"

    def test_uploads_included_in_zip(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            upload_folder = app.config["UPLOAD_FOLDER"]
            # Place a fake upload file
            with open(os.path.join(upload_folder, "doc_test_abc123.pdf"), "wb") as fh:
                fh.write(b"%PDF fake content")
            env = {
                k: v
                for k, v in os.environ.items()
                if k != "OPENHANGAR_BACKUP_ENCRYPTION_KEY"
            }
            with patch.dict(os.environ, env, clear=True):
                with patch(
                    "config.routes.subprocess.run", return_value=self._mock_pg_dump()
                ):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                assert "openhangar.sql" in names
                assert "uploads/doc_test_abc123.pdf" in names

    def test_uploads_folder_missing_does_not_fail(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            app.config["UPLOAD_FOLDER"] = "/nonexistent/uploads"
            env = {
                k: v
                for k, v in os.environ.items()
                if k != "OPENHANGAR_BACKUP_ENCRYPTION_KEY"
            }
            with patch.dict(os.environ, env, clear=True):
                with patch(
                    "config.routes.subprocess.run", return_value=self._mock_pg_dump()
                ):
                    record = run_backup()
            assert record.status == "ok"

    def test_failed_record_committed_on_pg_dump_error(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            bad = MagicMock()
            bad.returncode = 1
            bad.stderr = b"error"
            with patch("config.routes.subprocess.run", return_value=bad):
                with pytest.raises(RuntimeError):
                    run_backup()
        with app.app_context():
            r = BackupRecord.query.order_by(BackupRecord.id.desc()).first()
            assert r is not None
            assert r.status == "failed"


# ── View tests: index ──────────────────────────────────────────────────


class TestListBackups:
    def test_redirects_when_not_logged_in(self, client):
        resp = client.get("/config/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_aborts_403_in_demo_mode(self, client):
        with patch.dict(os.environ, {"OPENHANGAR_ENV": "demo"}):
            resp = client.get("/config/")
        assert resp.status_code == 403

    def test_shows_empty_state(self, app, client):
        _login(app, client)
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"No backups yet" in resp.data

    def test_shows_backup_records(self, app, client):
        _login(app, client)
        with app.app_context():
            db.session.add(
                BackupRecord(
                    filename="openhangar_backup_20260101T020000Z.zip.enc",
                    path="/data/backups/openhangar_backup_20260101T020000Z.zip.enc",
                    size_bytes=204800,
                    sha256="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                    created_at=datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc),
                    status="ok",
                )
            )
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"openhangar_backup_20260101T020000Z.zip.enc" in resp.data
        assert b"OK" in resp.data

    def test_shows_failed_record(self, app, client):
        _login(app, client)
        with app.app_context():
            db.session.add(
                BackupRecord(
                    filename="openhangar_backup_20260201T020000Z.zip.enc",
                    path="/data/backups/openhangar_backup_20260201T020000Z.zip.enc",
                    status="failed",
                )
            )
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"Failed" in resp.data

    def test_multiple_records_shown(self, app, client):
        _login(app, client)
        with app.app_context():
            for i in range(3):
                db.session.add(
                    BackupRecord(
                        filename=f"openhangar_backup_202601{i:02d}T020000Z.zip.enc",
                        path=f"/data/backups/openhangar_backup_202601{i:02d}T020000Z.zip.enc",
                        status="ok",
                    )
                )
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert resp.data.count(b"openhangar_backup_") == 3

    def test_shows_encryption_key_warning_when_not_set(self, app, client):
        _login(app, client)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENHANGAR_BACKUP_ENCRYPTION_KEY", None)
            resp = client.get("/config/")
        assert b"unencrypted" in resp.data

    def test_shows_encryption_key_ok_when_set(self, app, client):
        _login(app, client)
        with patch.dict(
            os.environ,
            {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": "test-key-32-bytes-padded-xxxxxxx"},
        ):
            resp = client.get("/config/")
        assert b"Encryption key set" in resp.data
        assert b"unencrypted" not in resp.data

    def test_shows_backup_folder_path(self, app, client):
        _login(app, client)
        resp = client.get("/config/")
        assert app.config["BACKUP_FOLDER"].encode() in resp.data

    def test_truncates_to_ten_records_and_shows_more(self, app, client):
        _login(app, client)
        with app.app_context():
            for i in range(12):
                db.session.add(
                    BackupRecord(
                        filename=f"openhangar_backup_20260{i + 1:02d}01T020000Z.zip.enc",
                        path=f"/data/backups/openhangar_backup_20260{i + 1:02d}01T020000Z.zip.enc",
                        status="ok",
                    )
                )
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert resp.data.count(b"openhangar_backup_") == 10
        assert b"2 more" in resp.data


# ── View tests: run_backup_now ────────────────────────────────────────────────


class TestTriggerBackup:
    def _mock_pg_dump(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = _make_valid_dump()
        return fake

    def test_aborts_403_when_not_logged_in(self, client):
        resp = client.post("/config/run")
        assert resp.status_code == 403

    def test_aborts_403_in_demo_mode(self, client):
        with patch.dict(os.environ, {"OPENHANGAR_ENV": "demo"}):
            resp = client.post("/config/run")
        assert resp.status_code == 403

    def test_success_redirects_with_flash(self, app, client):
        _login(app, client)
        with patch("config.routes.subprocess.run", return_value=self._mock_pg_dump()):
            with patch.dict(os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": "key"}):
                app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
                resp = client.post("/config/run", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Backup completed" in resp.data

    def test_failure_redirects_with_flash(self, app, client):
        _login(app, client)
        bad = MagicMock()
        bad.returncode = 1
        bad.stderr = b"pg_dump: connection refused"
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
        with patch("config.routes.subprocess.run", return_value=bad):
            resp = client.post("/config/run", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Backup failed" in resp.data


# ── Model tests: BackupRecord ─────────────────────────────────────────────────


class TestBackupRecordModel:
    def test_defaults(self, app):
        with app.app_context():
            r = BackupRecord(filename="f.zip.enc", path="/data/backups/f.zip.enc")
            db.session.add(r)
            db.session.commit()
            assert r.status == "ok"
            assert r.created_at is not None
            assert r.size_bytes is None
            assert r.sha256 is None

    def test_all_fields_persist(self, app):
        with app.app_context():
            r = BackupRecord(
                filename="backup.zip.enc",
                path="/data/backups/backup.zip.enc",
                size_bytes=1024,
                sha256="abc123",
                status="ok",
            )
            db.session.add(r)
            db.session.commit()
            fetched = db.session.get(BackupRecord, r.id)
            assert fetched.size_bytes == 1024
            assert fetched.sha256 == "abc123"
            assert fetched.status == "ok"


# ── Unit tests: _get_alembic_head ────────────────────────────────────────────


class TestGetAlembicHead:
    def test_returns_none_when_table_missing(self, app):
        from config.routes import _get_alembic_head  # pyright: ignore[reportMissingImports]

        with app.app_context():
            # SQLite test DB has no alembic_version table
            result = _get_alembic_head()
            assert result is None


# ── Unit tests: metadata in backup ───────────────────────────────────────────


class TestBackupMetadata:
    def _run_backup(self, app, version="0.200.0"):
        # Returns a plain dict so callers don't touch a detached ORM object.
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = _make_valid_dump()
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "OPENHANGAR_BACKUP_ENCRYPTION_KEY"
        }
        env["OPENHANGAR_VERSION"] = version
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("config.routes.subprocess.run", return_value=fake):
                with patch.dict(os.environ, env, clear=True):
                    record = run_backup()
            return {
                "id": record.id,
                "path": record.path,
                "filename": record.filename,
                "app_version": record.app_version,
                "alembic_head": record.alembic_head,
                "status": record.status,
            }

    def test_metadata_json_in_zip(self, app):
        r = self._run_backup(app, version="0.200.0")
        with open(r["path"], "rb") as fh:
            data = fh.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert "metadata.json" in zf.namelist()
            meta = json.loads(zf.read("metadata.json"))
        assert meta["app_version"] == "0.200.0"
        assert "created_at" in meta
        assert "alembic_head" in meta  # may be None in test env

    def test_meta_sidecar_written(self, app):
        r = self._run_backup(app, version="1.2.3")
        meta_path = r["path"].replace(".zip.enc", ".meta")
        assert os.path.exists(meta_path)
        with open(meta_path) as fh:
            meta = json.load(fh)
        assert meta["app_version"] == "1.2.3"

    def test_app_version_stored_in_record(self, app):
        r = self._run_backup(app, version="0.300.0")
        with app.app_context():
            fetched = db.session.get(BackupRecord, r["id"])
            assert fetched.app_version == "0.300.0"

    def test_development_version_when_env_unset(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = _make_valid_dump()
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "OPENHANGAR_VERSION")
        }
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("config.routes.subprocess.run", return_value=fake):
                with patch.dict(os.environ, env, clear=True):
                    record = run_backup()
            assert record.app_version == "development"

    def test_failed_backup_does_not_write_sidecar(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]

        bad = MagicMock()
        bad.returncode = 1
        bad.stderr = b"connection refused"
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("config.routes.subprocess.run", return_value=bad):
                with pytest.raises(RuntimeError):
                    run_backup()
        backup_folder = app.config["BACKUP_FOLDER"]
        meta_files = [f for f in os.listdir(backup_folder) if f.endswith(".meta")]
        assert meta_files == []


# ── CLI tests: check-empty-db ─────────────────────────────────────────────────


class TestCheckEmptyDb:
    def test_exits_0_when_no_users(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["check-empty-db"])
        assert result.exit_code == 0
        assert "empty" in result.output

    def test_exits_1_when_users_exist(self, app):
        _setup_user(app)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["check-empty-db"])
        assert result.exit_code == 1

    def test_exits_0_when_schema_missing(self, app):
        from sqlalchemy.exc import ProgrammingError  # pyright: ignore[reportMissingImports]

        from models import User  # pyright: ignore[reportMissingImports]

        runner = app.test_cli_runner()
        with app.app_context(), patch.object(User, "query") as mock_query:
            mock_query.count.side_effect = ProgrammingError(
                "relation does not exist", {}, None
            )
            result = runner.invoke(args=["check-empty-db"])
        assert result.exit_code == 0
        assert "empty" in result.output


# ── CLI tests: restore-backup ─────────────────────────────────────────────────


# ── Unit tests: _drop_and_restore_schema ─────────────────────────────────────


class TestDropAndRestoreSchema:
    def _mock_conn(self):
        c = MagicMock()
        c.__enter__ = MagicMock(return_value=c)
        c.__exit__ = MagicMock(return_value=False)
        return c

    def _pg_patches(self, db):
        """Patch the three operations that must not run against the test SQLite DB."""
        return (
            patch.object(db.engine, "dispose"),
            patch.object(db.session, "remove"),
        )

    def test_calls_psql_with_sql_bytes(self, app):
        from init import _drop_and_restore_schema  # pyright: ignore[reportMissingImports]
        from models import db as _db  # pyright: ignore[reportMissingImports]

        fake = MagicMock()
        fake.returncode = 0
        mock_conn = self._mock_conn()

        with app.app_context():
            p_dispose, p_remove = self._pg_patches(_db)
            with patch.object(_db.engine, "connect", return_value=mock_conn):
                with patch("subprocess.run", return_value=fake) as mock_sub:
                    with p_dispose, p_remove:
                        _drop_and_restore_schema("postgresql://u:p@h/db", b"-- sql")

        cmd = mock_sub.call_args[0][0]
        assert cmd[:2] == ["psql", "--no-password"]
        assert cmd[2] == "-f"
        assert cmd[3].endswith(".sql")
        assert cmd[4] == "postgresql://u:p@h/db"

    def test_raises_on_psql_failure(self, app):
        from init import _drop_and_restore_schema  # pyright: ignore[reportMissingImports]
        from models import db as _db  # pyright: ignore[reportMissingImports]

        fake = MagicMock()
        fake.returncode = 1
        mock_conn = self._mock_conn()

        with app.app_context():
            p_dispose, p_remove = self._pg_patches(_db)
            with patch.object(_db.engine, "connect", return_value=mock_conn):
                with patch("subprocess.run", return_value=fake):
                    with p_dispose, p_remove:
                        with pytest.raises(RuntimeError, match="psql exited with code"):
                            _drop_and_restore_schema("postgresql://u:p@h/db", b"-- sql")

    def test_raises_on_psql_timeout(self, app):
        import subprocess as _sp

        from init import _drop_and_restore_schema  # pyright: ignore[reportMissingImports]
        from models import db as _db  # pyright: ignore[reportMissingImports]

        mock_conn = self._mock_conn()

        with app.app_context():
            p_dispose, p_remove = self._pg_patches(_db)
            with patch.object(_db.engine, "connect", return_value=mock_conn):
                with patch(
                    "subprocess.run", side_effect=_sp.TimeoutExpired("psql", 600)
                ):
                    with p_dispose, p_remove:
                        with pytest.raises(RuntimeError, match="timed out"):
                            _drop_and_restore_schema("postgresql://u:p@h/db", b"-- sql")

    def test_executes_drop_schema(self, app):
        from init import _drop_and_restore_schema  # pyright: ignore[reportMissingImports]
        from models import db as _db  # pyright: ignore[reportMissingImports]

        fake = MagicMock()
        fake.returncode = 0
        mock_conn = self._mock_conn()

        with app.app_context():
            p_dispose, p_remove = self._pg_patches(_db)
            with patch.object(_db.engine, "connect", return_value=mock_conn):
                with patch("subprocess.run", return_value=fake):
                    with p_dispose, p_remove:
                        _drop_and_restore_schema("postgresql://u:p@h/db", b"-- sql")

        sqls = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
        assert any("DROP SCHEMA" in s for s in sqls)
        assert any("CREATE SCHEMA" in s for s in sqls)


class TestRestoreBackup:
    def _make_archive(self, backup_dir, sql=None, metadata=None, encrypt_key=""):
        """Build a minimal archive and matching .meta sidecar.

        Produces a .zip.enc when encrypt_key is given, otherwise a plain .zip.
        """
        import io as _io
        import json as _json
        import zipfile as _zipfile

        sql = sql or _make_valid_dump()
        metadata = metadata or {
            "app_version": "0.100.0",
            "alembic_head": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openhangar.sql", sql)
            zf.writestr("metadata.json", _json.dumps(metadata))
        zip_bytes = buf.getvalue()

        if encrypt_key:
            from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

            payload = _encrypt_bytes(zip_bytes, _derive_key(encrypt_key))
            ext = ".zip.enc"
        else:
            payload = zip_bytes
            ext = ".zip"

        archive_path = os.path.join(backup_dir, f"openhangar_backup_test{ext}")
        with open(archive_path, "wb") as fh:
            fh.write(payload)
        meta_path = os.path.join(backup_dir, "openhangar_backup_test.meta")
        with open(meta_path, "w") as fh:
            _json.dump(metadata, fh)
        return archive_path

    def test_refuses_when_db_has_users(self, app):
        _setup_user(app)
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "not empty" in result.output or "not empty" in str(
            result.exception or ""
        )

    def test_refuses_non_postgresql_url(self, app):
        # SQLite URL is explicitly rejected
        archive = self._make_archive(app.config["BACKUP_FOLDER"])
        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "PostgreSQL" in (result.output + str(result.exception or ""))

    def test_fails_with_wrong_decryption_key(self, app):
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(backup_dir, encrypt_key="correct-key")
        runner = app.test_cli_runner()
        with patch.dict(os.environ, {"OPENHANGAR_RESTORE_ENCRYPTION_KEY": "wrong-key"}):
            result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "Decryption" in (result.output + str(result.exception or ""))

    def test_missing_archive_raises(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore-backup", "/nonexistent/backup.zip.enc"])
        assert result.exit_code != 0

    def test_refuses_future_alembic_revision(self, app):
        # An archive with an unknown alembic_head should be rejected.
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(
            backup_dir,
            metadata={
                "app_version": "99.0.0",
                "alembic_head": "ffffffffffffffff",
                "created_at": "",
            },
        )
        runner = app.test_cli_runner()
        # Mock ScriptDirectory so the known-revision check actually executes
        mock_script = MagicMock()
        mock_rev = MagicMock()
        mock_rev.revision = "3f8a2c91b047"
        mock_script.walk_revisions.return_value = [mock_rev]
        with patch(
            "alembic.script.ScriptDirectory.from_config", return_value=mock_script
        ):
            result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "migration chain" in result.output

    def test_known_alembic_revision_passes_check(self, app):
        # A known revision should not cause an early exit from the version check.
        # The command still exits 1 because the URL is sqlite, but NOT from version check.
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(
            backup_dir,
            metadata={
                "app_version": "0.100.0",
                "alembic_head": "3f8a2c91b047",
                "created_at": "",
            },
        )
        runner = app.test_cli_runner()
        result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "PostgreSQL" in result.output

    def test_successful_restore_with_mocked_psql(self, app):
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 0
        assert "Restore complete" in result.output

    def test_restore_reports_psql_failure(self, app):
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch(
                "init._drop_and_restore_schema",
                side_effect=RuntimeError("psql: error: connection refused"),
            ):
                result = runner.invoke(args=["restore-backup", archive])
        assert result.exit_code == 1
        assert "psql restore failed" in result.output

    def test_restore_includes_uploads(self, app):
        import io as _io
        import zipfile as _zipfile

        backup_dir = app.config["BACKUP_FOLDER"]
        upload_folder = app.config["UPLOAD_FOLDER"]

        # Build an unencrypted archive (.zip) with a flat upload entry
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openhangar.sql", _make_valid_dump())
            zf.writestr(
                "metadata.json",
                json.dumps(
                    {"app_version": "0.1.0", "alembic_head": None, "created_at": ""}
                ),
            )
            zf.writestr("uploads/testdoc.pdf", b"%PDF test")
        archive_path = os.path.join(backup_dir, "openhangar_backup_uploads_test.zip")
        with open(archive_path, "wb") as fh:
            fh.write(buf.getvalue())

        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive_path])

        assert result.exit_code == 0
        assert os.path.exists(os.path.join(upload_folder, "testdoc.pdf"))

    def test_restore_includes_uploads_in_subdirectories(self, app):
        """Files stored under tenant/aircraft subdirs are restored to the correct path."""
        import io as _io
        import zipfile as _zipfile

        backup_dir = app.config["BACKUP_FOLDER"]
        upload_folder = app.config["UPLOAD_FOLDER"]

        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openhangar.sql", _make_valid_dump())
            zf.writestr(
                "metadata.json",
                json.dumps(
                    {"app_version": "0.1.0", "alembic_head": None, "created_at": ""}
                ),
            )
            zf.writestr("uploads/myhangar/OO-TST/photos/01-abc123.jpg", b"JPEG")
            zf.writestr("uploads/myhangar/OO-TST/docs/manual.pdf", b"%PDF")
        archive_path = os.path.join(backup_dir, "openhangar_backup_subdir_test.zip")
        with open(archive_path, "wb") as fh:
            fh.write(buf.getvalue())

        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive_path])

        assert result.exit_code == 0
        assert os.path.exists(
            os.path.join(upload_folder, "myhangar", "OO-TST", "photos", "01-abc123.jpg")
        )
        assert os.path.exists(
            os.path.join(upload_folder, "myhangar", "OO-TST", "docs", "manual.pdf")
        )

    def test_restore_clears_existing_uploads_into_snapshot(self, app):
        """Pre-existing upload files are snapshotted before being wiped."""
        backup_dir = app.config["BACKUP_FOLDER"]
        upload_folder = app.config["UPLOAD_FOLDER"]

        # Plant an existing file in the upload folder
        os.makedirs(os.path.join(upload_folder, "oldtenant"), exist_ok=True)
        old_file = os.path.join(upload_folder, "oldtenant", "stale.pdf")
        with open(old_file, "wb") as fh:
            fh.write(b"%PDF stale")

        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive])

        assert result.exit_code == 0
        # Stale file must be gone from uploads
        assert not os.path.exists(old_file)
        # A snapshot zip must have been created in the backup folder
        snapshots = [
            f
            for f in os.listdir(backup_dir)
            if f.startswith("uploads_pre_restore_") and f.endswith(".zip")
        ]
        assert len(snapshots) == 1
        # Snapshot must contain the stale file
        with zipfile.ZipFile(os.path.join(backup_dir, snapshots[0])) as zf:
            assert "oldtenant/stale.pdf" in zf.namelist()

    def test_restore_snapshot_encrypted_when_key_set(self, app):
        """Snapshot is encrypted with OPENHANGAR_BACKUP_ENCRYPTION_KEY when set."""
        import io as _io

        backup_dir = app.config["BACKUP_FOLDER"]
        upload_folder = app.config["UPLOAD_FOLDER"]

        old_file = os.path.join(upload_folder, "file.txt")
        with open(old_file, "wb") as fh:
            fh.write(b"data")

        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                with patch.dict(
                    os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": "snap-key"}
                ):
                    result = runner.invoke(args=["restore-backup", archive])

        assert result.exit_code == 0
        snapshots = [
            f
            for f in os.listdir(backup_dir)
            if f.startswith("uploads_pre_restore_") and f.endswith(".zip.enc")
        ]
        assert len(snapshots) == 1
        # Verify it decrypts correctly
        from config.routes import _derive_key  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]

        with open(os.path.join(backup_dir, snapshots[0]), "rb") as fh:
            payload = fh.read()
        key = _derive_key("snap-key")
        zip_bytes = AESGCM(key).decrypt(payload[:12], payload[12:], None)
        with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as zf:
            assert "file.txt" in zf.namelist()

    def test_restore_no_snapshot_when_upload_folder_empty(self, app):
        """No snapshot file is created when the upload folder is already empty."""
        backup_dir = app.config["BACKUP_FOLDER"]
        archive = self._make_archive(backup_dir)
        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive])

        assert result.exit_code == 0
        snapshots = [
            f for f in os.listdir(backup_dir) if f.startswith("uploads_pre_restore_")
        ]
        assert len(snapshots) == 0

    def test_enc_archive_without_key_is_rejected(self, app):
        """.enc extension with no OPENHANGAR_RESTORE_ENCRYPTION_KEY is rejected clearly."""
        import io as _io
        import zipfile as _zipfile

        backup_dir = app.config["BACKUP_FOLDER"]
        # Write an unencrypted zip with .enc extension (simulates passing wrong file)
        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openhangar.sql", _make_valid_dump())
            zf.writestr(
                "metadata.json",
                json.dumps(
                    {"app_version": "0.1.0", "alembic_head": None, "created_at": ""}
                ),
            )
        archive_path = os.path.join(backup_dir, "openhangar_backup_nokeytest.zip.enc")
        with open(archive_path, "wb") as fh:
            fh.write(buf.getvalue())

        runner = app.test_cli_runner()
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "OPENHANGAR_RESTORE_ENCRYPTION_KEY"
        }
        with patch.dict(os.environ, env, clear=True):
            result = runner.invoke(args=["restore-backup", archive_path])

        assert result.exit_code == 1
        assert (
            "no decryption key" in result.output.lower()
            or "RESTORE_ENCRYPTION_KEY" in result.output
        )

    def test_restore_skips_bare_uploads_directory_entry(self, app):
        """A bare 'uploads/' directory entry in the zip does not cause an error."""
        import io as _io
        import zipfile as _zipfile

        backup_dir = app.config["BACKUP_FOLDER"]
        upload_folder = app.config["UPLOAD_FOLDER"]

        buf = _io.BytesIO()
        with _zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("openhangar.sql", _make_valid_dump())
            zf.writestr(
                "metadata.json",
                json.dumps(
                    {"app_version": "0.1.0", "alembic_head": None, "created_at": ""}
                ),
            )
            zf.mkdir("uploads/")  # bare directory entry
            zf.writestr("uploads/real.pdf", b"%PDF")
        archive_path = os.path.join(backup_dir, "openhangar_backup_direntry.zip")
        with open(archive_path, "wb") as fh:
            fh.write(buf.getvalue())

        runner = app.test_cli_runner()
        with patch.dict(
            app.config, {"SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
        ):
            with patch("init._drop_and_restore_schema"):
                result = runner.invoke(args=["restore-backup", archive_path])

        assert result.exit_code == 0
        assert os.path.exists(os.path.join(upload_folder, "real.pdf"))


# ── Restore path: verify docs decryption matches backup output ────────────────


class TestRestoreDecryption:
    """Regression tests ensuring the restore docs stay in sync with backup code."""

    def test_restore_script_decrypts_backup(self, app):
        """The HKDF snippet shown in docs/backup_restore.md must decrypt a backup."""
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives import hashes  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # pyright: ignore[reportMissingImports]

        passphrase = "restore-test-key"
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            fake = MagicMock()
            fake.returncode = 0
            fake.stdout = _make_valid_dump()
            with patch("config.routes.subprocess.run", return_value=fake):
                with patch.dict(
                    os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": passphrase}
                ):
                    record = run_backup()
            backup_path = record.path

        with open(backup_path, "rb") as fh:
            data = fh.read()

        # Exact HKDF derivation as documented in docs/backup_restore.md
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"openhangar-backup-kdf-salt-v1",
            info=b"openhangar-backup-v1",
        ).derive(passphrase.encode())

        nonce, ct = data[:12], data[12:]
        zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            assert "openhangar.sql" in zf.namelist()

    def test_wrong_key_cannot_decrypt(self, app):
        from config.routes import run_backup  # pyright: ignore[reportMissingImports]
        from cryptography.exceptions import InvalidTag  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives import hashes  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # pyright: ignore[reportMissingImports]

        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            fake = MagicMock()
            fake.returncode = 0
            fake.stdout = _make_valid_dump()
            with patch("config.routes.subprocess.run", return_value=fake):
                with patch.dict(
                    os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": "correct-key"}
                ):
                    record = run_backup()
            backup_path = record.path

        with open(backup_path, "rb") as fh:
            data = fh.read()

        wrong_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"openhangar-backup-kdf-salt-v1",
            info=b"openhangar-backup-v1",
        ).derive(b"wrong-key")

        nonce, ct = data[:12], data[12:]
        with pytest.raises(InvalidTag):
            AESGCM(wrong_key).decrypt(nonce, ct, None)


# ── Unit tests: _add_uploads_to_zip ──────────────────────────────────────────


class TestAddUploadsToZip:
    def test_adds_files_under_uploads_prefix(self, app):
        from config.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]

        with app.app_context():
            upload_folder = app.config["UPLOAD_FOLDER"]
            with open(os.path.join(upload_folder, "file1.pdf"), "wb") as fh:
                fh.write(b"pdf content")
            with open(os.path.join(upload_folder, "file2.jpg"), "wb") as fh:
                fh.write(b"jpg content")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, upload_folder)
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                names = zf.namelist()
            assert "uploads/file1.pdf" in names
            assert "uploads/file2.jpg" in names

    def test_adds_files_in_subdirectories(self, app):
        from config.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]

        with app.app_context():
            upload_folder = app.config["UPLOAD_FOLDER"]
            subdir = os.path.join(upload_folder, "tenant", "OO-TST", "photos")
            os.makedirs(subdir, exist_ok=True)
            with open(os.path.join(subdir, "01-abc.jpg"), "wb") as fh:
                fh.write(b"JPEG")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, upload_folder)
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                names = zf.namelist()
            assert "uploads/tenant/OO-TST/photos/01-abc.jpg" in names

    def test_empty_folder_produces_no_entries(self, app):
        from config.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]

        with app.app_context():
            upload_folder = app.config["UPLOAD_FOLDER"]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, upload_folder)
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                assert zf.namelist() == []

    def test_nonexistent_folder_is_silently_skipped(self, app):
        from config.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]

        with app.app_context():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, "/nonexistent/path")
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                assert zf.namelist() == []


# ── Route tests: update_map_tiles ─────────────────────────────────────────────


class TestUpdateMapTiles:
    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.post("/config/map-tiles", data={"openaip_api_key": "KEY"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_creates_setting_when_absent(self, app, client):
        from models import AppSetting  # pyright: ignore[reportMissingImports]

        _login(app, client)
        resp = client.post("/config/map-tiles", data={"openaip_api_key": "MYKEY"})
        assert resp.status_code == 302
        with app.app_context():
            s = db.session.get(AppSetting, "openaip_api_key")
            assert s is not None and s.value == "MYKEY"

    def test_updates_existing_setting(self, app, client):
        from models import AppSetting  # pyright: ignore[reportMissingImports]

        _login(app, client)
        with app.app_context():
            db.session.add(AppSetting(key="openaip_api_key", value="OLD"))
            db.session.commit()
        client.post("/config/map-tiles", data={"openaip_api_key": "NEW"})
        with app.app_context():
            s = db.session.get(AppSetting, "openaip_api_key")
            assert s is not None and s.value == "NEW"

    def test_deletes_setting_when_key_empty_and_setting_exists(self, app, client):
        from models import AppSetting  # pyright: ignore[reportMissingImports]

        _login(app, client)
        with app.app_context():
            db.session.add(AppSetting(key="openaip_api_key", value="SOMEKEY"))
            db.session.commit()
        client.post("/config/map-tiles", data={"openaip_api_key": ""})
        with app.app_context():
            assert db.session.get(AppSetting, "openaip_api_key") is None

    def test_empty_key_with_no_existing_setting_flashes_removed(self, app, client):
        _login(app, client)
        resp = client.post(
            "/config/map-tiles",
            data={"openaip_api_key": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
