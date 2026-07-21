"""
Tests for automated backup restore-verification (INFRA-06, part 3):
services/backup_verification.py.
"""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest  # pyright: ignore[reportMissingImports]

from services.backup_verification import (  # pyright: ignore[reportMissingImports]
    BackupVerificationError,
    verify_and_alert,
    verify_backup_record,
)


def _record(path: str, id_: int = 1, filename: str = "backup.zip") -> SimpleNamespace:
    return SimpleNamespace(id=id_, filename=filename, path=path)


def _valid_sql() -> bytes:
    return b"--\n-- PostgreSQL database dump\n--\n\nSELECT 1;\n"


def _make_zip(sql: bytes | None = None, metadata: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if sql is not None:
            zf.writestr("openhangar.sql", sql)
        if metadata is not None:
            zf.writestr("metadata.json", json.dumps(metadata))
    return buf.getvalue()


def _write(tmp_path, name: str, data: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


class TestVerifyBackupRecordPlain:
    def test_valid_archive_passes(self, tmp_path):
        zip_bytes = _make_zip(_valid_sql(), {"app_version": "1.0"})
        path = _write(tmp_path, "backup.zip", zip_bytes)
        verify_backup_record(_record(path))  # does not raise

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(BackupVerificationError, match="missing"):
            verify_backup_record(_record(str(tmp_path / "nope.zip")))

    def test_not_a_zip_raises(self, tmp_path):
        path = _write(tmp_path, "backup.zip", b"not a zip file")
        with pytest.raises(BackupVerificationError, match="not a valid zip"):
            verify_backup_record(_record(path))

    def test_missing_sql_raises(self, tmp_path):
        zip_bytes = _make_zip(sql=None, metadata={"app_version": "1.0"})
        path = _write(tmp_path, "backup.zip", zip_bytes)
        with pytest.raises(BackupVerificationError, match="openhangar.sql is missing"):
            verify_backup_record(_record(path))

    def test_sql_without_pg_dump_marker_raises(self, tmp_path):
        zip_bytes = _make_zip(b"not a real dump", {"app_version": "1.0"})
        path = _write(tmp_path, "backup.zip", zip_bytes)
        with pytest.raises(BackupVerificationError, match="does not look like"):
            verify_backup_record(_record(path))

    def test_missing_metadata_raises(self, tmp_path):
        zip_bytes = _make_zip(_valid_sql(), metadata=None)
        path = _write(tmp_path, "backup.zip", zip_bytes)
        with pytest.raises(
            BackupVerificationError, match="metadata.json manifest is missing"
        ):
            verify_backup_record(_record(path))

    def test_invalid_metadata_json_raises(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("openhangar.sql", _valid_sql())
            zf.writestr("metadata.json", b"{not valid json")
        path = _write(tmp_path, "backup.zip", buf.getvalue())
        with pytest.raises(BackupVerificationError, match="not valid JSON"):
            verify_backup_record(_record(path))

    def test_crc_failure_raises(self, tmp_path):
        zip_bytes = bytearray(_make_zip(_valid_sql(), {"app_version": "1.0"}))
        # Flip a byte in the middle of the compressed data to break its CRC
        # without corrupting the zip's central directory structure itself.
        mid = len(zip_bytes) // 2
        zip_bytes[mid] ^= 0xFF
        path = _write(tmp_path, "backup.zip", bytes(zip_bytes))
        with pytest.raises(BackupVerificationError):
            verify_backup_record(_record(path))


class TestVerifyBackupRecordEncrypted:
    def test_valid_encrypted_archive_passes(self, tmp_path, monkeypatch):
        from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

        monkeypatch.setenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "testpass")
        zip_bytes = _make_zip(_valid_sql(), {"app_version": "1.0"})
        encrypted = _encrypt_bytes(zip_bytes, _derive_key("testpass"))
        path = _write(tmp_path, "backup.zip.enc", encrypted)
        verify_backup_record(_record(path))  # does not raise

    def test_encrypted_without_key_raises(self, tmp_path, monkeypatch):
        from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

        encrypted = _encrypt_bytes(_make_zip(_valid_sql(), {}), _derive_key("testpass"))
        path = _write(tmp_path, "backup.zip.enc", encrypted)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY_FILE", raising=False)
        with pytest.raises(BackupVerificationError, match="not set"):
            verify_backup_record(_record(path))

    def test_wrong_key_raises(self, tmp_path, monkeypatch):
        from config.routes import _derive_key, _encrypt_bytes  # pyright: ignore[reportMissingImports]

        encrypted = _encrypt_bytes(
            _make_zip(_valid_sql(), {}), _derive_key("rightpass")
        )
        path = _write(tmp_path, "backup.zip.enc", encrypted)
        monkeypatch.setenv("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "wrongpass")
        with pytest.raises(BackupVerificationError, match="decryption failed"):
            verify_backup_record(_record(path))


class TestVerifyAndAlert:
    def test_returns_true_and_logs_info_on_success(self, tmp_path):
        zip_bytes = _make_zip(_valid_sql(), {"app_version": "1.0"})
        path = _write(tmp_path, "backup.zip", zip_bytes)
        with patch("services.backup_verification.log") as mock_log:
            assert verify_and_alert(_record(path)) is True
            mock_log.info.assert_called_once()

    def test_returns_false_and_logs_security_error_on_verification_failure(
        self, tmp_path
    ):
        path = str(tmp_path / "nope.zip")
        with patch("services.backup_verification.log") as mock_log:
            assert verify_and_alert(_record(path)) is False
            args = mock_log.error.call_args[0]
            assert "[SECURITY] backup.verification_failed" in args[0]

    def test_returns_false_on_unexpected_error_without_raising(self, tmp_path):
        zip_bytes = _make_zip(_valid_sql(), {"app_version": "1.0"})
        path = _write(tmp_path, "backup.zip", zip_bytes)
        with patch(
            "services.backup_verification.verify_backup_record",
            side_effect=RuntimeError("boom"),
        ):
            with patch("services.backup_verification.log") as mock_log:
                assert verify_and_alert(_record(path)) is False
                mock_log.exception.assert_called_once()
