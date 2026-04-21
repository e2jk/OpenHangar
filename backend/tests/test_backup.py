"""
Tests for Phase 10: Backup & Restore.
"""
import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from init import create_app  # pyright: ignore[reportMissingImports]
from models import BackupRecord, Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def app():
    upload_dir = tempfile.mkdtemp()
    backup_dir = tempfile.mkdtemp()
    _app = create_app()
    _app.config["TESTING"] = True
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["UPLOAD_FOLDER"] = upload_dir
    _app.config["BACKUP_FOLDER"] = backup_dir
    with _app.app_context():
        db.create_all()
    yield _app
    with _app.app_context():
        db.drop_all()
    shutil.rmtree(upload_dir, ignore_errors=True)
    shutil.rmtree(backup_dir, ignore_errors=True)


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
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
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
        from backup.routes import _derive_key  # pyright: ignore[reportMissingImports]
        with app.app_context():
            key = _derive_key("secret")
            assert len(key) == 32

    def test_derive_key_deterministic(self, app):
        from backup.routes import _derive_key  # pyright: ignore[reportMissingImports]
        with app.app_context():
            assert _derive_key("abc") == _derive_key("abc")

    def test_derive_key_different_passwords_differ(self, app):
        from backup.routes import _derive_key  # pyright: ignore[reportMissingImports]
        with app.app_context():
            assert _derive_key("abc") != _derive_key("xyz")

    def test_encrypt_decrypt_roundtrip(self, app):
        from backup.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
        with app.app_context():
            key = _derive_key("testpass")
            plaintext = b"hello backup world"
            ciphertext = _encrypt_bytes(plaintext, key)
            nonce, ct = ciphertext[:12], ciphertext[12:]
            recovered = AESGCM(key).decrypt(nonce, ct, None)
            assert recovered == plaintext

    def test_encrypt_nonce_is_random(self, app):
        from backup.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]
        with app.app_context():
            key = _derive_key("testpass")
            c1 = _encrypt_bytes(b"data", key)
            c2 = _encrypt_bytes(b"data", key)
            assert c1[:12] != c2[:12]  # nonces differ


# ── Unit tests: _pg_dump ───────────────────────────────────────────────────────

class TestPgDump:
    def test_raises_on_non_postgresql_url(self, app):
        from backup.routes import _pg_dump  # pyright: ignore[reportMissingImports]
        with app.app_context():
            with pytest.raises(RuntimeError, match="Unsupported"):
                _pg_dump("sqlite:///test.db")

    def test_raises_on_pg_dump_failure(self, app):
        from backup.routes import _pg_dump  # pyright: ignore[reportMissingImports]
        with app.app_context():
            fake_result = MagicMock()
            fake_result.returncode = 1
            fake_result.stderr = b"connection refused"
            with patch("backup.routes.subprocess.run", return_value=fake_result):
                with pytest.raises(RuntimeError, match="connection refused"):
                    _pg_dump("postgresql://user:pw@localhost/db")

    def test_returns_stdout_on_success(self, app):
        from backup.routes import _pg_dump  # pyright: ignore[reportMissingImports]
        with app.app_context():
            fake_result = MagicMock()
            fake_result.returncode = 0
            fake_result.stdout = b"SELECT 1;"
            with patch("backup.routes.subprocess.run", return_value=fake_result):
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
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                record = run_backup()
            assert record.status == "ok"
            assert record.sha256 is not None
            assert record.size_bytes > 0
            assert os.path.exists(record.path)

    def test_backup_file_written_to_backup_folder(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            backup_folder = app.config["BACKUP_FOLDER"]
            with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                record = run_backup()
            assert record.path.startswith(backup_folder)
            assert record.filename.startswith("openhangar_backup_")
            assert record.filename.endswith(".zip.enc")

    def test_encrypted_when_key_set(self, app):
        from backup.routes import _derive_key, run_backup  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": "mykey"}):
                with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            key = _derive_key("mykey")
            nonce, ct = data[:12], data[12:]
            zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                assert "openhangar.sql" in zf.namelist()

    def test_unencrypted_when_no_key(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            env = {k: v for k, v in os.environ.items() if k != "BACKUP_ENCRYPTION_KEY"}
            with patch.dict(os.environ, env, clear=True):
                with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                assert "openhangar.sql" in zf.namelist()

    def test_sha256_matches_file(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                record = run_backup()
            with open(record.path, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
            assert record.sha256 == actual

    def test_record_persisted_in_db(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                record = run_backup()
            record_id = record.id
        with app.app_context():
            r = db.session.get(BackupRecord, record_id)
            assert r is not None
            assert r.status == "ok"

    def test_uploads_included_in_zip(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            upload_folder = app.config["UPLOAD_FOLDER"]
            # Place a fake upload file
            with open(os.path.join(upload_folder, "doc_test_abc123.pdf"), "wb") as fh:
                fh.write(b"%PDF fake content")
            env = {k: v for k, v in os.environ.items() if k != "BACKUP_ENCRYPTION_KEY"}
            with patch.dict(os.environ, env, clear=True):
                with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                    record = run_backup()
            with open(record.path, "rb") as fh:
                data = fh.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                assert "openhangar.sql" in names
                assert "uploads/doc_test_abc123.pdf" in names

    def test_uploads_folder_missing_does_not_fail(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            app.config["UPLOAD_FOLDER"] = "/nonexistent/uploads"
            env = {k: v for k, v in os.environ.items() if k != "BACKUP_ENCRYPTION_KEY"}
            with patch.dict(os.environ, env, clear=True):
                with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
                    record = run_backup()
            assert record.status == "ok"

    def test_failed_record_committed_on_pg_dump_error(self, app):
        from backup.routes import run_backup  # pyright: ignore[reportMissingImports]
        with app.app_context():
            app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://u:p@h/db"
            bad = MagicMock()
            bad.returncode = 1
            bad.stderr = b"error"
            with patch("backup.routes.subprocess.run", return_value=bad):
                with pytest.raises(RuntimeError):
                    run_backup()
        with app.app_context():
            r = BackupRecord.query.order_by(BackupRecord.id.desc()).first()
            assert r is not None
            assert r.status == "failed"


# ── View tests: list_backups ──────────────────────────────────────────────────

class TestListBackups:
    def test_redirects_when_not_logged_in(self, client):
        resp = client.get("/config/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_aborts_403_in_demo_mode(self, client):
        with patch.dict(os.environ, {"FLASK_ENV": "demo"}):
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
            db.session.add(BackupRecord(
                filename="openhangar_backup_20260101T020000Z.zip.enc",
                path="/data/backups/openhangar_backup_20260101T020000Z.zip.enc",
                size_bytes=204800,
                sha256="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                created_at=datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc),
                status="ok",
            ))
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"openhangar_backup_20260101T020000Z.zip.enc" in resp.data
        assert b"OK" in resp.data

    def test_shows_failed_record(self, app, client):
        _login(app, client)
        with app.app_context():
            db.session.add(BackupRecord(
                filename="openhangar_backup_20260201T020000Z.zip.enc",
                path="/data/backups/openhangar_backup_20260201T020000Z.zip.enc",
                status="failed",
            ))
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"Failed" in resp.data

    def test_multiple_records_shown(self, app, client):
        _login(app, client)
        with app.app_context():
            for i in range(3):
                db.session.add(BackupRecord(
                    filename=f"openhangar_backup_202601{i:02d}T020000Z.zip.enc",
                    path=f"/data/backups/openhangar_backup_202601{i:02d}T020000Z.zip.enc",
                    status="ok",
                ))
            db.session.commit()
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert resp.data.count(b"openhangar_backup_") == 3


# ── View tests: trigger_backup ────────────────────────────────────────────────

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
        with patch.dict(os.environ, {"FLASK_ENV": "demo"}):
            resp = client.post("/config/run")
        assert resp.status_code == 403

    def test_success_redirects_with_flash(self, app, client):
        _login(app, client)
        with patch("backup.routes.subprocess.run", return_value=self._mock_pg_dump()):
            with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": "key"}):
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
        with patch("backup.routes.subprocess.run", return_value=bad):
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


# ── Unit tests: _add_uploads_to_zip ──────────────────────────────────────────

class TestAddUploadsToZip:
    def test_adds_files_under_uploads_prefix(self, app):
        from backup.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]
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

    def test_empty_folder_produces_no_entries(self, app):
        from backup.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]
        with app.app_context():
            upload_folder = app.config["UPLOAD_FOLDER"]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, upload_folder)
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                assert zf.namelist() == []

    def test_nonexistent_folder_is_silently_skipped(self, app):
        from backup.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]
        with app.app_context():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                _add_uploads_to_zip(zf, "/nonexistent/path")
            buf.seek(0)
            with zipfile.ZipFile(buf) as zf:
                assert zf.namelist() == []
