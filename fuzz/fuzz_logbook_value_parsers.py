"""Fuzz the cell value parsers used to interpret uploaded logbook rows.

These run on every cell of an uploaded CSV/XLSX file (pilots/logbook_import.py)
and must never raise — an unparseable cell should come back as None, not crash
the whole import. Found a real OverflowError in parse_int_value locally before
this was pushed: float("1e400") overflows to inf, and int(inf) raises
OverflowError rather than ValueError. Fixed in logbook_import.py.
"""

import sys
from datetime import date, time
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# instrument_imports() gives Atheris real coverage-guided feedback from inside
# logbook_import.py itself, not just the harness wrapper — see
# fuzz_gps_import.py for the local measurement that motivated this.
with atheris.instrument_imports():
    from pilots.logbook_import import (  # noqa: E402
        parse_date_value,
        parse_duration_value,
        parse_int_value,
        parse_time_value,
    )


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    s = fdp.ConsumeUnicodeNoSurrogates(256)

    d = parse_date_value(s)
    assert d is None or isinstance(d, date), f"parse_date_value returned {d!r}"

    t = parse_time_value(s)
    assert t is None or isinstance(t, time), f"parse_time_value returned {t!r}"

    dur = parse_duration_value(s)
    assert dur is None or (isinstance(dur, float) and dur >= 0), (
        f"parse_duration_value returned {dur!r}"
    )

    n = parse_int_value(s)
    assert n is None or (isinstance(n, int) and n >= 0), (
        f"parse_int_value returned {n!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
