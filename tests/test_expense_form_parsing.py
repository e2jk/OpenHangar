"""Direct unit tests for expenses/form_parsing.py.

Extracted from _validate_and_save in expenses/routes.py so it can be fuzzed
directly (fuzz/fuzz_expense_form_parsing.py) — these cover the recurrence
validation branches route-level HTTP tests don't happen to exercise.
"""

from datetime import date

from expenses.form_parsing import parse_expense_fields  # pyright: ignore[reportMissingImports]
from models import ExpenseRecurrence, ExpenseType  # pyright: ignore[reportMissingImports]


def _valid_form(**overrides):
    form = {
        "date": "2026-01-01",
        "expense_type": ExpenseType.FUEL,
        "amount": "100",
    }
    form.update(overrides)
    return form


class TestParseExpenseFields:
    def test_valid_minimal_form(self):
        values, error = parse_expense_fields(_valid_form())
        assert error is None
        assert values["date"] == date(2026, 1, 1)
        assert values["amount"] == 100.0

    def test_missing_date_is_error(self):
        _values, error = parse_expense_fields(_valid_form(date=""))
        assert error is not None and "Date is required" in error

    def test_invalid_date_is_error(self):
        _values, error = parse_expense_fields(_valid_form(date="garbage"))
        assert error is not None and "Invalid date format" in error

    def test_invalid_expense_type_is_error(self):
        _values, error = parse_expense_fields(_valid_form(expense_type="bogus"))
        assert error is not None and "Invalid expense type" in error

    def test_invalid_expense_category_is_error(self):
        _values, error = parse_expense_fields(
            _valid_form(expense_category="not-a-real-category")
        )
        assert error is not None and "Invalid expense category" in error

    def test_missing_amount_is_error(self):
        _values, error = parse_expense_fields(_valid_form(amount=""))
        assert error is not None and "Amount is required" in error

    def test_negative_amount_is_error(self):
        _values, error = parse_expense_fields(_valid_form(amount="-5"))
        assert error is not None and "non-negative number" in error

    def test_non_numeric_amount_is_error(self):
        _values, error = parse_expense_fields(_valid_form(amount="not-a-number"))
        assert error is not None and "non-negative number" in error

    def test_negative_quantity_is_error(self):
        _values, error = parse_expense_fields(_valid_form(quantity="-1"))
        assert error is not None and "Quantity must be" in error

    def test_only_coverage_start_is_error(self):
        _values, error = parse_expense_fields(_valid_form(coverage_start="2026-01-01"))
        assert error is not None and "must both be set" in error

    def test_invalid_coverage_date_is_error(self):
        _values, error = parse_expense_fields(
            _valid_form(coverage_start="garbage", coverage_end="2026-02-01")
        )
        assert error is not None and "Invalid coverage date format" in error

    def test_coverage_end_before_start_is_error(self):
        _values, error = parse_expense_fields(
            _valid_form(coverage_start="2026-02-01", coverage_end="2026-01-01")
        )
        assert error is not None and "must not be before the start" in error

    def test_invalid_recurrence_is_error(self):
        _values, error = parse_expense_fields(_valid_form(recurrence="bogus"))
        assert error is not None and "Invalid recurrence" in error

    def test_recurrence_end_without_recurrence_is_error(self):
        _values, error = parse_expense_fields(_valid_form(recurrence_end="2026-02-01"))
        assert error is not None and "requires a recurrence" in error

    def test_invalid_recurrence_end_format_is_error(self):
        _values, error = parse_expense_fields(
            _valid_form(recurrence=ExpenseRecurrence.MONTHLY, recurrence_end="garbage")
        )
        assert error is not None and "Invalid recurrence end date" in error

    def test_recurrence_end_before_expense_date_is_error(self):
        _values, error = parse_expense_fields(
            _valid_form(
                date="2026-05-01",
                recurrence=ExpenseRecurrence.MONTHLY,
                recurrence_end="2026-01-01",
            )
        )
        assert error is not None and "not be before the expense date" in error

    def test_valid_recurrence_and_end(self):
        values, error = parse_expense_fields(
            _valid_form(
                date="2026-01-01",
                recurrence=ExpenseRecurrence.MONTHLY,
                recurrence_end="2026-12-01",
            )
        )
        assert error is None
        assert values["recurrence_end"] == date(2026, 12, 1)
