"""Fuzz the offline-sync malformed-body checks (offline/routes.py).

_malformed_sync_body/_malformed_linked_pilot_body/_malformed_pilot_sync_body
are the hand-rolled validators gating every offline sync API request body
before any field is trusted enough to index into (manual type coercion
following request.get_json()). Feeds fuzzed text through json.loads() so
`fields`/`base` cover the full range of JSON value shapes a real request
body's "fields"/"base" keys could hold (dict, list, str, number, bool,
null), not just already-well-formed dicts.
"""

import json
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["offline.routes"]):
    from offline.routes import (  # noqa: E402
        _malformed_linked_pilot_body,
        _malformed_pilot_sync_body,
        _malformed_sync_body,
    )

_SENTINEL = object()


def _fuzzed_json(fdp: "atheris.FuzzedDataProvider", length: int) -> object:
    text = fdp.ConsumeUnicodeNoSurrogates(length)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _SENTINEL


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    fields = _fuzzed_json(fdp, 96)
    base = _fuzzed_json(fdp, 96)
    if fields is _SENTINEL or base is _SENTINEL:
        return  # not valid JSON — request.get_json(silent=True) itself would
        # already have rejected the whole body before these functions ever see it

    for check in (
        _malformed_sync_body,
        _malformed_linked_pilot_body,
        _malformed_pilot_sync_body,
    ):
        result = check(fields, base)
        assert isinstance(result, bool), f"{check.__name__} returned {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
