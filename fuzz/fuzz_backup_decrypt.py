"""Fuzz the encrypted-backup decrypt path (services/backup_verification.py).

fuzz_backup_format.py already fuzzes the *decrypted* zip archive parser.
Nothing feeds _decrypt_if_needed truncated/bit-flipped/oversized ciphertext
directly — a corrupted backup file (disk error, partial download,
tampering) must always come back as a clean BackupVerificationError, never
an unhandled cryptography exception, even though _decrypt_if_needed
already wraps AESGCM.decrypt in a broad except.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "OPENHANGAR_BACKUP_ENCRYPTION_KEY", "fuzz-harness-passphrase-not-a-real-secret"
)

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["services.backup_verification"]):
    from services.backup_verification import (  # noqa: E402
        BackupVerificationError,
        _decrypt_if_needed,
    )


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    payload = fdp.ConsumeBytes(fdp.remaining_bytes())

    try:
        result = _decrypt_if_needed(payload, "backup.zip.enc")
    except BackupVerificationError:
        return  # expected: corrupted/truncated ciphertext rejected cleanly

    assert isinstance(result, bytes), f"unexpected return type: {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
