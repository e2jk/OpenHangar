"""Fuzz the row-reconciliation logic in flights/airframe_import.py.

Byte-level parsing of an uploaded airframe logbook file is already fuzzed
via pilots.logbook_import (fuzz_logbook_parse_file.py /
fuzz_logbook_value_parsers.py), which this module reuses wholesale. What
isn't covered is this module's own transformation of already-parsed rows:
a spreadsheet cell can legitimately come back as str, int, float, bool,
datetime, or timedelta (openpyxl's own type range), and
_parse_row_date/_build_airframe_fields/_clean_icao/_is_subtotal/
_score_airframe_candidate must handle every one of those without raising,
since a single bad historical logbook row must never 500 the whole import.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["flights.airframe_import"]):
    from flights.airframe_import import (
        AIRFRAME_TARGET_FIELDS,
        _build_airframe_fields,
        _clean_icao,
        _is_subtotal,
        _parse_row_date,
        _score_airframe_candidate,
    )

# One column per target field, in AIRFRAME_TARGET_FIELDS order — every
# field mapped (none "ignore"), so every parser branch in
# _build_airframe_fields gets exercised on each fuzzed row.
_MAPPING = {col: col for col in AIRFRAME_TARGET_FIELDS}
_COL_INDEX = {col: i for i, col in enumerate(AIRFRAME_TARGET_FIELDS)}
_DATE_IDX = _COL_INDEX["date"]


def _fuzzed_cell(fdp: "atheris.FuzzedDataProvider") -> Any:
    """One value in the range openpyxl/csv actually hands back for a cell."""
    kind = fdp.ConsumeIntInRange(0, 6)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeUnicodeNoSurrogates(24)
    if kind == 2:
        return fdp.ConsumeIntInRange(-(10**9), 10**9)
    if kind == 3:
        return fdp.ConsumeRegularFloat()
    if kind == 4:
        try:
            return datetime(
                fdp.ConsumeIntInRange(1, 9999),
                fdp.ConsumeIntInRange(1, 12),
                fdp.ConsumeIntInRange(1, 28),
            )
        except ValueError:
            return None
    if kind == 5:
        return timedelta(seconds=fdp.ConsumeIntInRange(-(10**7), 10**7))
    return fdp.ConsumeBool()


def _fuzzed_number_or_none(fdp: "atheris.FuzzedDataProvider") -> float | None:
    """Stand-in for a Numeric/Decimal ORM column value — never a raw cell;
    the real `existing` argument is always a persisted Flight row."""
    if fdp.ConsumeBool():
        return None
    return fdp.ConsumeRegularFloat()


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    row = [_fuzzed_cell(fdp) for _ in AIRFRAME_TARGET_FIELDS]

    is_sub = _is_subtotal(row, _DATE_IDX)
    assert isinstance(is_sub, bool), f"_is_subtotal returned {is_sub!r}"

    icao = _clean_icao(_fuzzed_cell(fdp))
    assert isinstance(icao, str) and 1 <= len(icao) <= 4, (
        f"_clean_icao returned {icao!r}"
    )

    date_val = _parse_row_date(row, _MAPPING, _COL_INDEX)
    assert date_val is None or isinstance(date_val, date), (
        f"_parse_row_date returned {date_val!r}"
    )
    if date_val is None:
        return

    fields, crew_name, warnings = _build_airframe_fields(
        row, _MAPPING, _COL_INDEX, date_val
    )
    assert isinstance(fields, dict), f"_build_airframe_fields fields: {fields!r}"
    assert crew_name is None or isinstance(crew_name, str)
    assert isinstance(warnings, list)
    assert fields["date"] == date_val

    existing = SimpleNamespace(
        departure_icao=_clean_icao(_fuzzed_cell(fdp)),
        arrival_icao=_clean_icao(_fuzzed_cell(fdp)),
        departure_time=None,
        arrival_time=None,
        flight_time=_fuzzed_number_or_none(fdp),
        landing_count=_fuzzed_number_or_none(fdp),
        flight_time_counter_end=_fuzzed_number_or_none(fdp),
    )
    score = _score_airframe_candidate(fields, existing)
    assert isinstance(score, int) and 0 <= score <= 7, (
        f"_score_airframe_candidate returned {score!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
