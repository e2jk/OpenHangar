"""Fuzz the FlightEntry form-field parser (flights/form_parsing.py).

parse_flight_fields() is the shared validator behind both the online flight
form and the offline sync API — it must never raise on arbitrary HTTP form
data, only return (values, errors). Revives the intent of the old
fuzz_numeric_inputs.py harness (date/counter/quantity parsing) without
reimplementing any of it: the function is already standalone and
importable, no refactor needed.
"""

import sys
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# include= scopes instrumentation to just this module rather than every
# transitively-imported one (Flask, Jinja2, the whole of models.py via
# flights.form_parsing's own `from models import ...`) — verified locally
# this cuts one-time setup from ~55s to under 1s with no meaningful loss in
# coverage-guided exploration of the function actually being fuzzed.
with atheris.instrument_imports(include=["flights.form_parsing"]):
    from flights.form_parsing import parse_flight_fields  # noqa: E402

_FIELD_KEYS = (
    "date",
    "departure_icao",
    "arrival_icao",
    "crew_name_0",
    "crew_role_0",
    "crew_name_1",
    "crew_role_1",
    "departure_time",
    "arrival_time",
    "flight_time_counter_start",
    "flight_time_counter_end",
    "engine_time_counter_start",
    "engine_time_counter_end",
    "flight_time",
    "passenger_count",
    "landing_count",
    "fuel_event",
    "fuel_added_qty",
    "fuel_added_unit",
    "fuel_remaining_qty",
    "oil_added_l",
    "nature_of_flight",
    "notes",
)


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    form = {key: fdp.ConsumeUnicodeNoSurrogates(24) for key in _FIELD_KEYS}

    ac: Any = None
    if fdp.ConsumeBool():
        ac = SimpleNamespace(
            has_flight_counter=fdp.ConsumeBool(),
            flight_counter_offset=fdp.ConsumeFloatInRange(0.0, 5.0),
        )

    values, errors = parse_flight_fields(form, ac)

    assert isinstance(values, dict)
    assert isinstance(errors, list)
    assert values["date"] is None or isinstance(values["date"], date)
    assert values["departure_time"] is None or isinstance(
        values["departure_time"], time
    )
    assert values["arrival_time"] is None or isinstance(values["arrival_time"], time)
    for key in (
        "flight_time",
        "flight_time_counter_start",
        "flight_time_counter_end",
        "engine_time_counter_start",
        "engine_time_counter_end",
        "fuel_added_qty",
        "fuel_remaining_qty",
        "oil_added_l",
    ):
        v = values[key]
        assert v is None or (isinstance(v, float) and v >= 0), f"{key} returned {v!r}"
    for key in ("passenger_count", "landing_count"):
        v = values[key]
        assert v is None or (isinstance(v, int) and v >= 0), f"{key} returned {v!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
