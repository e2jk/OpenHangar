"""Automated restore-verification for the daily backup (INFRA-06, part 3).

After each successful scheduled backup, decrypt (if encrypted) the archive
that was just written, and check it is well-formed: every zip entry's CRC
checks out, ``openhangar.sql`` is present and looks like real pg_dump
output, and ``metadata.json`` (the backup's manifest — app version, Alembic
head, creation time) is present and parses. Failure fires a [SECURITY]
alert through the existing ntfy/email/webhook channels so operators find
out immediately, not the next time they actually need the backup.

This only proves the archive is intact and decryptable — it does not spin
up a scratch database and replay the SQL. That heavier check stays a
documented manual quarterly procedure; see docs/backup_restore.md.
"""

import logging
import os

from init import _env_or_file  # pyright: ignore[reportMissingImports]
from models import BackupRecord  # pyright: ignore[reportMissingImports]
from services.backup_format import (  # pyright: ignore[reportMissingImports]
    BackupArchiveError,
    parse_backup_archive,
)

log = logging.getLogger("openhangar.backup")


class BackupVerificationError(Exception):
    """Raised when a backup archive fails integrity verification."""


def _decrypt_if_needed(payload: bytes, filename: str) -> bytes:
    if not filename.endswith(".enc"):
        return payload
    key_raw = _env_or_file("BACKUP_ENCRYPTION_KEY")
    if not key_raw:
        raise BackupVerificationError(
            "archive is encrypted but OPENHANGAR_BACKUP_ENCRYPTION_KEY is not set"
        )
    from config.routes import _derive_key  # pyright: ignore[reportMissingImports]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]

    key = _derive_key(key_raw)
    nonce, ct = payload[:12], payload[12:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as exc:
        raise BackupVerificationError(f"decryption failed: {exc}") from exc


def verify_backup_record(record: BackupRecord) -> None:
    """Decrypt and validate *record*'s archive.

    Raises BackupVerificationError describing the first problem found.
    """
    if not record.path or not os.path.exists(record.path):
        raise BackupVerificationError(f"archive file missing: {record.path!r}")

    with open(record.path, "rb") as fh:
        payload = fh.read()

    zip_bytes = _decrypt_if_needed(payload, os.path.basename(record.path))

    try:
        parse_backup_archive(zip_bytes, require_metadata=True)
    except BackupArchiveError as exc:
        raise BackupVerificationError(str(exc)) from exc


def verify_and_alert(record: BackupRecord) -> bool:
    """Run verify_backup_record(); log + fire a [SECURITY] alert on failure.

    Returns True if verification passed, False otherwise. Never raises —
    a verification bug must not take down the backup scheduler.
    """
    try:
        verify_backup_record(record)
    except BackupVerificationError as exc:
        log.error(
            "[SECURITY] backup.verification_failed backup_id=%s filename=%s reason=%s",
            record.id,
            record.filename,
            exc,
        )
        return False
    except Exception:
        log.exception(
            "[SECURITY] backup.verification_failed backup_id=%s filename=%s reason=unexpected_error",
            record.id,
            record.filename,
        )
        return False
    log.info("Backup verification OK: %s", record.filename)
    return True
