"""
PostgreSQL advisory-lock helper for background jobs that must run once per
scheduled tick rather than once per gunicorn worker.

Production runs `gunicorn --workers 4` without `--preload`
(docker/docker-entrypoint.sh), so every scheduler thread started in
create_app() runs independently in all four worker processes. Jobs guarded
here acquire a well-known lock id before doing their work; the first worker
to grab it proceeds, the rest skip that run and try again on the next tick.

The lock is held on a dedicated connection (not the ORM session's pooled
connection), so it survives however many commits the guarded work performs —
acquiring it via the session and relying on `pg_try_advisory_xact_lock`
would release the lock at the *first* commit, letting a second worker start
racing partway through a multi-commit job.

Lock id registry (pick a new one when adding a caller):
  7283910456 — welcome email, startup, one-shot (services/notification_service.py)
  7283910457 — daily notification checks (services/notification_service.py)
  7283910458 — EASA airworthiness sync (airworthiness_sync.py)
  7283910459 — document sync-watcher scan (sync_watcher.py)
  7283910460 — scheduled backup + retention (services/backup_scheduler.py)
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def advisory_lock_scope(db: Any, lock_id: int) -> Iterator[bool]:
    """Yield True if lock_id was acquired for the duration of the `with` block.

    On non-PostgreSQL engines (SQLite in dev/test), always yields True
    without touching the database.
    """
    if db.engine.dialect.name != "postgresql":
        yield True
        return

    from sqlalchemy import text  # pyright: ignore[reportMissingImports]

    conn = db.engine.connect()
    try:
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            ).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                )
    finally:
        conn.close()
