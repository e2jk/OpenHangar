"""J9 — Backup, wipe, restore (docs/functional_test_plan.md).

Intent per the plan: build a small world via routes, back it up, wipe the
DB and upload dir, restore, then log in again and re-read dashboard
hours / logbook / document bytes / expenses to prove the restore is
usable, not just structurally correct.

Documented deviation (the plan's own "deviate only with a documented
reason" rule): the "re-read dashboard hours / logbook / expenses" half
of that intent is not achievable against this test suite's SQLite DB.
`run_backup()`'s SQL dump is `pg_dump` (Postgres-only, verified in
app/config/routes.py._pg_dump: raises RuntimeError for any non-postgres
URL), and the CLI `restore-backup` command's schema-drop-and-reload is
`psql`-only (app/init.py._drop_and_restore_schema). test_backup.py's own
suite -- the existing coverage this journey is supposed to extend --
only ever exercises both by faking the DB URL string and mocking
subprocess.run / _drop_and_restore_schema; there is no code path in
this codebase that dumps and reloads real row data against SQLite, in
tests or otherwise. That means the DB content itself never round-trips
here, in any test environment available to this suite -- asserting
"dashboard hours matched" after a mocked SQL restore would be asserting
against data that was never actually serialized, which is worse than
not testing it at all.

What genuinely does round-trip through this route + this CLI command,
unmocked, and is new relative to test_backup.py's structural/row-count
checks: an uploaded document's *file* content, byte-for-byte, through a
real backup produced by POSTing the real route and a real restore
invoked via the real CLI command (chaining backup-route -> restore-CLI
in one test is itself new; test_backup.py always tests them as two
independently-mocked halves). That is what this journey proves.
"""

import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from config.routes import _derive_key  # pyright: ignore[reportMissingImports]
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
from models import BackupRecord, Document, db  # pyright: ignore[reportMissingImports]

from tests.functional.conftest import submit

_ENCRYPTION_KEY = "j9-test-backup-key"
_FAKE_POSTGRES_URL = "postgresql://u:p@h/db"
# Must start with the real pg_dump marker — restore_backup_command now
# validates this too (via services.backup_format.parse_backup_archive),
# closing a gap where a malformed/non-SQL "openhangar.sql" entry would
# previously only be caught by verification, not by an actual restore.
_FAKE_SQL_DUMP = b"-- PostgreSQL database dump\nSELECT 1;\n"


def _mock_pg_dump() -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stdout = _FAKE_SQL_DUMP
    return result


def test_backup_restore_document_round_trip(owner_env, app):
    client = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # One document, one expense -- the plan's "small world" via routes.
    doc_bytes = b"%PDF-1.4 fake certificate content for J9\n"
    submit(
        client,
        f"/aircraft/{aircraft_id}/documents/upload",
        {
            "file": (BytesIO(doc_bytes), "certificate.pdf"),
            "doc_type": "insurance",
        },
        content_type="multipart/form-data",
    )
    submit(
        client,
        f"/aircraft/{aircraft_id}/expenses/add",
        {
            "date": "2024-06-01",
            "expense_type": "fuel",
            "expense_category": "operating",
            "amount": "50.00",
        },
    )

    with app.app_context():
        doc = Document.query.filter_by(aircraft_id=aircraft_id).one()
        stored_filename = (
            doc.filename
        )  # relative to UPLOAD_FOLDER, e.g. "doc_ac1_....pdf"

    backup_dir = tempfile.mkdtemp()
    try:
        # BACKUP_FOLDER stays patched for both steps below: restore also
        # writes to it (a pre-restore snapshot of the uploads it's about to
        # replace), not just the backup route.
        with patch.dict(app.config, {"BACKUP_FOLDER": backup_dir}):
            with (
                patch.dict(app.config, {"SQLALCHEMY_DATABASE_URI": _FAKE_POSTGRES_URL}),
                patch.dict(
                    os.environ, {"OPENHANGAR_BACKUP_ENCRYPTION_KEY": _ENCRYPTION_KEY}
                ),
                patch("config.routes.subprocess.run", return_value=_mock_pg_dump()),
            ):
                resp = submit(client, "/config/run", {})
                assert b"Backup completed" in resp.data

            with app.app_context():
                record = BackupRecord.query.order_by(BackupRecord.id.desc()).first()
                assert record is not None and record.status == "ok"
                archive_path = record.path

            # Independently decrypt + unzip (no restore involved yet) to prove
            # the document's bytes survived the real backup route
            # byte-for-byte.
            with open(archive_path, "rb") as fh:
                payload = fh.read()
            key = _derive_key(_ENCRYPTION_KEY)
            nonce, ct = payload[:12], payload[12:]
            zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                assert zf.read(f"uploads/{stored_filename}") == doc_bytes

            # Wipe: restore-backup refuses to run against a non-empty DB (a
            # deliberate safety check, app/init.py), so this is a required
            # step, not just the plan's flavour text. Same truncation
            # technique as tests/conftest.py's clean_db fixture, run early
            # rather than at teardown since this test needs the DB empty
            # *during* its own body.
            with app.app_context():
                db.session.remove()
                for table in reversed(db.metadata.sorted_tables):
                    db.session.execute(table.delete())
                db.session.commit()
            upload_folder = app.config["UPLOAD_FOLDER"]
            for name in os.listdir(upload_folder):
                os.unlink(os.path.join(upload_folder, name))

            with (
                patch.dict(app.config, {"SQLALCHEMY_DATABASE_URI": _FAKE_POSTGRES_URL}),
                patch.dict(
                    os.environ, {"OPENHANGAR_RESTORE_ENCRYPTION_KEY": _ENCRYPTION_KEY}
                ),
                patch("init._drop_and_restore_schema"),
            ):
                result = app.test_cli_runner().invoke(
                    args=["restore-backup", archive_path]
                )
            assert result.exit_code == 0, result.output
            assert "Restore complete" in result.output

            # The one thing that genuinely round-tripped through wipe +
            # restore: the document's bytes, at the same relative path.
            restored_path = os.path.join(upload_folder, stored_filename)
            assert os.path.exists(restored_path)
            with open(restored_path, "rb") as fh:
                assert fh.read() == doc_bytes
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
