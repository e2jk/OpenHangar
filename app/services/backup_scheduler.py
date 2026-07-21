"""Built-in daily backup scheduling and retention.

OPENHANGAR_BACKUP_TIME (HH:MM UTC, empty = disabled) runs the existing
run_backup() from a daemon thread once a day, guarded by the same advisory
lock mechanism as the other schedulers so only one gunicorn worker produces
a ZIP per tick.  After every *successful* backup, retention prunes per
OPENHANGAR_BACKUP_RETENTION: 'simple' (default) keeps the newest
OPENHANGAR_BACKUP_KEEP backups; 'gfs' keeps everything for
OPENHANGAR_BACKUP_KEEP_DAYS days, then the newest backup per week for
OPENHANGAR_BACKUP_KEEP_WEEKS weeks, per month for
OPENHANGAR_BACKUP_KEEP_MONTHS months, then per year forever.  A failed
backup never triggers pruning, so a broken pipeline cannot silently erase
the archives that still exist.
"""

import logging
import os
from datetime import date, timedelta
from typing import Any

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


def _parse_positive_int(env_name: str, default: int) -> int:
    """Positive-integer env var with a default; ValueError on bad values."""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    err = f"{env_name}={raw!r} is invalid — expected a positive integer"
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(err)
    if value < 1:
        raise ValueError(err)
    return value


def parse_backup_keep() -> int:
    """Retention count for the 'simple' scheme (OPENHANGAR_BACKUP_KEEP)."""
    return _parse_positive_int("OPENHANGAR_BACKUP_KEEP", DEFAULT_KEEP)


RETENTION_SIMPLE = "simple"
RETENTION_GFS = "gfs"

DEFAULT_KEEP_DAYS = 7
DEFAULT_KEEP_WEEKS = 4
DEFAULT_KEEP_MONTHS = 12


def parse_backup_retention() -> str:
    """Retention scheme from OPENHANGAR_BACKUP_RETENTION (default 'simple').

    'simple' keeps the newest OPENHANGAR_BACKUP_KEEP backups; 'gfs' keeps
    everything for OPENHANGAR_BACKUP_KEEP_DAYS days, then the newest backup
    per week for OPENHANGAR_BACKUP_KEEP_WEEKS weeks, per month for
    OPENHANGAR_BACKUP_KEEP_MONTHS months, and per year forever.
    """
    raw = os.environ.get("OPENHANGAR_BACKUP_RETENTION", "").strip().lower()
    if not raw:
        return RETENTION_SIMPLE
    if raw not in (RETENTION_SIMPLE, RETENTION_GFS):
        raise ValueError(
            f"OPENHANGAR_BACKUP_RETENTION={raw!r} is invalid — expected "
            f"'{RETENTION_SIMPLE}' or '{RETENTION_GFS}'"
        )
    return raw


def parse_backup_keep_days() -> int:
    return _parse_positive_int("OPENHANGAR_BACKUP_KEEP_DAYS", DEFAULT_KEEP_DAYS)


def parse_backup_keep_weeks() -> int:
    return _parse_positive_int("OPENHANGAR_BACKUP_KEEP_WEEKS", DEFAULT_KEEP_WEEKS)


def parse_backup_keep_months() -> int:
    return _parse_positive_int("OPENHANGAR_BACKUP_KEEP_MONTHS", DEFAULT_KEEP_MONTHS)


def _gfs_keep_ids(
    ok_records: "list[Any]", today: "date", days: int, weeks: int, months: int
) -> "set[int]":
    """Grandfather-father-son keep-set over newest-first successful backups.

    Everything younger than `days` days is kept.  Beyond that, the newest
    backup of each ISO week is kept for the first `weeks` distinct weeks,
    then the newest per calendar month for `months` distinct months, then
    the newest per calendar year forever.  Counting distinct periods (like
    restic's --keep-weekly) means gaps in the schedule never shrink the
    retained history.
    """
    keep: set[int] = set()
    weeks_seen: set[tuple[int, int]] = set()
    months_seen: set[tuple[int, int]] = set()
    years_seen: set[int] = set()
    daily_cutoff = today - timedelta(days=days)
    for record in ok_records:
        created = record.created_at.date()
        if created >= daily_cutoff:
            keep.add(record.id)
            continue
        iso = created.isocalendar()
        wkey = (iso[0], iso[1])
        if wkey in weeks_seen:
            continue  # this week is already represented by a newer backup
        if len(weeks_seen) < weeks:
            weeks_seen.add(wkey)
            keep.add(record.id)
            continue
        mkey = (created.year, created.month)
        if mkey in months_seen:
            continue
        if len(months_seen) < months:
            months_seen.add(mkey)
            keep.add(record.id)
            continue
        if created.year not in years_seen:
            years_seen.add(created.year)
            keep.add(record.id)
    return keep


def prune_old_backups(keep: "int | None" = None, today: "date | None" = None) -> int:
    """Delete successful backups that fall outside the retention scheme.

    With an explicit `keep` (or OPENHANGAR_BACKUP_RETENTION=simple, the
    default) the newest `keep` backups survive.  With the 'gfs' scheme the
    day/week/month/year tiers decide.  Returns the number of records
    removed.  A record whose file cannot be deleted is kept so the operator
    can still see (and clean up) the stranded archive.  Failed records
    carry no archive and are left alone.
    """
    from models import BackupRecord, db  # pyright: ignore[reportMissingImports]

    ok_records = (
        BackupRecord.query.filter_by(status="ok")
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
        .all()
    )
    if keep is None and parse_backup_retention() == RETENTION_GFS:
        keep_ids = _gfs_keep_ids(
            ok_records,
            today or date.today(),
            parse_backup_keep_days(),
            parse_backup_keep_weeks(),
            parse_backup_keep_months(),
        )
        to_delete = [r for r in ok_records if r.id not in keep_ids]
    else:
        if keep is None:
            keep = parse_backup_keep()
        to_delete = ok_records[keep:]

    removed = 0
    for record in to_delete:
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
                from services.backup_verification import verify_and_alert  # pyright: ignore[reportMissingImports]

                verify_and_alert(record)
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
