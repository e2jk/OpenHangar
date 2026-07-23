"""Direct unit tests for maintenance/form_parsing.py.

Extracted from _save_trigger/service_trigger in maintenance/routes.py so it
can be fuzzed directly (fuzz/fuzz_maintenance_form_parsing.py) — these
cover the parsing branches route-level tests don't happen to exercise
(non-numeric interval/hobbs strings, as opposed to merely out-of-range
ones).
"""

from datetime import date

from maintenance.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_service_fields,
    parse_trigger_fields,
)
from models import TriggerType  # pyright: ignore[reportMissingImports]


class TestParseTriggerFields:
    def test_valid_calendar_trigger(self):
        values, errors = parse_trigger_fields(
            {
                "name": "Annual",
                "trigger_type": TriggerType.CALENDAR,
                "due_date": "2026-01-01",
                "interval_days": "365",
            }
        )
        assert errors == []
        assert values["due_date"] == date(2026, 1, 1)
        assert values["interval_days"] == 365

    def test_valid_hours_trigger(self):
        values, errors = parse_trigger_fields(
            {
                "name": "Oil change",
                "trigger_type": TriggerType.HOURS,
                "due_engine_hours": "100",
                "interval_hours": "50",
            }
        )
        assert errors == []
        assert values["due_engine_hours"] == 100.0
        assert values["interval_hours"] == 50.0

    def test_missing_name_is_error(self):
        _values, errors = parse_trigger_fields({"trigger_type": TriggerType.CALENDAR})
        assert any("Name is required" in e for e in errors)

    def test_invalid_trigger_type_is_error(self):
        _values, errors = parse_trigger_fields({"name": "x", "trigger_type": "bogus"})
        assert any("Trigger type" in e for e in errors)

    def test_non_numeric_interval_days_is_error_and_none(self):
        values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.CALENDAR,
                "due_date": "2026-01-01",
                "interval_days": "not-a-number",
            }
        )
        assert values["interval_days"] is None
        assert any("Interval (days)" in e for e in errors)

    def test_negative_interval_days_is_error(self):
        _values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.CALENDAR,
                "due_date": "2026-01-01",
                "interval_days": "-5",
            }
        )
        assert any("Interval (days)" in e for e in errors)

    def test_non_numeric_due_engine_hours_is_error_and_none(self):
        values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.HOURS,
                "due_engine_hours": "not-a-number",
            }
        )
        assert values["due_engine_hours"] is None
        assert any("Due engine hours" in e for e in errors)

    def test_non_numeric_interval_hours_is_error_and_none(self):
        values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.HOURS,
                "due_engine_hours": "10",
                "interval_hours": "not-a-number",
            }
        )
        assert values["interval_hours"] is None
        assert any("Interval (hours)" in e for e in errors)

    def test_zero_interval_hours_is_error(self):
        _values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.HOURS,
                "due_engine_hours": "10",
                "interval_hours": "0",
            }
        )
        assert any("Interval (hours)" in e for e in errors)

    def test_invalid_due_date_is_error(self):
        _values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.CALENDAR,
                "due_date": "not-a-date",
            }
        )
        assert any("Due date must be" in e for e in errors)

    def test_missing_due_date_for_calendar_is_error(self):
        _values, errors = parse_trigger_fields(
            {"name": "x", "trigger_type": TriggerType.CALENDAR}
        )
        assert any("Due date is required" in e for e in errors)

    def test_missing_due_engine_hours_for_hours_is_error(self):
        _values, errors = parse_trigger_fields(
            {"name": "x", "trigger_type": TriggerType.HOURS}
        )
        assert any("Due engine hours is required" in e for e in errors)

    def test_negative_due_engine_hours_is_error(self):
        _values, errors = parse_trigger_fields(
            {
                "name": "x",
                "trigger_type": TriggerType.HOURS,
                "due_engine_hours": "-1",
            }
        )
        assert any("Due engine hours" in e for e in errors)


class TestParseServiceFields:
    def test_valid_calendar_service(self):
        values, errors = parse_service_fields(
            {"performed_at": "2026-01-01"}, TriggerType.CALENDAR
        )
        assert errors == []
        assert values["performed_at"] == date(2026, 1, 1)
        assert values["hobbs_at_service"] is None

    def test_missing_performed_at_is_error(self):
        _values, errors = parse_service_fields({}, TriggerType.CALENDAR)
        assert any("Service date is required" in e for e in errors)

    def test_invalid_performed_at_is_error(self):
        _values, errors = parse_service_fields(
            {"performed_at": "garbage"}, TriggerType.CALENDAR
        )
        assert any("Service date must be" in e for e in errors)

    def test_hours_trigger_requires_hobbs(self):
        _values, errors = parse_service_fields(
            {"performed_at": "2026-01-01"}, TriggerType.HOURS
        )
        assert any("Hobbs at service is required" in e for e in errors)

    def test_hours_trigger_negative_hobbs_is_error(self):
        values, errors = parse_service_fields(
            {"performed_at": "2026-01-01", "hobbs_at_service": "-1"},
            TriggerType.HOURS,
        )
        assert values["hobbs_at_service"] is None
        assert any("Hobbs at service must be" in e for e in errors)

    def test_hours_trigger_valid_hobbs(self):
        values, errors = parse_service_fields(
            {"performed_at": "2026-01-01", "hobbs_at_service": "123.4"},
            TriggerType.HOURS,
        )
        assert errors == []
        assert values["hobbs_at_service"] == 123.4

    def test_calendar_trigger_optional_hobbs_ignored_if_invalid(self):
        values, errors = parse_service_fields(
            {"performed_at": "2026-01-01", "hobbs_at_service": "garbage"},
            TriggerType.CALENDAR,
        )
        assert values["hobbs_at_service"] is None
        assert errors == []

    def test_calendar_trigger_optional_hobbs_used_if_present(self):
        values, errors = parse_service_fields(
            {"performed_at": "2026-01-01", "hobbs_at_service": "42"},
            TriggerType.CALENDAR,
        )
        assert values["hobbs_at_service"] == 42.0
        assert errors == []
