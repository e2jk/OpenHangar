"""Due dates of month and year intervals no later than the calendar deadline

Maintenance intervals are stored in days, a month as 30 and a year as 365,
so an item serviced on 31 January with a "1MO" interval was due 2 March
instead of 28 February. Due dates are now the earlier of the day count and
the calendar months/years (models.next_due_date).

This recomputes the due date of every item whose due date was derived from
its last service (due = last service date + interval), with an interval
that is a multiple of 30 days, when the new date is earlier: no date moves
later. Each moved item gets a line in its notes saying what changed, so the
owner can check it.

Revision ID: c6ab03391954
Revises: ce32ed9d9ea6
Create Date: 2026-11-25 00:00:00.000000
"""

import calendar
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op

revision = "c6ab03391954"
down_revision = "ce32ed9d9ea6"
branch_labels = None
depends_on = None


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _next_due(start: date, days: int) -> date:
    """Only called for multiples of 30 or 365 days (see upgrade)."""
    months = days // 365 * 12 if days % 365 == 0 else days // 30
    return min(start + timedelta(days=days), _add_months(start, months))


def _as_date(value: object) -> date | None:
    if value is None or isinstance(value, date):
        return value  # type: ignore[return-value]
    return date.fromisoformat(str(value)[:10])


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT t.id, t.due_date, t.interval_days, t.notes, "
            "(SELECT MAX(r.performed_at) FROM maintenance_records r "
            "WHERE r.trigger_id = t.id) AS last_done "
            "FROM maintenance_triggers t "
            "WHERE t.due_date IS NOT NULL AND t.interval_days IS NOT NULL "
            "AND t.interval_days > 0"
        )
    ).fetchall()
    for trigger_id, due_raw, days, notes, last_raw in rows:
        due, last_done = _as_date(due_raw), _as_date(last_raw)
        if last_done is None or (days % 30 != 0 and days % 365 != 0):
            continue
        if due != last_done + timedelta(days=days):
            continue  # set by hand, not derived from the last service
        new_due = _next_due(last_done, days)
        if new_due >= due:
            continue
        note = (
            f"Due date moved from {due.isoformat()} to {new_due.isoformat()}: "
            "month and year intervals now follow the calendar."
        )
        bind.execute(
            sa.text(
                "UPDATE maintenance_triggers SET due_date = :due, notes = :notes "
                "WHERE id = :id"
            ).bindparams(sa.bindparam("due", type_=sa.Date())),
            {
                "due": new_due,
                "notes": f"{notes}\n\n{note}" if notes else note,
                "id": trigger_id,
            },
        )


def downgrade() -> None:
    """Moved due dates are not restored: they were later than the deadline."""
