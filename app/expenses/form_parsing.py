"""Shared validation for the Expense editable field set (excluding receipts).

``parse_expense_fields`` extracts the validation previously inlined
directly in ``_validate_and_save`` (expenses/routes.py), following the
same pattern as flights/form_parsing.py, pilots/form_parsing.py, and
maintenance/form_parsing.py — a standalone, importable function that never
raises on arbitrary form data. Unlike those, this preserves
``_validate_and_save``'s original "return on first error" contract (a
single error message, not an accumulated list) — that's its pre-existing
UX behaviour (only the first problem found is ever shown), not something
this extraction should change. The receipt-file upload isn't included:
it operates on a ``FileStorage`` object and a real ``Expense``/``Aircraft``
row, not string form fields, so it stays in the route.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as _date
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    ExpenseCategory,
    ExpenseRecurrence,
    ExpenseType,
)


def parse_expense_fields(f: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:
    """Validate the editable Expense fields (excluding the receipt upload).

    Returns ``(values, None)`` on success, or ``({}, error_message)`` on the
    first validation failure — mirrors ``_validate_and_save``'s pre-existing
    single-error-at-a-time behaviour exactly.
    """
    date_str = (f.get("date") or "").strip()
    expense_type = (f.get("expense_type") or "").strip()
    expense_category = (f.get("expense_category") or "").strip()
    description = (f.get("description") or "").strip() or None
    amount_str = (f.get("amount") or "").strip()
    currency = (f.get("currency") or "EUR").strip()
    quantity_str = (f.get("quantity") or "").strip()
    unit = (f.get("unit") or "").strip() or None
    coverage_start_str = (f.get("coverage_start") or "").strip()
    coverage_end_str = (f.get("coverage_end") or "").strip()
    recurrence = (f.get("recurrence") or "").strip() or None
    recurrence_end_str = (f.get("recurrence_end") or "").strip()

    if not date_str:
        return {}, str(_("Date is required."))
    try:
        date_val = _date.fromisoformat(date_str)
    except ValueError:
        return {}, str(_("Invalid date format."))

    if expense_type not in ExpenseType.ALL:
        return {}, str(_("Invalid expense type."))

    if not expense_category:
        expense_category = ExpenseCategory.DEFAULTS.get(
            expense_type, ExpenseCategory.OPERATING
        )
    if expense_category not in ExpenseCategory.ALL:
        return {}, str(_("Invalid expense category."))

    if not amount_str:
        return {}, str(_("Amount is required."))
    try:
        amount = float(amount_str)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError
    except ValueError:
        return {}, str(_("Amount must be a non-negative number."))

    quantity = None
    if quantity_str:
        try:
            quantity = float(quantity_str)
            if not math.isfinite(quantity) or quantity < 0:
                raise ValueError
        except ValueError:
            return {}, str(_("Quantity must be a non-negative number."))

    coverage_start = None
    coverage_end = None
    if coverage_start_str or coverage_end_str:
        if not (coverage_start_str and coverage_end_str):
            return {}, str(
                _("Coverage start and end dates must both be set, or both left blank.")
            )
        try:
            coverage_start = _date.fromisoformat(coverage_start_str)
            coverage_end = _date.fromisoformat(coverage_end_str)
        except ValueError:
            return {}, str(_("Invalid coverage date format."))
        if coverage_end < coverage_start:
            return {}, str(_("Coverage end date must not be before the start date."))

    if recurrence is not None and recurrence not in ExpenseRecurrence.ALL:
        return {}, str(_("Invalid recurrence."))
    recurrence_end = None
    if recurrence_end_str:
        if recurrence is None:
            return {}, str(_("A recurrence end date requires a recurrence."))
        try:
            recurrence_end = _date.fromisoformat(recurrence_end_str)
        except ValueError:
            return {}, str(_("Invalid recurrence end date format."))
        if recurrence_end < date_val:
            return {}, str(
                _("The recurrence end date must not be before the expense date.")
            )

    values: dict[str, Any] = {
        "date": date_val,
        "expense_type": expense_type,
        "expense_category": expense_category,
        "description": description,
        "amount": amount,
        "currency": currency,
        "quantity": quantity,
        "unit": unit,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "recurrence": recurrence,
        "recurrence_end": recurrence_end,
    }
    return values, None
