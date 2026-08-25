"""Tests for Phase 40: AMP task-list spreadsheet parsing (maintenance/amp_import.py)."""

from datetime import date, timedelta

import openpyxl
import pytest
from maintenance.amp_import import (  # pyright: ignore[reportMissingImports]
    compute_due_fields,
    find_header,
    format_interval,
    hours_basis_for_component,
    match_category,
    parse_amp_rows,
    parse_interval,
    suggest_component_id,
)
from models import AmpCategory, ComponentType, HoursBasis  # pyright: ignore[reportMissingImports]


class _FakeComponent:
    def __init__(self, id, type, removed_at=None):  # noqa: A002
        self.id = id
        self.type = type
        self.removed_at = removed_at


def _wb_from_rows(rows, sheet_name="Sheet1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    return wb


# ── Interval parser ─────────────────────────────────────────────────────────


class TestParseInterval:
    def test_hours_only(self):
        r = parse_interval("100FH")
        assert r.interval_hours == 100.0
        assert r.interval_days is None
        assert r.needs_review is False

    def test_months_only(self):
        r = parse_interval("12MO")
        assert r.interval_hours is None
        assert r.interval_days == 360
        assert r.needs_review is False

    def test_combined_hours_and_months(self):
        r = parse_interval("100FH / 12MO")
        assert r.interval_hours == 100.0
        assert r.interval_days == 360
        assert r.needs_review is False

    def test_years(self):
        r = parse_interval("3YR")
        assert r.interval_days == 3 * 365

    def test_days(self):
        r = parse_interval("30DY")
        assert r.interval_days == 30

    def test_pending_needs_review(self):
        r = parse_interval("PENDING")
        assert r.needs_review is True
        assert r.interval_hours is None
        assert r.interval_days is None

    def test_empty_needs_review(self):
        r = parse_interval("")
        assert r.needs_review is True

    def test_none_needs_review(self):
        r = parse_interval(None)
        assert r.needs_review is True

    def test_unparseable_string_needs_review(self):
        r = parse_interval("garbage")
        assert r.needs_review is True

    def test_partial_garbage_invalidates_whole_interval(self):
        r = parse_interval("100FH / garbage")
        assert r.needs_review is True
        assert r.interval_hours is None
        assert r.interval_days is None

    def test_case_insensitive(self):
        r = parse_interval("100fh / 12mo")
        assert r.interval_hours == 100.0
        assert r.interval_days == 360

    def test_whitespace_tolerant(self):
        r = parse_interval("  100FH   /   12MO  ")
        assert r.interval_hours == 100.0
        assert r.interval_days == 360


# ── Category matching ────────────────────────────────────────────────────────


class TestMatchCategory:
    def test_exact_match(self):
        assert match_category("Maintenance due to repetitive ADs") == (
            AmpCategory.REPETITIVE_ADS
        )

    def test_case_and_whitespace_tolerant(self):
        assert (
            match_category("  maintenance DUE to   repetitive ads  ")
            == AmpCategory.REPETITIVE_ADS
        )

    def test_no_match_returns_none(self):
        assert match_category("Routine DAH ICA inspection") is None

    def test_blank_returns_none(self):
        assert match_category("") is None
        assert match_category(None) is None


# ── Component heuristic ──────────────────────────────────────────────────────


class TestSuggestComponentId:
    def test_engine_mention_matches_engine_component(self):
        comps = [_FakeComponent(1, ComponentType.ENGINE)]
        assert (
            suggest_component_id("Maintenance recommendations", "Engine 100 hrs", comps)
            == 1
        )

    def test_propeller_mention_matches_propeller_component(self):
        comps = [
            _FakeComponent(1, ComponentType.ENGINE),
            _FakeComponent(2, ComponentType.PROPELLER),
        ]
        assert suggest_component_id(None, "Propeller 100 hrs inspection", comps) == 2

    def test_no_mention_returns_none(self):
        comps = [_FakeComponent(1, ComponentType.ENGINE)]
        assert suggest_component_id("Other", "Annual inspection", comps) is None

    def test_mention_without_matching_component_returns_none(self):
        assert suggest_component_id(None, "Engine oil change", []) is None

    def test_removed_component_not_suggested(self):
        comps = [_FakeComponent(1, ComponentType.ENGINE, removed_at=date(2020, 1, 1))]
        assert suggest_component_id(None, "Engine 100 hrs", comps) is None

    def test_category_text_also_checked(self):
        comps = [_FakeComponent(1, ComponentType.ENGINE)]
        assert (
            suggest_component_id("Engine recommendations", "100 hrs check", comps) == 1
        )


class TestHoursBasisForComponent:
    def test_engine_component_uses_engine_basis(self):
        assert (
            hours_basis_for_component(_FakeComponent(1, ComponentType.ENGINE))
            == HoursBasis.ENGINE
        )

    def test_propeller_component_uses_engine_basis(self):
        assert (
            hours_basis_for_component(_FakeComponent(1, ComponentType.PROPELLER))
            == HoursBasis.ENGINE
        )

    def test_airframe_component_uses_flight_basis(self):
        assert (
            hours_basis_for_component(_FakeComponent(1, ComponentType.AIRFRAME))
            == HoursBasis.FLIGHT
        )

    def test_no_component_uses_flight_basis(self):
        assert hours_basis_for_component(None) == HoursBasis.FLIGHT


# ── Header detection ─────────────────────────────────────────────────────────


class TestFindHeader:
    def test_finds_header_in_first_row(self):
        wb = _wb_from_rows(
            [
                ["Category", "Task description", "Reference", "Interval"],
                ["", "100 hr inspection", "", "100FH"],
            ]
        )
        loc = find_header(wb)
        assert loc.header_row_index == 0
        assert loc.col_index["task_description"] == 1
        assert loc.col_index["interval"] == 3

    def test_ignores_leading_unrelated_rows(self):
        wb = _wb_from_rows(
            [
                ["AMP Analysis workbook"],
                ["Purpose: something else entirely"],
                [],
                ["Category", "Task description", "Reference", "Action", "Interval"],
                ["", "100 hr inspection", "", "", "100FH"],
            ]
        )
        loc = find_header(wb)
        assert loc.header_row_index == 3

    def test_column_order_does_not_matter(self):
        wb = _wb_from_rows(
            [
                ["Interval", "Task description", "Category"],
                ["100FH", "100 hr inspection", ""],
            ]
        )
        loc = find_header(wb)
        assert loc.col_index["interval"] == 0
        assert loc.col_index["task_description"] == 1

    def test_case_insensitive_header_match(self):
        wb = _wb_from_rows(
            [
                ["TASK DESCRIPTION", "interval"],
                ["100 hr inspection", "100FH"],
            ]
        )
        loc = find_header(wb)
        assert loc.col_index["task_description"] == 0

    def test_scans_other_sheets(self):
        wb = _wb_from_rows([["Nothing useful here"]], sheet_name="README")
        ws2 = wb.create_sheet("Tasks")
        ws2.append(["Task description", "Interval"])
        ws2.append(["100 hr inspection", "100FH"])
        loc = find_header(wb)
        assert loc.sheet_name == "Tasks"

    def test_no_matching_header_raises(self):
        wb = _wb_from_rows([["Foo", "Bar"], ["a", "b"]])
        with pytest.raises(ValueError, match="No matching header row"):
            find_header(wb)


# ── Full row parsing ─────────────────────────────────────────────────────────


class TestParseAmpRows:
    def test_parses_basic_rows(self):
        wb = _wb_from_rows(
            [
                [
                    "Category",
                    "Task description",
                    "Reference",
                    "Action",
                    "Interval",
                    "Part number",
                    "Serial number",
                    "Notes",
                ],
                [
                    "Maintenance due to repetitive ADs",
                    "AD compliance check",
                    "AD 2023-0048",
                    "INSPECTION",
                    "100FH / 12MO",
                    "PN-123",
                    "SN-456",
                    "Some notes",
                ],
            ]
        )
        rows = parse_amp_rows(wb, [])
        assert len(rows) == 1
        r = rows[0]
        assert r.name == "AD compliance check"
        assert r.category == AmpCategory.REPETITIVE_ADS
        assert r.reference == "AD 2023-0048"
        assert r.action == "INSPECTION"
        assert r.part_number == "PN-123"
        assert r.serial_number == "SN-456"
        assert r.notes == "Some notes"
        assert r.interval_hours == 100.0
        assert r.interval_days == 360
        assert r.needs_review is False

    def test_skips_blank_rows(self):
        wb = _wb_from_rows(
            [
                ["Task description", "Interval"],
                ["100 hr inspection", "100FH"],
                [None, None],
                ["", ""],
                ["Annual", "12MO"],
            ]
        )
        rows = parse_amp_rows(wb, [])
        assert [r.name for r in rows] == ["100 hr inspection", "Annual"]

    def test_pending_row_flagged_needs_review(self):
        wb = _wb_from_rows(
            [
                ["Task description", "Interval", "Notes"],
                ["Undecided task", "PENDING", "PENDING SHOP INPUT"],
            ]
        )
        rows = parse_amp_rows(wb, [])
        assert rows[0].needs_review is True
        assert rows[0].interval_raw == "PENDING"

    def test_row_number_reflects_spreadsheet_position(self):
        wb = _wb_from_rows(
            [
                ["Task description", "Interval"],
                ["First", "100FH"],
                ["Second", "12MO"],
            ]
        )
        rows = parse_amp_rows(wb, [])
        assert rows[0].row_number == 2
        assert rows[1].row_number == 3

    def test_suggested_component_wired_through(self):
        comps = [_FakeComponent(7, ComponentType.ENGINE)]
        wb = _wb_from_rows(
            [
                ["Task description", "Interval"],
                ["Engine 100 hr inspection", "100FH"],
            ]
        )
        rows = parse_amp_rows(wb, comps)
        assert rows[0].suggested_component_id == 7


# ── compute_due_fields ───────────────────────────────────────────────────────


class TestComputeDueFields:
    def test_hours_only(self):
        due_h, due_d = compute_due_fields(
            100.0,
            None,
            HoursBasis.ENGINE,
            current_engine_hours=50.0,
            current_flight_hours=None,
        )
        assert due_h == 150.0
        assert due_d is None

    def test_calendar_only(self):
        today = date(2026, 1, 1)
        due_h, due_d = compute_due_fields(
            None,
            30,
            HoursBasis.FLIGHT,
            current_engine_hours=None,
            current_flight_hours=None,
            today=today,
        )
        assert due_h is None
        assert due_d == today + timedelta(days=30)

    def test_combined(self):
        today = date(2026, 1, 1)
        due_h, due_d = compute_due_fields(
            100.0,
            360,
            HoursBasis.ENGINE,
            current_engine_hours=50.0,
            current_flight_hours=None,
            today=today,
        )
        assert due_h == 150.0
        assert due_d == today + timedelta(days=360)

    def test_flight_basis_uses_flight_hours(self):
        due_h, _due_d = compute_due_fields(
            100.0,
            None,
            HoursBasis.FLIGHT,
            current_engine_hours=999.0,
            current_flight_hours=50.0,
        )
        assert due_h == 150.0

    def test_hours_interval_but_no_current_reading_leaves_unset(self):
        due_h, _due_d = compute_due_fields(
            100.0,
            None,
            HoursBasis.ENGINE,
            current_engine_hours=None,
            current_flight_hours=None,
        )
        assert due_h is None

    def test_no_interval_at_all(self):
        due_h, due_d = compute_due_fields(
            None,
            None,
            HoursBasis.ENGINE,
            current_engine_hours=50.0,
            current_flight_hours=None,
        )
        assert due_h is None
        assert due_d is None


# ── format_interval (Appendix B export — inverse of parse_interval) ─────────


class TestFormatInterval:
    def test_hours_only(self):
        assert format_interval(100.0, None) == "100FH"

    def test_months_only(self):
        assert format_interval(None, 360) == "12MO"

    def test_years(self):
        assert format_interval(None, 365 * 3) == "3YR"

    def test_days_when_not_evenly_divisible(self):
        assert format_interval(None, 10) == "10DY"

    def test_combined(self):
        assert format_interval(100.0, 360) == "100FH / 12MO"

    def test_no_interval_at_all(self):
        assert format_interval(None, None) == ""

    def test_fractional_hours_preserved(self):
        assert format_interval(50.5, None) == "50.5FH"

    def test_round_trips_through_parse_interval(self):
        for raw in ["100FH", "12MO", "100FH / 12MO", "3YR", "500FH / 72MO"]:
            parsed = parse_interval(raw)
            assert format_interval(parsed.interval_hours, parsed.interval_days) == raw
