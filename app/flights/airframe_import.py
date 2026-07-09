"""Bulk import of a historical airframe logbook (CSV/Excel) for one aircraft.

Reuses the Phase 28 pilot-logbook machinery wholesale — file parsing, header
auto-detection, subtotal-row skipping, value parsers — mapped onto
FlightEntry fields.  Counter continuity is validated with per-row warnings
(historical paper logs often carry small corrections), free-text pilot names
become FlightCrew rows with user_id = NULL, and an optional "opening
counters" baseline supports importing from a cutover date forward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from pilots.logbook_import import (  # pyright: ignore[reportMissingImports]
    ParsedFile,
    parse_date_value,
    parse_duration_value,
    parse_int_value,
    parse_time_value,
)

AIRFRAME_TARGET_FIELDS: list[str] = [
    "date",
    "crew_name",
    "departure_icao",
    "arrival_icao",
    "departure_time",
    "arrival_time",
    "flight_time",
    "flight_counter_start",
    "flight_counter_end",
    "engine_counter_start",
    "engine_counter_end",
    "landing_count",
    "passenger_count",
    "nature_of_flight",
    "notes",
]

# Normalised source column name → airframe target field.
_AIRFRAME_ALIASES: dict[str, str] = {
    "date": "date",
    "date dd/mm/yy": "date",
    "pilot": "crew_name",
    "pilot in command": "crew_name",
    "pic": "crew_name",
    "crew": "crew_name",
    "name": "crew_name",
    "from": "departure_icao",
    "departure": "departure_icao",
    "dep": "departure_icao",
    "to": "arrival_icao",
    "arrival": "arrival_icao",
    "arr": "arrival_icao",
    "time": "departure_time",  # first TIME → departure
    "time_2": "arrival_time",  # second TIME → arrival
    "departure time": "departure_time",
    "off block": "departure_time",
    "off-block": "departure_time",
    "arrival time": "arrival_time",
    "on block": "arrival_time",
    "on-block": "arrival_time",
    "flight time": "flight_time",
    "block time": "flight_time",
    "duration": "flight_time",
    "total time": "flight_time",
    "landings": "landing_count",
    "ldg": "landing_count",
    "ldgs": "landing_count",
    "landing count": "landing_count",
    "pax": "passenger_count",
    "passengers": "passenger_count",
    "nature": "nature_of_flight",
    "nature of flight": "nature_of_flight",
    "remarks": "notes",
    "remarks and endorsements": "notes",
    "notes": "notes",
    "hobbs start": "engine_counter_start",
    "hobbs end": "engine_counter_end",
    "hobbs": "engine_counter_end",
    "engine start": "engine_counter_start",
    "engine end": "engine_counter_end",
    "engine counter start": "engine_counter_start",
    "engine counter end": "engine_counter_end",
    "tach start": "engine_counter_start",
    "tach end": "engine_counter_end",
    "flight counter start": "flight_counter_start",
    "flight counter end": "flight_counter_end",
    "counter start": "flight_counter_start",
    "counter end": "flight_counter_end",
}

# Target field → parser (None = keep trimmed string)
_AIRFRAME_PARSERS: dict[str, Any] = {
    "departure_time": parse_time_value,
    "arrival_time": parse_time_value,
    "flight_time": parse_duration_value,
    "flight_counter_start": parse_duration_value,
    "flight_counter_end": parse_duration_value,
    "engine_counter_start": parse_duration_value,
    "engine_counter_end": parse_duration_value,
    "landing_count": parse_int_value,
    "passenger_count": parse_int_value,
}

# How far apart a row's start counter may be from the previous row's end
# counter before a continuity warning is raised.
_COUNTER_TOLERANCE = 0.05


@dataclass
class AirframeImportResult:
    imported: int = 0
    subtotals: int = 0
    skipped: list[tuple[int, str]] = field(default_factory=list)
    parse_warnings: list[tuple[int, str, str, str]] = field(default_factory=list)
    # (row_num, counter_label, previous_end, this_start)
    continuity_warnings: list[tuple[int, str, float, float]] = field(
        default_factory=list
    )
    has_opening_counters: bool = False


def propose_airframe_mapping(
    parsed: ParsedFile, saved: list[Any]
) -> tuple[dict[str, str], str]:
    """Return (mapping, match_type) — exact fingerprint reuse, else aliases."""
    for m in saved:
        if m.source_fingerprint == parsed.fingerprint:
            stored = json.loads(m.column_mapping)
            mapping = {
                col: stored.get(col, "ignore")
                if stored.get(col) in AIRFRAME_TARGET_FIELDS
                else "ignore"
                for col in parsed.norm_cols
            }
            return mapping, "exact"
    mapping = {col: _AIRFRAME_ALIASES.get(col, "ignore") for col in parsed.norm_cols}
    return mapping, "alias"


_HINT_SAMPLE_ROWS = 25

_TYPE_NAMES: dict[str, str] = {
    "departure_time": "time",
    "arrival_time": "time",
    "flight_time": "duration",
    "flight_counter_start": "counter value",
    "flight_counter_end": "counter value",
    "engine_counter_start": "counter value",
    "engine_counter_end": "counter value",
    "landing_count": "whole number",
    "passenger_count": "whole number",
}


def airframe_type_hints(parsed: ParsedFile, mapping: dict[str, str]) -> dict[str, str]:
    """{col: hint} where sample data fails to parse as the proposed type."""
    col_index = {col: i for i, col in enumerate(parsed.norm_cols)}
    hints: dict[str, str] = {}
    for col, target in mapping.items():
        parser = _AIRFRAME_PARSERS.get(target)
        idx = col_index.get(col)
        if parser is None or idx is None:
            continue
        sample = [
            row[idx]
            for row in parsed.data_rows[:_HINT_SAMPLE_ROWS]
            if idx < len(row) and row[idx] is not None and str(row[idx]).strip()
        ]
        if not sample:
            continue
        failed = [v for v in sample if parser(v) is None]
        if failed:
            example = str(failed[0])[:30]
            hints[col] = (
                f"Sample data doesn't look like a {_TYPE_NAMES[target]} "
                f"(e.g. {example!r})"
            )
    return hints


def _clean_icao(raw: Any) -> str:
    """Normalise a place cell to the 4-char ICAO field; ZZZZ when unusable."""
    val = str(raw).strip().upper() if raw is not None else ""
    if not val:
        return "ZZZZ"
    return val[:4]


def _is_subtotal(row: list[Any], date_idx: int | None) -> bool:
    from pilots.logbook_import import _is_subtotal_row  # pyright: ignore[reportMissingImports]

    return _is_subtotal_row(row, date_idx)


def execute_airframe_import(
    parsed: ParsedFile,
    mapping: dict[str, str],
    aircraft: Any,
    batch_id: int,
    opening_counters: dict[str, float | None] | None = None,
) -> AirframeImportResult:
    """Create FlightEntry (+ FlightCrew) rows from *parsed* using *mapping*.

    Rows are added to db.session but NOT committed — the caller commits after
    updating the batch record.  Counter continuity is checked in date order
    against the previous imported row (and the opening counters, if given),
    producing warnings rather than errors.
    """
    from models import CrewRole, FlightCrew, FlightEntry, db  # pyright: ignore[reportMissingImports]

    result = AirframeImportResult()
    col_index = {col: i for i, col in enumerate(parsed.norm_cols)}
    date_idx = next(
        (col_index[c] for c, t in mapping.items() if t == "date" and c in col_index),
        None,
    )

    def _get(row: list[Any], col: str) -> Any:
        i = col_index.get(col)
        return row[i] if i is not None and i < len(row) else None

    rows: list[tuple[int, dict[str, Any], str | None]] = []
    for row_num, row in enumerate(parsed.data_rows, start=1):
        if _is_subtotal(row, date_idx):
            result.subtotals += 1
            continue

        date_val: date | None = None
        for col, target in mapping.items():
            if target == "date":
                date_val = parse_date_value(_get(row, col))
                break
        if date_val is None:
            raw_date = (
                row[date_idx] if date_idx is not None and date_idx < len(row) else None
            )
            result.skipped.append((row_num, f"unparseable date: {raw_date!r}"))
            continue

        fields: dict[str, Any] = {"date": date_val}
        crew_name: str | None = None
        for col, target in mapping.items():
            if target in ("ignore", "date"):
                continue
            raw = _get(row, col)
            if target == "crew_name":
                crew_name = str(raw).strip() if raw is not None else None
                crew_name = crew_name or None
                continue
            if target in ("departure_icao", "arrival_icao"):
                fields[target] = _clean_icao(raw)
                continue
            parser = _AIRFRAME_PARSERS.get(target)
            if parser is not None:
                parsed_val = parser(raw)
                if parsed_val is None and raw is not None and str(raw).strip():
                    result.parse_warnings.append(
                        (row_num, col, target, repr(str(raw)[:40]))
                    )
                fields[target] = parsed_val
            else:  # nature_of_flight, notes — free text
                val = str(raw).strip() if raw is not None else None
                fields[target] = val or None
        rows.append((row_num, fields, crew_name))

    # Continuity checks run in chronological order (file order as tiebreaker).
    rows.sort(key=lambda item: (item[1]["date"], item[0]))
    prev_end: dict[str, float | None] = {
        "flight": (opening_counters or {}).get("flight"),
        "engine": (opening_counters or {}).get("engine"),
    }
    for row_num, fields, _crew in rows:
        for kind, start_key, end_key in (
            ("flight", "flight_counter_start", "flight_counter_end"),
            ("engine", "engine_counter_start", "engine_counter_end"),
        ):
            start = fields.get(start_key)
            prev = prev_end[kind]
            if (
                start is not None
                and prev is not None
                and abs(start - prev) > _COUNTER_TOLERANCE
            ):
                result.continuity_warnings.append((row_num, kind, prev, start))
            if fields.get(end_key) is not None:
                prev_end[kind] = fields[end_key]

    earliest = rows[0][1]["date"] if rows else date.today()
    if opening_counters and any(v is not None for v in opening_counters.values()):
        # Baseline entry seeding the counters: zero-length deltas so hours
        # statistics are unaffected, dated before the first imported flight.
        baseline = FlightEntry(
            aircraft_id=aircraft.id,
            airframe_import_batch_id=batch_id,
            source="import",
            date=earliest - timedelta(days=1),
            departure_icao="ZZZZ",
            arrival_icao="ZZZZ",
            flight_time_counter_start=opening_counters.get("flight"),
            flight_time_counter_end=opening_counters.get("flight"),
            engine_time_counter_start=opening_counters.get("engine"),
            engine_time_counter_end=opening_counters.get("engine"),
            notes="Opening counters (imported)",
        )
        db.session.add(baseline)
        result.has_opening_counters = True

    for _row_num, fields, crew_name in rows:
        fe = FlightEntry(
            aircraft_id=aircraft.id,
            airframe_import_batch_id=batch_id,
            source="import",
            date=fields["date"],
            departure_icao=fields.get("departure_icao") or "ZZZZ",
            arrival_icao=fields.get("arrival_icao") or "ZZZZ",
            departure_time=fields.get("departure_time"),
            arrival_time=fields.get("arrival_time"),
            flight_time=fields.get("flight_time"),
            flight_time_counter_start=fields.get("flight_counter_start"),
            flight_time_counter_end=fields.get("flight_counter_end"),
            engine_time_counter_start=fields.get("engine_counter_start"),
            engine_time_counter_end=fields.get("engine_counter_end"),
            landing_count=fields.get("landing_count"),
            passenger_count=fields.get("passenger_count"),
            nature_of_flight=fields.get("nature_of_flight"),
            notes=fields.get("notes"),
        )
        db.session.add(fe)
        if crew_name:
            db.session.flush()
            db.session.add(
                FlightCrew(
                    flight_id=fe.id,
                    user_id=None,
                    name=crew_name,
                    role=CrewRole.PIC,
                    sort_order=0,
                )
            )
        result.imported += 1

    return result
