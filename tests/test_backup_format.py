"""Direct unit tests for services/backup_format.py.

Extracted from app/init.py's restore_backup_command and
services/backup_verification.py's verify_backup_record so the two paths
can't diverge, and so it can be fuzzed directly
(fuzz/fuzz_backup_format.py). Before the extraction, restore_backup_command
had no error handling around this parsing at all — an unhandled
zipfile.BadZipFile or KeyError (missing openhangar.sql) would crash the CLI
command with a raw traceback instead of a clean error message.
"""

import io
import json
import zipfile

import pytest  # pyright: ignore[reportMissingImports]

from services.backup_format import (  # pyright: ignore[reportMissingImports]
    BackupArchiveError,
    parse_backup_archive,
)

_VALID_SQL = b"-- PostgreSQL database dump\nSELECT 1;\n"


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestParseBackupArchive:
    def test_valid_archive_with_metadata(self):
        zip_bytes = _make_zip(
            {
                "openhangar.sql": _VALID_SQL,
                "metadata.json": json.dumps({"app_version": "1.0"}),
                "uploads/tenant/file.jpg": b"data",
            }
        )
        metadata, sql_bytes, upload_entries = parse_backup_archive(zip_bytes)
        assert metadata == {"app_version": "1.0"}
        assert sql_bytes == _VALID_SQL
        assert upload_entries == ["uploads/tenant/file.jpg"]

    def test_missing_metadata_defaults_to_empty_dict(self):
        zip_bytes = _make_zip({"openhangar.sql": _VALID_SQL})
        metadata, _sql, _uploads = parse_backup_archive(zip_bytes)
        assert metadata == {}

    def test_missing_metadata_raises_when_required(self):
        zip_bytes = _make_zip({"openhangar.sql": _VALID_SQL})
        with pytest.raises(
            BackupArchiveError, match="metadata.json manifest is missing"
        ):
            parse_backup_archive(zip_bytes, require_metadata=True)

    def test_not_a_zip_file_raises(self):
        with pytest.raises(BackupArchiveError, match="not a valid zip archive"):
            parse_backup_archive(b"not a zip at all")

    def test_missing_sql_entry_raises(self):
        zip_bytes = _make_zip({"metadata.json": "{}"})
        with pytest.raises(BackupArchiveError, match="openhangar.sql is missing"):
            parse_backup_archive(zip_bytes)

    def test_sql_not_looking_like_a_dump_raises(self):
        zip_bytes = _make_zip({"openhangar.sql": b"garbage, not sql"})
        with pytest.raises(BackupArchiveError, match="does not look like a pg_dump"):
            parse_backup_archive(zip_bytes)

    def test_invalid_json_metadata_raises(self):
        zip_bytes = _make_zip(
            {"openhangar.sql": _VALID_SQL, "metadata.json": b"{not json"}
        )
        with pytest.raises(BackupArchiveError, match="not valid JSON"):
            parse_backup_archive(zip_bytes)

    def test_non_dict_json_metadata_raises(self):
        zip_bytes = _make_zip(
            {"openhangar.sql": _VALID_SQL, "metadata.json": json.dumps([1, 2, 3])}
        )
        with pytest.raises(BackupArchiveError, match="not a JSON object"):
            parse_backup_archive(zip_bytes)

    def test_crc_failure_raises(self):
        zip_bytes = bytearray(
            _make_zip({"openhangar.sql": _VALID_SQL, "metadata.json": "{}"})
        )
        # Flip a byte in the middle of the compressed data (past the local
        # file headers) to corrupt CRC without breaking the zip structure.
        zip_bytes[40] ^= 0xFF
        with pytest.raises(BackupArchiveError):
            parse_backup_archive(bytes(zip_bytes))
