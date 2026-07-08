"""Materialise recurring fixed-cost expenses (hangar rent, insurance, …).

A template expense (Expense.recurrence set) describes the recurring cost.
The daily pass creates an ordinary, editable Expense row for every period
whose date has arrived, copying the template's current amount/type/category
and advancing the coverage span — so editing the template changes future
occurrences only.

Occurrence dates are derived from the template date by whole-month
arithmetic (index-based, not cursor-based), so a template dated Jan 31
yields Feb 28/29 and then Mar 31 — no end-of-month drift.  The
recurrence_last_date cursor on the template records how far materialisation
has progressed; deleting a generated row therefore never resurrects it.
"""

import calendar
import logging
from datetime import date

log = logging.getLogger(__name__)


def _add_months(d: date, months: int) -> date:
    """d shifted by `months` whole months, day clamped to the target month."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def materialize_recurring_expenses(today: date | None = None) -> int:
    """Create the concrete Expense rows for every due recurrence period.

    Returns the number of rows created.  Skips templates on archived
    aircraft.  Must run under the daily-checks advisory lock so only one
    gunicorn worker materialises per day.
    """
    from models import Aircraft, Expense, ExpenseRecurrence, db

    if today is None:
        today = date.today()

    created = 0
    templates = (
        Expense.query.join(Aircraft, Expense.aircraft_id == Aircraft.id)
        .filter(Expense.recurrence.isnot(None), Aircraft.archived_at.is_(None))
        .all()
    )
    for tpl in templates:
        months = ExpenseRecurrence.MONTHS.get(tpl.recurrence)
        if months is None:
            log.warning(
                "Expense %s has unknown recurrence %r — skipping",
                tpl.id,
                tpl.recurrence,
            )
            continue
        # Periods already materialised, derived from the cursor.
        k = (
            0
            if tpl.recurrence_last_date is None
            else max(
                0, round(_months_between(tpl.date, tpl.recurrence_last_date) / months)
            )
        )
        while True:
            k += 1
            next_date = _add_months(tpl.date, k * months)
            if next_date > today:
                break
            if tpl.recurrence_end is not None and next_date > tpl.recurrence_end:
                break
            db.session.add(
                Expense(
                    aircraft_id=tpl.aircraft_id,
                    date=next_date,
                    expense_type=tpl.expense_type,
                    expense_category=tpl.expense_category,
                    description=tpl.description,
                    amount=tpl.amount,
                    currency=tpl.currency,
                    quantity=tpl.quantity,
                    unit=tpl.unit,
                    coverage_start=_add_months(tpl.coverage_start, k * months)
                    if tpl.coverage_start
                    else None,
                    coverage_end=_add_months(tpl.coverage_end, k * months)
                    if tpl.coverage_end
                    else None,
                    recurring_template_id=tpl.id,
                )
            )
            tpl.recurrence_last_date = next_date
            created += 1
    if created:
        log.info("Materialised %d recurring expense(s)", created)
    db.session.commit()
    return created
