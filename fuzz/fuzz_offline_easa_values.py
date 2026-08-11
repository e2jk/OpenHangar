"""Fuzz the EASA value parsers used by the offline logbook sync API.

_parse_easa_decimal/_parse_easa_int/_apply_easa_fields parse untrusted
`fields`/`base` string values from POST /api/offline/flights/<id>/sync
(offline/routes.py) into Decimal/int Flight columns, once
_malformed_sync_body has confirmed the overall JSON shape is right — see
fuzz_offline_sync_validators.py, which stops at that shape check, before
values ever reach these parsers.

Found a real crash locally before this was pushed: _apply_easa_fields used
`int(raw) if raw.isdigit() else None` for landings_day/landings_night —
but str.isdigit() is True for ~95 Unicode characters (superscripts like
'²', Ethiopic digits, etc.) that int() itself rejects with ValueError.
A landings count of e.g. '²' crashed the sync endpoint instead of
degrading to None like every other malformed EASA field. Fixed by adding
_parse_easa_int, mirroring _parse_easa_decimal's already-safe try/except
shape.
"""

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["offline.routes"]):
    from offline.routes import (
        _EASA_DECIMAL_FIELDS,
        _EASA_INT_FIELDS,
        _apply_easa_fields,
        _parse_easa_decimal,
        _parse_easa_int,
    )


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    s = fdp.ConsumeUnicodeNoSurrogates(64)

    dec = _parse_easa_decimal(s)
    assert dec is None or (isinstance(dec, Decimal) and dec >= 0), (
        f"_parse_easa_decimal returned {dec!r}"
    )

    n = _parse_easa_int(s)
    assert n is None or (isinstance(n, int) and n >= 0), (
        f"_parse_easa_int returned {n!r}"
    )

    effective = {
        key: fdp.ConsumeUnicodeNoSurrogates(32)
        for key in (*_EASA_DECIMAL_FIELDS, *_EASA_INT_FIELDS)
    }
    fe = SimpleNamespace()
    _apply_easa_fields(fe, effective)  # type: ignore[arg-type]  # must never raise
    for key in _EASA_DECIMAL_FIELDS:
        v = getattr(fe, key)
        assert v is None or (isinstance(v, Decimal) and v >= 0)
    for key in _EASA_INT_FIELDS:
        v = getattr(fe, key)
        assert v is None or (isinstance(v, int) and v >= 0)


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
