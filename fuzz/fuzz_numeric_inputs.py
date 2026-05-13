"""Fuzz numeric input parsing used for hobbs/tach counters and trigger values."""
import sys
import atheris
from datetime import date as _date


def _parse_float_positive(raw: str) -> float | None:
    """Mirrors the float-parsing pattern in maintenance/routes.py and flights/routes.py."""
    try:
        val = float(raw)
        if val < 0:
            raise ValueError
        return val
    except (ValueError, TypeError):
        return None


def _parse_int_positive(raw: str) -> int | None:
    """Mirrors the int-parsing pattern for interval_days in maintenance/routes.py."""
    try:
        val = int(raw)
        if val <= 0:
            raise ValueError
        return val
    except (ValueError, TypeError):
        return None


def _parse_iso_date(raw: str) -> _date | None:
    """Mirrors date.fromisoformat() usage in maintenance/routes.py."""
    try:
        return _date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    raw = fdp.ConsumeUnicodeNoSurrogates(64)

    f = _parse_float_positive(raw)
    assert f is None or f >= 0, f"negative float leaked: {f!r}"

    i = _parse_int_positive(raw)
    assert i is None or i > 0, f"non-positive int leaked: {i!r}"

    _parse_iso_date(raw)  # must not raise


atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
