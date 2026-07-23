"""Fuzz the decrypted-backup-archive parser (services/backup_format.py).

Untrusted-file-input surface: a backup archive is, by definition, a file
whose contents are only as trustworthy as wherever it was stored/restored
from — a truncated, corrupted, or hand-edited archive should fail cleanly
with BackupArchiveError, never crash with a raw zipfile/json exception.
This is what closed the original restore_backup_command gap (see
services/backup_format.py's module docstring): that function used to parse
this same structure inline with no error handling at all.
"""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["services.backup_format"]):
    from services.backup_format import (  # noqa: E402
        BackupArchiveError,
        parse_backup_archive,
    )


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    require_metadata = fdp.ConsumeBool()
    zip_bytes = fdp.ConsumeBytes(fdp.remaining_bytes())

    try:
        metadata, sql_bytes, upload_entries = parse_backup_archive(
            zip_bytes, require_metadata=require_metadata
        )
    except BackupArchiveError:
        return  # expected: malformed/incomplete archive rejected cleanly

    assert isinstance(metadata, dict), f"unexpected metadata type: {metadata!r}"
    assert isinstance(sql_bytes, bytes), f"unexpected sql_bytes type: {sql_bytes!r}"
    assert isinstance(upload_entries, list)
    assert all(isinstance(e, str) and e.startswith("uploads/") for e in upload_entries)


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
