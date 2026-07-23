"""Direct unit tests for pilots/form_parsing.py.

Regression test for a bug found by fuzz/fuzz_pilot_form_parsing.py:
_parse_time() split "HH:MM" and passed both halves through the unbounded
int(), then only caught (ValueError, AttributeError) around the
datetime.time() constructor — but time() is C-backed and raises
OverflowError (not ValueError) once the value no longer fits a C long,
e.g. an hour string of 20+ digits. Reachable via the pilot logbook form's
departure/arrival time fields and the offline sync API.
"""

from pilots.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_linked_pilot_fields,
    parse_pilot_fields,
)


class TestParseTimeOverflow:
    def test_oversized_hour_digit_string_returns_none_not_crash(self):
        values, errors = parse_pilot_fields(
            {"departure_time": "99999999999999999999999999:00"}
        )
        assert values["departure_time"] is None
        assert any("Departure time" in e for e in errors)

    def test_oversized_minute_digit_string_returns_none_not_crash(self):
        values, errors = parse_linked_pilot_fields(
            {"arrival_time": "1:99999999999999999999999999"}
        )
        assert values["arrival_time"] is None
        assert any("Arrival time" in e for e in errors)
