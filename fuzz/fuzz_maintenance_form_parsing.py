"""Fuzz the MaintenanceTrigger/MaintenanceRecord form-field parsers
(maintenance/form_parsing.py).

parse_trigger_fields()/parse_service_fields() are the validators behind
_save_trigger/service_trigger in maintenance/routes.py — extracted from
those route handlers specifically so they could be fuzzed directly
(the "import the real function, don't reimplement" rule needs a standalone
function to import). Neither should ever raise on arbitrary HTTP form data,
only return (values, errors).
"""

import math
import sys
from datetime import date
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["maintenance.form_parsing"]):
    from maintenance.form_parsing import (
        parse_service_fields,
        parse_trigger_fields,
    )

_TRIGGER_KEYS = (
    "name",
    "trigger_type",
    "due_date",
    "interval_days",
    "due_engine_hours",
    "interval_hours",
    "due_landings",
    "interval_landings",
    "notes",
)


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    form = {key: fdp.ConsumeUnicodeNoSurrogates(24) for key in _TRIGGER_KEYS}

    values, errors = parse_trigger_fields(form)
    assert isinstance(values, dict)
    assert isinstance(errors, list)
    assert values["due_date"] is None or isinstance(values["due_date"], date)
    assert values["interval_days"] is None or (
        isinstance(values["interval_days"], int) and values["interval_days"] > 0
    )
    for key in ("due_engine_hours", "interval_hours"):
        v = values[key]
        assert v is None or (isinstance(v, float) and math.isfinite(v) and v >= 0), (
            f"{key} returned {v!r}"
        )
    for key in ("due_landings", "interval_landings"):
        v = values[key]
        assert v is None or (isinstance(v, int) and v >= 0), f"{key} returned {v!r}"

    requires_hobbs = fdp.ConsumeBool()
    requires_landings = fdp.ConsumeBool()
    service_form = {
        "performed_at": fdp.ConsumeUnicodeNoSurrogates(24),
        "hobbs_at_service": fdp.ConsumeUnicodeNoSurrogates(24),
        "landings_at_service": fdp.ConsumeUnicodeNoSurrogates(24),
        "notes": fdp.ConsumeUnicodeNoSurrogates(24),
    }
    service_values, service_errors = parse_service_fields(
        service_form, requires_hobbs, requires_landings
    )
    assert isinstance(service_values, dict)
    assert isinstance(service_errors, list)
    assert service_values["performed_at"] is None or isinstance(
        service_values["performed_at"], date
    )
    hobbs = service_values["hobbs_at_service"]
    assert hobbs is None or (isinstance(hobbs, float) and math.isfinite(hobbs))
    if requires_hobbs:
        assert hobbs is None or hobbs >= 0, f"hobbs_at_service returned {hobbs!r}"
    landings = service_values["landings_at_service"]
    assert landings is None or (isinstance(landings, int) and landings >= 0), (
        f"landings_at_service returned {landings!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
