"""Failed sign-in counters shared by every worker, kept across restarts.

Each counter is one row, changed by a single atomic statement: concurrent
failures never lose an increment, and every gunicorn worker sees the same
counts. The database is the only shared store a deployment always has, so no
extra service (such as Redis) is needed for this.

Nothing sleeps in a serving thread: a client that must wait is answered at
once (HTTP 429) until its `blocked_until` time has passed.
"""

from __future__ import annotations

import time
from typing import Any

from models import LoginThrottle, db
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def _now() -> int:
    return int(time.time())


def record_failure(key: str, window: int) -> int:
    """Count one failure for `key`; the count restarts after `window` seconds
    without failures. Returns the new count."""
    purge_expired()
    now = _now()
    table = LoginThrottle.__table__
    insert = (
        pg_insert
        if db.session.get_bind().dialect.name == "postgresql"
        else sqlite_insert
    )
    expired = table.c.window_expires_at <= now
    statement = (
        insert(table)
        .values(key=key, failures=1, window_expires_at=now + window, blocked_until=None)
        .on_conflict_do_update(
            index_elements=[table.c.key],
            set_={
                "failures": case((expired, 1), else_=table.c.failures + 1),
                "window_expires_at": now + window,
                "blocked_until": case((expired, None), else_=table.c.blocked_until),
            },
        )
        .returning(table.c.failures)
    )
    failures = int(db.session.execute(statement).scalar_one())
    db.session.commit()
    return failures


def block(key: str, seconds: int, *, reset: bool = False) -> int:
    """Refuse `key` for `seconds` from now; returns the end time. `reset`
    also restarts its failure count (an account lock starts afresh)."""
    until = _now() + seconds
    values: dict[Any, Any] = {
        "blocked_until": until,
        # Keep the row (and its count) at least as long as the block.
        "window_expires_at": case(
            (LoginThrottle.window_expires_at < until, until),
            else_=LoginThrottle.window_expires_at,
        ),
    }
    if reset:
        values["failures"] = 0
    db.session.query(LoginThrottle).filter_by(key=key).update(
        values, synchronize_session=False
    )
    db.session.commit()
    return until


def blocked_for(key: str) -> int:
    """Seconds `key` must still wait, 0 when it may try again."""
    row = db.session.get(LoginThrottle, key, populate_existing=True)
    if row is None or row.blocked_until is None:
        return 0
    return max(0, int(row.blocked_until) - _now())


def clear(*keys: str) -> None:
    db.session.execute(delete(LoginThrottle).where(LoginThrottle.key.in_(keys)))
    db.session.commit()


def purge_expired() -> None:
    """Drop counters whose window and block have both ended."""
    now = _now()
    db.session.execute(
        delete(LoginThrottle).where(
            LoginThrottle.window_expires_at <= now,
            (LoginThrottle.blocked_until.is_(None))
            | (LoginThrottle.blocked_until <= now),
        )
    )
    db.session.commit()
