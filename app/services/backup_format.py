"""Shared parsing for decrypted backup archive contents.

Used by both the restore path (init.py's ``restore-backup`` CLI command)
and the automated restore-verification path (backup_verification.py) so
malformed-archive handling can't diverge between them. Before this was
extracted, ``restore_backup_command`` parsed the same zip structure inline
with no error handling at all (an unhandled ``zipfile.BadZipFile`` or
``KeyError`` on a missing ``openhangar.sql`` entry would crash the CLI
command with a raw traceback), while ``verify_backup_record`` already
handled both cases cleanly — this closes that gap rather than leaving the
two paths to drift.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from typing import Any

# pg_dump always opens a plain-text dump with this comment line.
_SQL_DUMP_MARKER = b"-- PostgreSQL database dump"


class BackupArchiveError(Exception):
    """Raised when a decrypted backup archive is malformed or incomplete."""


def parse_backup_archive(
    zip_bytes: bytes, *, require_metadata: bool = False
) -> tuple[dict[str, Any], bytes, list[str]]:
    """Parse a decrypted backup zip's contents.

    Returns ``(metadata, sql_bytes, upload_entries)``. Raises
    ``BackupArchiveError`` describing the first problem found — callers
    only ever need to handle this one exception type, never zipfile's or
    json's own.

    ``metadata.json`` is optional by default (``restore_backup_command``'s
    original behaviour: missing manifest → ``{}``, still restorable).
    Pass ``require_metadata=True`` to reject a missing manifest instead
    (``verify_backup_record``'s stricter original behaviour, since a
    freshly-created backup should always have one).
    """
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            bad_entry = zf.testzip()
            if bad_entry is not None:
                raise BackupArchiveError(f"CRC check failed for {bad_entry!r}")

            names = zf.namelist()

            if "openhangar.sql" not in names:
                raise BackupArchiveError("openhangar.sql is missing from the archive")
            sql_bytes = zf.read("openhangar.sql")
            if _SQL_DUMP_MARKER not in sql_bytes[:2048]:
                raise BackupArchiveError(
                    "openhangar.sql does not look like a pg_dump SQL dump"
                )

            metadata: dict[str, Any] = {}
            if "metadata.json" in names:
                try:
                    parsed = json.loads(zf.read("metadata.json"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BackupArchiveError(
                        f"metadata.json manifest is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise BackupArchiveError(
                        "metadata.json manifest is not a JSON object"
                    )
                metadata = parsed
            elif require_metadata:
                raise BackupArchiveError(
                    "metadata.json manifest is missing from the archive"
                )

            upload_entries = [n for n in names if n.startswith("uploads/")]
    except zipfile.BadZipFile as exc:
        raise BackupArchiveError(f"not a valid zip archive: {exc}") from exc

    return metadata, sql_bytes, upload_entries
