"""Direct unit tests for flights/form_parsing.py's parse_flight_fields().

Regression tests for bugs found by fuzz/fuzz_flight_form_parsing.py: several
numeric fields assigned the parsed value *before* validating its sign, then
raised ValueError to reject it — but the except block only appended an
error message without resetting the field back to None, so a negative
input still came back in the returned `values` dict alongside the
rejection error.
"""

from types import SimpleNamespace

from flights.form_parsing import (
    parse_flight_fields,  # pyright: ignore[reportMissingImports]
)


class TestNegativeValuesResetToNone:
    def test_garbage_flight_time_input_ignored_not_an_error(self):
        """flight_time is never taken as free-text user input — a posted
        value (garbage or otherwise) is simply ignored, not validated or
        rejected; with no counters/clock times to derive it from here, it
        stays None."""
        values, errors = parse_flight_fields({"flight_time": "-5"}, None)
        assert values["flight_time"] is None
        assert not any("time" in e.lower() for e in errors)

    def test_negative_passenger_count_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"passenger_count": "-3"}, None)
        assert values["passenger_count"] is None
        assert any("Passenger count" in e for e in errors)

    def test_negative_landing_count_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"landing_count": "-1"}, None)
        assert values["landing_count"] is None
        assert any("Landing count" in e for e in errors)

    def test_negative_fuel_added_qty_returns_none_not_negative(self):
        values, errors = parse_flight_fields(
            {"fuel_event": "before", "fuel_added_qty": "-10"}, None
        )
        assert values["fuel_added_qty"] is None
        assert any("Fuel quantity added" in e for e in errors)

    def test_negative_fuel_remaining_qty_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"fuel_remaining_qty": "-1"}, None)
        assert values["fuel_remaining_qty"] is None
        assert any("Fuel remaining" in e for e in errors)

    def test_negative_oil_added_l_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"oil_added_l": "-1"}, None)
        assert values["oil_added_l"] is None
        assert any("Oil added" in e for e in errors)


class TestNonFiniteValuesRejected:
    """Regression tests for a systemic bug found while auditing this
    session's numeric-parsing fixes: float() accepts "inf"/"nan"/"Infinity"
    without raising, and inf/nan both fail a naive `< 0` sign check to look
    "non-negative" — so an all-sign-check validator let non-finite values
    through. int(float("inf")) then raises OverflowError downstream
    (see pilots/personal_minimums.py::recency_breaches)."""

    def test_nan_fuel_added_qty_rejected(self):
        values, errors = parse_flight_fields(
            {"fuel_event": "before", "fuel_added_qty": "nan"}, None
        )
        assert values["fuel_added_qty"] is None
        assert any("Fuel quantity added" in e for e in errors)

    def test_infinite_fuel_remaining_qty_rejected(self):
        values, errors = parse_flight_fields({"fuel_remaining_qty": "Infinity"}, None)
        assert values["fuel_remaining_qty"] is None
        assert any("Fuel remaining" in e for e in errors)

    def test_infinite_oil_added_l_rejected(self):
        values, errors = parse_flight_fields({"oil_added_l": "inf"}, None)
        assert values["oil_added_l"] is None
        assert any("Oil added" in e for e in errors)

    def test_infinite_counter_value_rejected(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields({"flight_time_counter_start": "inf"}, ac)
        assert values["flight_time_counter_start"] is None
        assert any("Counter value" in e for e in errors)


class TestUtcOffsetClockTimeRejected:
    """Regression test for a crash found by fuzz/fuzz_flight_form_parsing.py:
    time.fromisoformat() also accepts a UTC-offset suffix, producing a
    timezone-aware time that can't be compared against the naive time from
    plain "HH:MM" input in _hours_between() — raising TypeError instead of
    a validation error."""

    def test_departure_time_with_utc_offset_rejected(self):
        values, errors = parse_flight_fields(
            {"departure_time": "04:23+02:00", "arrival_time": "06:00"}, None
        )
        assert values["departure_time"] is None
        assert any("Departure time" in e for e in errors)

    def test_arrival_time_with_utc_offset_rejected(self):
        values, errors = parse_flight_fields(
            {"departure_time": "04:23", "arrival_time": "06:00+02:00"}, None
        )
        assert values["arrival_time"] is None
        assert any("Arrival time" in e for e in errors)

    def test_takeoff_time_with_utc_offset_rejected(self):
        values, errors = parse_flight_fields(
            {"takeoff_time": "04:23+02:00", "landing_time": "06:00"}, None
        )
        assert values["takeoff_time"] is None
        assert any("Takeoff time" in e for e in errors)

    def test_landing_time_with_utc_offset_rejected(self):
        values, errors = parse_flight_fields(
            {"takeoff_time": "04:23", "landing_time": "06:00+02:00"}, None
        )
        assert values["landing_time"] is None
        assert any("Landing time" in e for e in errors)


class TestCounterDerivedFlightTimeNeverNegative:
    def test_end_before_start_clamps_to_zero_not_negative(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "flight_time_counter_start": "100",
                "flight_time_counter_end": "1",
            },
            ac,
        )
        assert values["flight_time"] == 0.0
        assert any("Flight counter end" in e for e in errors)

    def test_end_after_start_computes_normally(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "flight_time_counter_start": "1",
                "flight_time_counter_end": "2.5",
            },
            ac,
        )
        assert values["flight_time"] == 1.5
        assert not any("counter" in e.lower() for e in errors)
