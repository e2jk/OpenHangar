"""Fuzz the free-text aircraft-type-to-ICAO resolver (utils.py).

resolve_aircraft_type_icao() takes an untrusted free-text aircraft type
string — typed directly into a form, or read from an uploaded pilot-logbook/
airframe-import CSV/XLSX "aircraft_type" column — and fuzzy-matches it
against the bundled aircraft_types.csv lookup (prefix stripping, then a
longest-prefix-first scan with a digit-tail guard). Pure string
normalisation and dict lookups against static, trusted data, so the
invariant worth checking is simpler than a "never crashes" one: whatever it
resolves to must actually be a known type designator, not just any string
that happens to satisfy the prefix-matching logic.
"""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["utils"]):
    from utils import _load_aircraft_types, resolve_aircraft_type_icao  # noqa: E402

_KNOWN_TYPES = frozenset(_load_aircraft_types())


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    aircraft_type = fdp.ConsumeUnicodeNoSurrogates(32)

    result = resolve_aircraft_type_icao(aircraft_type)
    assert result is None or (isinstance(result, str) and result in _KNOWN_TYPES), (
        f"resolved to an unknown type designator: {result!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
