"""AMP task-list spreadsheet import (Phase 40).

Parses the OpenHangar "AMP task list" import template — documented in
docs/maintenance_import.md — into structured rows ready for review. Column
order and leading unrelated rows/sheets don't matter: the header row is
located by matching known column names, not a fixed position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from models import (  # pyright: ignore[reportMissingImports]
    AmpCategory,
    ComponentType,
    HoursBasis,
)

if TYPE_CHECKING:
    import openpyxl

_MAX_HEADER_SCAN_ROWS = 30

# Canonical column key -> normalised header text it matches.
_HEADER_ALIASES = {
    "category": "category",
    "task description": "task_description",
    "reference": "reference",
    "action": "action",
    "interval": "interval",
    "part number": "part_number",
    "serial number": "serial_number",
    "notes": "notes",
}
_REQUIRED_HEADER_KEYS = {"task_description", "interval"}


def _norm(raw: Any) -> str:
    return " ".join(str(raw or "").strip().lower().split())


@dataclass
class HeaderLocation:
    sheet_name: str
    header_row_index: int  # 0-based, within the sheet
    col_index: dict[str, int]  # canonical key -> column index


def find_header(wb: "openpyxl.Workbook") -> HeaderLocation:
    """Scan every sheet's first _MAX_HEADER_SCAN_ROWS rows for one whose
    cells include at least 'Task description' and 'Interval' (matched
    case/whitespace-insensitively); returns the first match found, sheets
    in workbook order."""
    for ws in wb.worksheets:
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_row=_MAX_HEADER_SCAN_ROWS, values_only=True)
        ):
            col_index: dict[str, int] = {}
            for col_idx, cell in enumerate(row):
                key = _HEADER_ALIASES.get(_norm(cell))
                if key and key not in col_index:
                    col_index[key] = col_idx
            if _REQUIRED_HEADER_KEYS <= col_index.keys():
                return HeaderLocation(ws.title, row_idx, col_index)
    raise ValueError(
        "No matching header row found. Expected a sheet with at least "
        "'Task description' and 'Interval' columns."
    )


_INTERVAL_FH_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*FH$", re.IGNORECASE)
_INTERVAL_CAL_RE = re.compile(r"^(\d+)\s*(DY|MO|YR)$", re.IGNORECASE)
_CAL_UNIT_DAYS = {"DY": 1, "MO": 30, "YR": 365}


@dataclass
class IntervalParseResult:
    interval_hours: float | None = None
    interval_days: int | None = None
    needs_review: bool = False


def parse_interval(raw: Any) -> IntervalParseResult:
    """Parse the Interval mini-syntax: one or two '/'-separated parts, each
    either '<n>FH' or '<n>(DY|MO|YR)'. Both parts populate together for a
    combined "whichever comes first" interval. Empty, 'PENDING', or any
    unparseable part makes the whole interval needs_review — never a
    partial/silent result."""
    text = str(raw or "").strip()
    if not text or text.upper() == "PENDING":
        return IntervalParseResult(needs_review=True)

    interval_hours: float | None = None
    interval_days: int | None = None
    for part in text.split("/"):
        part = part.strip()
        m = _INTERVAL_FH_RE.match(part)
        if m:
            interval_hours = float(m.group(1))
            continue
        m = _INTERVAL_CAL_RE.match(part)
        if m:
            n = int(m.group(1))
            interval_days = n * _CAL_UNIT_DAYS[m.group(2).upper()]
            continue
        return IntervalParseResult(needs_review=True)

    # Every part matched (or we'd have returned above), so at least one of
    # interval_hours/interval_days is set here — text.split("/") can never
    # yield zero parts.
    return IntervalParseResult(
        interval_hours=interval_hours, interval_days=interval_days
    )


def match_category(raw: Any) -> str | None:
    """Case/whitespace-tolerant exact match against the 9 canonical
    AmpCategory values. No match (blank, admin/routine text, typo) -> None,
    the row's category is simply left unset — not rejected."""
    norm = _norm(raw)
    if not norm:
        return None
    for canonical in AmpCategory.ALL:
        if _norm(canonical) == norm:
            return canonical
    return None


def suggest_component_id(
    category_raw: Any, task_description_raw: Any, components: list[Any]
) -> int | None:
    """Heuristic: a mention of 'propeller' or 'engine' in the category or
    task description text suggests the aircraft's matching installed
    component. No mention, or no installed component of that type, leaves
    the row unscoped — corrected per-row on the review screen."""
    text = f"{category_raw or ''} {task_description_raw or ''}".lower()
    if "propeller" in text:
        comp_type = ComponentType.PROPELLER
    elif "engine" in text:
        comp_type = ComponentType.ENGINE
    else:
        return None
    for c in components:
        if c.removed_at is None and c.type == comp_type:
            return c.id  # type: ignore[no-any-return]
    return None


def hours_basis_for_component(component: Any) -> str:
    """Engine/propeller components track TBO-style intervals in engine
    hours; everything else (including unscoped) is flight-hours based —
    mirrors the same default used by the trigger form's component picker."""
    if component is not None and component.type in (
        ComponentType.ENGINE,
        ComponentType.PROPELLER,
    ):
        return HoursBasis.ENGINE
    return HoursBasis.FLIGHT


@dataclass
class ParsedAmpRow:
    row_number: int  # 1-based spreadsheet row, for display only
    name: str
    category_raw: str | None
    category: str | None
    reference: str | None
    action: str | None
    part_number: str | None
    serial_number: str | None
    notes: str | None
    interval_raw: str | None
    interval_hours: float | None
    interval_days: int | None
    needs_review: bool
    suggested_component_id: int | None


def _cell(row: tuple[Any, ...], col_index: dict[str, int], key: str) -> Any:
    idx = col_index.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _text_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_amp_rows(
    wb: "openpyxl.Workbook", components: list[Any]
) -> list[ParsedAmpRow]:
    """Parse every data row of the located header's sheet. A row with no
    task description is a blank/trailing row and is silently skipped (not
    counted, not an error) — everything else always produces a row, even
    when its interval is unparseable (see parse_interval)."""
    header = find_header(wb)
    ws = wb[header.sheet_name]
    rows: list[ParsedAmpRow] = []
    for row_idx, row in enumerate(
        ws.iter_rows(min_row=header.header_row_index + 2, values_only=True),
        start=header.header_row_index + 2,
    ):
        name = _text_or_none(_cell(row, header.col_index, "task_description"))
        if not name:
            continue
        category_raw = _text_or_none(_cell(row, header.col_index, "category"))
        interval_raw = _text_or_none(_cell(row, header.col_index, "interval"))
        interval = parse_interval(interval_raw)
        rows.append(
            ParsedAmpRow(
                row_number=row_idx,
                name=name,
                category_raw=category_raw,
                category=match_category(category_raw),
                reference=_text_or_none(_cell(row, header.col_index, "reference")),
                action=_text_or_none(_cell(row, header.col_index, "action")),
                part_number=_text_or_none(_cell(row, header.col_index, "part_number")),
                serial_number=_text_or_none(
                    _cell(row, header.col_index, "serial_number")
                ),
                notes=_text_or_none(_cell(row, header.col_index, "notes")),
                interval_raw=interval_raw,
                interval_hours=interval.interval_hours,
                interval_days=interval.interval_days,
                needs_review=interval.needs_review,
                suggested_component_id=suggest_component_id(
                    category_raw, name, components
                ),
            )
        )
    return rows


def compute_due_fields(
    interval_hours: float | None,
    interval_days: int | None,
    hours_basis: str,
    current_engine_hours: float | None,
    current_flight_hours: float | None,
    today: "_date | None" = None,
) -> tuple[float | None, "_date | None"]:
    """Initial due_engine_hours/due_date for a freshly-imported trigger:
    the current reading plus the interval (hours side needs a current
    reading to anchor to; with none available yet, that side is simply
    left unset — the same as a manually-entered trigger with no due value
    yet, correctable once flight history exists)."""
    due_engine_hours = None
    if interval_hours is not None:
        current = (
            current_flight_hours
            if hours_basis == HoursBasis.FLIGHT
            else current_engine_hours
        )
        if current is not None:
            due_engine_hours = current + interval_hours
    due_date = None
    if interval_days is not None:
        due_date = (today or _date.today()) + timedelta(days=interval_days)
    return due_engine_hours, due_date
