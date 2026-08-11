"""Bulk import of a historical airframe logbook (CSV/Excel) for one aircraft.

Reuses the Phase 28 pilot-logbook machinery wholesale — file parsing, header
auto-detection, subtotal-row skipping, value parsers — mapped onto
Flight fields.  Counter continuity is validated with per-row warnings
(historical paper logs often carry small corrections), free-text pilot names
are written to Flight.pic_name (pic_user_id left NULL), and an optional "opening
counters" baseline supports importing from a cutover date forward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
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
    # (row_num, reason) — rows that matched a flight already in this aircraft's log
    duplicates: list[tuple[int, str]] = field(default_factory=list)
    parse_warnings: list[tuple[int, str, str, str]] = field(default_factory=list)
    # (row_num, counter_label, previous_end, this_start)
    continuity_warnings: list[tuple[int, str, float, float]] = field(
        default_factory=list
    )
    has_opening_counters: bool = False


def _num(v: Any) -> float | None:
    # Numeric columns come back as decimal.Decimal; freshly parsed values are
    # plain float — normalise both sides before building/comparing keys (see
    # the matching note in pilots/logbook_import.py for why this matters).
    return None if v is None else float(v)


def _dup_key(fields: dict[str, Any]) -> tuple[Any, ...]:
    """Exact-duplicate key: date + route + duration + landings. Re-importing
    the same airframe logbook file (by mistake, or deliberately after
    appending new rows) must only add genuinely new flights, not double up
    everything already imported."""
    return (
        fields["date"],
        fields.get("departure_icao") or "ZZZZ",
        fields.get("arrival_icao") or "ZZZZ",
        _num(fields.get("flight_time")),
        fields.get("landing_count"),
    )


def _fetch_existing_dedup_keys(aircraft_id: int) -> set[tuple[Any, ...]]:
    from models import Flight, db  # pyright: ignore[reportMissingImports]

    return {
        (row[0], row[1], row[2], _num(row[3]), row[4])
        for row in db.session.query(
            Flight.date,
            Flight.departure_icao,
            Flight.arrival_icao,
            Flight.flight_time,
            Flight.landing_count,
        ).filter_by(aircraft_id=aircraft_id)
    }


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
    from pilots.logbook_import import (
        _is_subtotal_row,  # pyright: ignore[reportMissingImports]
    )

    return _is_subtotal_row(row, date_idx)


def _row_get(row: list[Any], col_index: dict[str, int], col: str) -> Any:
    i = col_index.get(col)
    return row[i] if i is not None and i < len(row) else None


def _parse_row_date(
    row: list[Any], mapping: dict[str, str], col_index: dict[str, int]
) -> date | None:
    for col, target in mapping.items():
        if target == "date":
            return parse_date_value(_row_get(row, col_index, col))
    return None


def _build_airframe_fields(
    row: list[Any],
    mapping: dict[str, str],
    col_index: dict[str, int],
    date_val: date,
) -> tuple[dict[str, Any], str | None, list[tuple[str, str, str]]]:
    """Build Flight field values (+ crew name) for one data row.

    Returns (fields, crew_name, parse_warnings), where parse_warnings is a
    list of (col, target, raw_repr) for non-empty cells that couldn't be
    parsed as their target field's type. Shared by the normal import pass
    and the near-match conflict finder below, so the two can never compute a
    row's fields differently.
    """
    fields: dict[str, Any] = {"date": date_val}
    crew_name: str | None = None
    parse_warnings: list[tuple[str, str, str]] = []
    for col, target in mapping.items():
        if target in ("ignore", "date"):
            continue
        raw = _row_get(row, col_index, col)
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
                parse_warnings.append((col, target, repr(str(raw)[:40])))
            fields[target] = parsed_val
        else:  # nature_of_flight, notes — free text
            val = str(raw).strip() if raw is not None else None
            fields[target] = val or None
    return fields, crew_name, parse_warnings


def _fields_to_flight_entry_kwargs(fields: dict[str, Any]) -> dict[str, Any]:
    """Map parsed row *fields* (mapping-target-field keys) to Flight
    constructor kwargs (model-column keys) — the two differ for the counter
    fields (flight_counter_end → flight_time_counter_end etc). Shared by the
    normal insert path and the review step's overwrite/new-entry handling."""
    return {
        "date": fields["date"],
        "departure_icao": fields.get("departure_icao") or "ZZZZ",
        "arrival_icao": fields.get("arrival_icao") or "ZZZZ",
        "departure_time": fields.get("departure_time"),
        "arrival_time": fields.get("arrival_time"),
        "flight_time": fields.get("flight_time"),
        "flight_time_counter_start": fields.get("flight_counter_start"),
        "flight_time_counter_end": fields.get("flight_counter_end"),
        "engine_time_counter_start": fields.get("engine_counter_start"),
        "engine_time_counter_end": fields.get("engine_counter_end"),
        "landing_count": fields.get("landing_count"),
        "passenger_count": fields.get("passenger_count"),
        "nature_of_flight": fields.get("nature_of_flight"),
        "notes": fields.get("notes"),
    }


def execute_airframe_import(
    parsed: ParsedFile,
    mapping: dict[str, str],
    aircraft: Any,
    batch_id: int,
    opening_counters: dict[str, float | None] | None = None,
    skip_row_nums: set[int] | None = None,
) -> AirframeImportResult:
    """Create Flight rows from *parsed* using *mapping*.

    Rows are added to db.session but NOT committed — the caller commits after
    updating the batch record.  Counter continuity is checked in date order
    against the previous imported row (and the opening counters, if given),
    producing warnings rather than errors. *skip_row_nums* lets the caller
    carve out rows it's handling separately — near-match conflicts routed to
    the interactive review step in app/flights/routes.py — so they're
    excluded entirely from this pass.
    """
    from models import Flight, db  # pyright: ignore[reportMissingImports]

    result = AirframeImportResult()
    col_index = {col: i for i, col in enumerate(parsed.norm_cols)}
    date_idx = next(
        (col_index[c] for c, t in mapping.items() if t == "date" and c in col_index),
        None,
    )
    skip_row_nums = skip_row_nums or set()

    existing_keys = _fetch_existing_dedup_keys(aircraft.id)
    rows: list[tuple[int, dict[str, Any], str | None]] = []
    for row_num, row in enumerate(parsed.data_rows, start=1):
        if row_num in skip_row_nums:
            continue
        if _is_subtotal(row, date_idx):
            result.subtotals += 1
            continue

        date_val = _parse_row_date(row, mapping, col_index)
        if date_val is None:
            raw_date = (
                row[date_idx] if date_idx is not None and date_idx < len(row) else None
            )
            result.skipped.append((row_num, f"unparseable date: {raw_date!r}"))
            continue

        fields, crew_name, parse_warnings = _build_airframe_fields(
            row, mapping, col_index, date_val
        )
        for col, target, raw_repr in parse_warnings:
            result.parse_warnings.append((row_num, col, target, raw_repr))

        dup_key = _dup_key(fields)
        if dup_key in existing_keys:
            result.duplicates.append(
                (
                    row_num,
                    _(
                        "matches a flight already in this aircraft's log "
                        "(same date, route, duration and landings)"
                    ),
                )
            )
            continue
        existing_keys.add(dup_key)

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
        baseline = Flight(
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
        fe = Flight(
            aircraft_id=aircraft.id,
            airframe_import_batch_id=batch_id,
            source="import",
            pic_name=crew_name,
            **_fields_to_flight_entry_kwargs(fields),
        )
        db.session.add(fe)
        result.imported += 1

    return result


# ── Near-match conflict detection (possible corrections) ───────────────────────

_CANDIDATE_MIN_SCORE = 3
_CANDIDATE_TIME_TOLERANCE_MINUTES = 120
_CANDIDATE_DURATION_TOLERANCE = 1.0
_CANDIDATE_COUNTER_TOLERANCE = 0.3


@dataclass
class AirframeConflictRow:
    """A parsed row that isn't an exact duplicate but scores highly enough
    against one or more existing Flight rows to plausibly be an edited
    version of one of them — needs a human decision, not a guess."""

    row_num: int
    fields: dict[str, Any]
    crew_name: str | None
    candidates: list[tuple[int, int]]  # (score, existing_flight_id), best first


def _time_close(t1: time | None, t2: time | None, tolerance_minutes: int) -> bool:
    if t1 is None or t2 is None:
        return False
    m1 = t1.hour * 60 + t1.minute
    m2 = t2.hour * 60 + t2.minute
    return abs(m1 - m2) <= tolerance_minutes


def _score_airframe_candidate(fields: dict[str, Any], existing: Any) -> int:
    """Score how likely *existing* (a Flight row) is the same real-world
    flight as *fields* (a freshly parsed row), across 7 points: departure,
    arrival, departure time, arrival time, duration, landings, flight
    counter reading. A point only counts toward the score if both sides
    have meaningful data for it — "ZZZZ" (unmapped ICAO) and missing values
    are neutral, never a mismatch.
    """
    score = 0

    dep_new = (fields.get("departure_icao") or "ZZZZ").strip().upper()
    dep_old = (existing.departure_icao or "ZZZZ").strip().upper()
    if dep_new != "ZZZZ" and dep_old != "ZZZZ" and dep_new == dep_old:
        score += 1

    arr_new = (fields.get("arrival_icao") or "ZZZZ").strip().upper()
    arr_old = (existing.arrival_icao or "ZZZZ").strip().upper()
    if arr_new != "ZZZZ" and arr_old != "ZZZZ" and arr_new == arr_old:
        score += 1

    if _time_close(
        fields.get("departure_time"),
        existing.departure_time,
        _CANDIDATE_TIME_TOLERANCE_MINUTES,
    ):
        score += 1

    if _time_close(
        fields.get("arrival_time"),
        existing.arrival_time,
        _CANDIDATE_TIME_TOLERANCE_MINUTES,
    ):
        score += 1

    dur_new = _num(fields.get("flight_time"))
    dur_old = _num(existing.flight_time)
    if (
        dur_new is not None
        and dur_old is not None
        and abs(dur_new - dur_old) <= _CANDIDATE_DURATION_TOLERANCE
    ):
        score += 1

    land_new = fields.get("landing_count")
    land_old = existing.landing_count
    if land_new is not None and land_old is not None and land_new == land_old:
        score += 1

    ctr_new = _num(fields.get("flight_counter_end"))
    ctr_old = _num(existing.flight_time_counter_end)
    if (
        ctr_new is not None
        and ctr_old is not None
        and abs(ctr_new - ctr_old) <= _CANDIDATE_COUNTER_TOLERANCE
    ):
        score += 1

    return score


def find_conflicting_airframe_rows(
    parsed: ParsedFile,
    mapping: dict[str, str],
    aircraft_id: int,
    exclude_row_nums: set[int] | None = None,
) -> list[AirframeConflictRow]:
    """Find rows that aren't an exact duplicate but plausibly match an
    existing Flight row closely enough (score >= _CANDIDATE_MIN_SCORE) to
    need a human decision: keep the existing entry, overwrite it with the
    new data, or import as a genuinely separate new flight.

    Skips subtotal rows, rows with an unparseable date, and exact duplicates
    (same as execute_airframe_import's own dedup), plus any row_num in
    *exclude_row_nums* — typically rows already resolved in an earlier pass
    of this same review.
    """
    from models import Flight  # pyright: ignore[reportMissingImports]

    exclude_row_nums = exclude_row_nums or set()
    col_index = {col: i for i, col in enumerate(parsed.norm_cols)}
    date_idx = next(
        (col_index[c] for c, t in mapping.items() if t == "date" and c in col_index),
        None,
    )
    existing_keys = _fetch_existing_dedup_keys(aircraft_id)
    conflicts: list[AirframeConflictRow] = []

    for row_num, row in enumerate(parsed.data_rows, start=1):
        if row_num in exclude_row_nums:
            continue
        if _is_subtotal(row, date_idx):
            continue
        date_val = _parse_row_date(row, mapping, col_index)
        if date_val is None:
            continue

        fields, crew_name, _parse_warnings = _build_airframe_fields(
            row, mapping, col_index, date_val
        )
        if _dup_key(fields) in existing_keys:
            continue  # exact duplicate — execute_airframe_import's own dedup handles this

        same_day = Flight.query.filter_by(aircraft_id=aircraft_id, date=date_val).all()
        scored = [
            (score, existing.id)
            for existing in same_day
            if (score := _score_airframe_candidate(fields, existing))
            >= _CANDIDATE_MIN_SCORE
        ]
        if scored:
            scored.sort(key=lambda t: -t[0])
            conflicts.append(
                AirframeConflictRow(
                    row_num=row_num,
                    fields=fields,
                    crew_name=crew_name,
                    candidates=scored,
                )
            )

    return conflicts
