"""Fuzz the Expense form-field parser (expenses/form_parsing.py).

parse_expense_fields() is the validator behind both the "add expense" and
"edit expense" routes — it must never raise on arbitrary HTTP form data,
only return (values, error). Unlike flights/pilots/maintenance's
form_parsing modules, this one returns a single error string (or None) on
the first problem found rather than an accumulated list — the harness
checks the contract that matters either way: never raise, and whichever
branch wins, the two return slots are mutually exclusive in the right way.
"""

import sys
from datetime import date
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["expenses.form_parsing"]):
    from expenses.form_parsing import parse_expense_fields  # noqa: E402

_FIELD_KEYS = (
    "date",
    "expense_type",
    "expense_category",
    "description",
    "amount",
    "currency",
    "quantity",
    "unit",
    "coverage_start",
    "coverage_end",
    "recurrence",
    "recurrence_end",
)


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    form = {key: fdp.ConsumeUnicodeNoSurrogates(20) for key in _FIELD_KEYS}

    values, error = parse_expense_fields(form)

    if error is not None:
        assert isinstance(error, str)
        assert values == {}
        return

    assert isinstance(values, dict)
    assert isinstance(values["date"], date)
    assert values["amount"] is None or (
        isinstance(values["amount"], float) and values["amount"] >= 0
    )
    if values["quantity"] is not None:
        assert isinstance(values["quantity"], float) and values["quantity"] >= 0
    for key in ("coverage_start", "coverage_end", "recurrence_end"):
        assert values[key] is None or isinstance(values[key], date)


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
