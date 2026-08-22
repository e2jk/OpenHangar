"""Direct unit tests for flights/form_parsing.py's parse_flight_fields().

Regression tests for bugs found by fuzz/fuzz_flight_form_parsing.py: several
numeric fields assigned the parsed value *before* validating its sign, then
raised ValueError to reject it — but the except block only appended an
error message without resetting the field back to None, so a negative
input still came back in the returned `values` dict alongside the
rejection error.
"""

from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]
from flights.form_parsing import (  # pyright: ignore[reportMissingImports]
    flight_is_lenient,
    parse_flight_fields,
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

    def test_negative_fuel_added_before_qty_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"fuel_added_before_qty": "-10"}, None)
        assert values["fuel_added_before_qty"] is None
        assert any("Fuel quantity added" in e for e in errors)

    def test_negative_fuel_added_after_qty_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"fuel_added_after_qty": "-10"}, None)
        assert values["fuel_added_after_qty"] is None
        assert any("Fuel quantity added" in e for e in errors)

    def test_negative_fuel_remaining_qty_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"fuel_remaining_qty": "-1"}, None)
        assert values["fuel_remaining_qty"] is None
        assert any("Fuel remaining" in e for e in errors)

    def test_negative_oil_added_before_l_returns_none_not_negative(self):
        values, errors = parse_flight_fields({"oil_added_before_l": "-1"}, None)
        assert values["oil_added_before_l"] is None
        assert any("Oil added" in e for e in errors)


class TestNonFiniteValuesRejected:
    """Regression tests for a systemic bug found while auditing this
    session's numeric-parsing fixes: float() accepts "inf"/"nan"/"Infinity"
    without raising, and inf/nan both fail a naive `< 0` sign check to look
    "non-negative" — so an all-sign-check validator let non-finite values
    through. int(float("inf")) then raises OverflowError downstream
    (see pilots/personal_minimums.py::recency_breaches)."""

    def test_nan_fuel_added_before_qty_rejected(self):
        values, errors = parse_flight_fields({"fuel_added_before_qty": "nan"}, None)
        assert values["fuel_added_before_qty"] is None
        assert any("Fuel quantity added" in e for e in errors)

    def test_infinite_fuel_remaining_qty_rejected(self):
        values, errors = parse_flight_fields({"fuel_remaining_qty": "Infinity"}, None)
        assert values["fuel_remaining_qty"] is None
        assert any("Fuel remaining" in e for e in errors)

    def test_infinite_oil_added_before_l_rejected(self):
        values, errors = parse_flight_fields({"oil_added_before_l": "inf"}, None)
        assert values["oil_added_before_l"] is None
        assert any("Oil added" in e for e in errors)

    def test_infinite_counter_value_rejected(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields({"flight_time_counter_start": "inf"}, ac)
        assert values["flight_time_counter_start"] is None
        assert any("Counter value" in e for e in errors)


class TestFuelAddedBeforeAndAfterAreIndependent:
    def test_both_before_and_after_can_be_set_on_the_same_flight(self):
        values, errors = parse_flight_fields(
            {
                "date": "2025-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "fuel_added_before_qty": "20",
                "fuel_added_before_unit": "L",
                "fuel_added_after_qty": "15",
                "fuel_added_after_unit": "gal",
            },
            None,
        )
        assert errors == []
        assert values["fuel_added_before_qty"] == 20.0
        assert values["fuel_added_before_unit"] == "L"
        assert values["fuel_added_after_qty"] == 15.0
        assert values["fuel_added_after_unit"] == "gal"

    def test_unit_defaults_to_none_when_qty_not_provided(self):
        values, _errors = parse_flight_fields({}, None)
        assert values["fuel_added_before_qty"] is None
        assert values["fuel_added_before_unit"] is None
        assert values["fuel_added_after_qty"] is None
        assert values["fuel_added_after_unit"] is None


class TestOilAddedBeforeAndAfterAreIndependent:
    def test_both_before_and_after_can_be_set_on_the_same_flight(self):
        values, errors = parse_flight_fields(
            {
                "date": "2025-06-01",
                "departure_icao": "EBOS",
                "arrival_icao": "EBBR",
                "oil_added_before_l": "0.5",
                "oil_added_after_l": "0.25",
            },
            None,
        )
        assert errors == []
        assert values["oil_added_before_l"] == 0.5
        assert values["oil_added_after_l"] == 0.25


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


class TestColonNotationCounters:
    """Counter fields accept either a plain decimal ("972.2") or
    unambiguous "H:MM" colon notation ("972:12") — same parser the CSV
    airframe/pilot-log importers already use for counters, reused here so
    manual entry behaves identically. No per-aircraft/per-field setting:
    each value is read on its own."""

    def test_colon_counters_parsed_and_derive_correct_duration(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "flight_time_counter_start": "972:12",
                "flight_time_counter_end": "972:37",
            },
            ac,
        )
        # parse_duration_value rounds to 1 decimal, same precision as the
        # DB columns — 972:37 = 972.6167h rounds to 972.6.
        assert values["flight_time_counter_start"] == pytest.approx(972.2)
        assert values["flight_time_counter_end"] == pytest.approx(972.6)
        # 972.6 - 972.2 = 0.4h (~25 minutes, within the 0.1h rounding).
        assert values["flight_time"] == pytest.approx(0.4)
        assert not any("Counter value" in e for e in errors)

    def test_decimal_and_colon_can_mix_across_fields(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "engine_time_counter_start": "500.0",
                "engine_time_counter_end": "501:30",
            },
            ac,
        )
        assert values["engine_time_counter_start"] == 500.0
        assert values["engine_time_counter_end"] == pytest.approx(501.5)
        assert not any("Counter value" in e for e in errors)

    def test_malformed_colon_notation_rejected(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields({"flight_time_counter_start": "972:1"}, ac)
        assert values["flight_time_counter_start"] is None
        assert any("Counter value" in e for e in errors)


class TestStrictParameter:
    """strict=False (used when editing a flight from a batch flagged
    is_historical) suppresses only the crew-name-required and
    counter/clock duration-mismatch checks — pre-existing paper-log
    imprecision that was never a fresh data-entry mistake. Everything else
    still applies regardless."""

    def test_crew_name_required_when_strict_default(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        _values, errors = parse_flight_fields(
            {"date": "2025-06-01", "departure_icao": "EBOS", "arrival_icao": "EBBR"},
            ac,
        )
        assert any("Pilot (crew 1)" in e for e in errors)

    def test_crew_name_not_required_when_not_strict(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        _values, errors = parse_flight_fields(
            {"date": "2025-06-01", "departure_icao": "EBOS", "arrival_icao": "EBBR"},
            ac,
            strict=False,
        )
        assert not any("Pilot (crew 1)" in e for e in errors)

    def test_engine_duration_mismatch_blocked_when_strict(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        _values, errors = parse_flight_fields(
            {
                "departure_time": "08:00",
                "arrival_time": "09:00",
                "engine_time_counter_start": "10",
                "engine_time_counter_end": "10.2",
            },
            ac,
        )
        assert any("Engine time from the counters" in e for e in errors)

    def test_engine_duration_mismatch_suppressed_when_not_strict(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "departure_time": "08:00",
                "arrival_time": "09:00",
                "engine_time_counter_start": "10",
                "engine_time_counter_end": "10.2",
            },
            ac,
            strict=False,
        )
        assert not any("Engine time from the counters" in e for e in errors)
        assert values["engine_time"] == 0.2  # counters still preferred

    def test_flight_duration_mismatch_blocked_when_strict(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        _values, errors = parse_flight_fields(
            {
                "takeoff_time": "08:00",
                "landing_time": "09:00",
                "flight_time_counter_start": "10",
                "flight_time_counter_end": "10.2",
            },
            ac,
        )
        assert any("Flight time from the counters" in e for e in errors)

    def test_flight_duration_mismatch_suppressed_when_not_strict(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "takeoff_time": "08:00",
                "landing_time": "09:00",
                "flight_time_counter_start": "10",
                "flight_time_counter_end": "10.2",
            },
            ac,
            strict=False,
        )
        assert not any("Flight time from the counters" in e for e in errors)
        assert values["flight_time"] == 0.2  # counters still preferred

    def test_not_strict_still_enforces_date_required(self):
        _values, errors = parse_flight_fields({}, None, strict=False)
        assert any("Date is required" in e for e in errors)

    def test_not_strict_still_enforces_counter_end_before_start(self):
        ac = SimpleNamespace(has_flight_counter=True, flight_counter_offset=0.3)
        values, errors = parse_flight_fields(
            {
                "flight_time_counter_start": "100",
                "flight_time_counter_end": "1",
            },
            ac,
            strict=False,
        )
        assert values["flight_time"] == 0.0
        assert any("Flight counter end" in e for e in errors)


class TestFlightIsLenient:
    def test_none_flight_is_not_lenient(self):
        assert flight_is_lenient(None) is False

    def test_flight_without_batch_is_not_lenient(self):
        fe = SimpleNamespace(airframe_import_batch=None)
        assert flight_is_lenient(fe) is False

    def test_flight_with_non_historical_batch_is_not_lenient(self):
        fe = SimpleNamespace(airframe_import_batch=SimpleNamespace(is_historical=False))
        assert flight_is_lenient(fe) is False

    def test_flight_with_historical_batch_is_lenient(self):
        fe = SimpleNamespace(airframe_import_batch=SimpleNamespace(is_historical=True))
        assert flight_is_lenient(fe) is True
