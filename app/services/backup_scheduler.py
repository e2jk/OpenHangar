"""Built-in daily backup scheduling and retention.

OPENHANGAR_BACKUP_TIME (HH:MM UTC, empty = disabled) runs the existing
run_backup() from a daemon thread once a day, guarded by the same advisory
lock mechanism as the other schedulers so only one gunicorn worker produces
a ZIP per tick.  After every *successful* backup, retention keeps the newest
OPENHANGAR_BACKUP_KEEP (default 30) successful backups and deletes older
records and their files — a failed backup never triggers pruning, so a
broken pipeline cannot silently erase the archives that still exist.
"""

import logging
import os

log = logging.getLogger(__name__)

# See the lock id registry in services/advisory_lock.py.
BACKUP_LOCK_ID = 7283910460

DEFAULT_KEEP = 30


def parse_backup_time() -> "tuple[int, int] | None":
    """Return (hour, minute) from OPENHANGAR_BACKUP_TIME, or None when unset.

    Raises ValueError with a human-readable message if the value is invalid.
    """
    raw = os.environ.get("OPENHANGAR_BACKUP_TIME", "").strip()
    if not raw:
        return None
    err = (
        f"OPENHANGAR_BACKUP_TIME={raw!r} is invalid — expected HH:MM UTC "
        f"(e.g. '03:30'), or empty to disable scheduled backups"
    )
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(err)
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(err)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(err)
    return hour, minute


def parse_backup_keep() -> int:
    """Return the retention count from OPENHANGAR_BACKUP_KEEP (default 30).

    Raises ValueError with a human-readable message if the value is invalid.
    """
    raw = os.environ.get("OPENHANGAR_BACKUP_KEEP", "").strip()
    if not raw:
        return DEFAULT_KEEP
    err = f"OPENHANGAR_BACKUP_KEEP={raw!r} is invalid — expected a positive integer"
    try:
        keep = int(raw)
    except ValueError:
        raise ValueError(err)
    if keep < 1:
        raise ValueError(err)
    return keep


def prune_old_backups(keep: "int | None" = None) -> int:
    """Delete the oldest successful backups beyond the retention count.

    Returns the number of records removed.  A record whose file cannot be
    deleted is kept so the operator can still see (and clean up) the
    stranded archive.  Failed records carry no archive and are left alone.
    """
    from models import BackupRecord, db  # pyright: ignore[reportMissingImports]

    if keep is None:
        keep = parse_backup_keep()
    ok_records = (
        BackupRecord.query.filter_by(status="ok")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        .all()
    )
    removed = 0
    for record in ok_records[keep:]:
        try:
            if record.path and os.path.exists(record.path):
                os.remove(record.path)
        except OSError:
            log.warning(
                "Backup retention: could not delete %s — keeping its record",
                record.path,
            )
            continue
        db.session.delete(record)
        removed += 1
    db.session.commit()
    return removed


def run_scheduled_backup(app: "object") -> None:
    """One scheduled tick: back up, then prune retention on success only."""
    from models import db  # pyright: ignore[reportMissingImports]
    from services.advisory_lock import advisory_lock_scope  # pyright: ignore[reportMissingImports]

    with app.app_context():  # type: ignore[attr-defined]
        try:
            with advisory_lock_scope(db, BACKUP_LOCK_ID) as acquired:
                if not acquired:
                    log.info(
                        "Scheduled backup: another worker holds the lock — skipping"
                    )
                    return
                from config.routes import run_backup  # pyright: ignore[reportMissingImports]

                try:
                    record = run_backup()
                except RuntimeError:
                    log.exception("Scheduled backup failed — retention pruning skipped")
                    return
                log.info("Scheduled backup OK: %s", record.filename)
                removed = prune_old_backups()
                if removed:
                    log.info("Backup retention: pruned %d old backup(s)", removed)
        except Exception:
            log.exception("Error in scheduled backup run")


def _backup_daily_loop(app: "object", run_hour: int, run_minute: int) -> None:
    import time
    from datetime import datetime, timedelta, timezone

    log.info("Backup scheduled daily at %02d:%02d UTC", run_hour, run_minute)
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(
            hour=run_hour, minute=run_minute, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        time.sleep((next_run - datetime.now(timezone.utc)).total_seconds())
        run_scheduled_backup(app)


def start_backup_scheduler(app: "object") -> None:
    """Start the daily backup thread when OPENHANGAR_BACKUP_TIME is set."""
    import threading

    schedule = parse_backup_time()
    if schedule is None:
        log.info("OPENHANGAR_BACKUP_TIME not set — built-in backup scheduling disabled")
        return
    threading.Thread(
        target=_backup_daily_loop,
        args=(app, schedule[0], schedule[1]),
        daemon=True,
        name="backup-scheduler",
    ).start()
