"""Fuzz the PilotLogbookEntry form-field parsers (pilots/form_parsing.py).

parse_pilot_fields()/parse_linked_pilot_fields() are the shared validators
behind the online pilot-logbook form and the offline sync API — neither
should ever raise on arbitrary HTTP form data, only return (values, errors).
"""

import sys
from datetime import date, time
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# include= scopes instrumentation to just this module — see
# fuzz_flight_form_parsing.py for the measured setup-time win.
with atheris.instrument_imports(include=["pilots.form_parsing"]):
    from pilots.form_parsing import (  # noqa: E402
        parse_linked_pilot_fields,
        parse_pilot_fields,
    )

_STANDALONE_KEYS = (
    "entry_type",
    "fstd_type",
    "fstd_duration",
    "date",
    "aircraft_type",
    "aircraft_type_icao",
    "aircraft_registration",
    "departure_place",
    "departure_time",
    "arrival_place",
    "arrival_time",
    "pic_name",
    "night_time",
    "instrument_time",
    "landings_day",
    "landings_night",
    "single_pilot_se",
    "single_pilot_me",
    "multi_pilot",
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
    "remarks",
)

_LINKED_KEYS = (
    "night_time",
    "instrument_time",
    "landings_day",
    "landings_night",
    "multi_pilot",
    "pic_name",
    "departure_time",
    "arrival_time",
)

_DECIMAL_FIELDS = (
    "fstd_duration",
    "night_time",
    "instrument_time",
    "single_pilot_se",
    "single_pilot_me",
    "multi_pilot",
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
)

_INT_FIELDS = ("landings_day", "landings_night")


def _assert_common_invariants(values: dict) -> None:
    if "date" in values:
        assert values["date"] is None or isinstance(values["date"], date)
    for key in ("departure_time", "arrival_time"):
        assert values[key] is None or isinstance(values[key], time)
    for key in _DECIMAL_FIELDS:
        if key in values:
            v = values[key]
            assert v is None or (isinstance(v, float) and v >= 0), (
                f"{key} returned {v!r}"
            )
    for key in _INT_FIELDS:
        v = values[key]
        assert v is None or (isinstance(v, int) and v >= 0), f"{key} returned {v!r}"


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    linked = fdp.ConsumeBool()
    keys = _LINKED_KEYS if linked else _STANDALONE_KEYS
    form = {key: fdp.ConsumeUnicodeNoSurrogates(24) for key in keys}

    if linked:
        values, errors = parse_linked_pilot_fields(form)
    else:
        values, errors = parse_pilot_fields(form)

    assert isinstance(values, dict)
    assert isinstance(errors, list)
    _assert_common_invariants(values)


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
