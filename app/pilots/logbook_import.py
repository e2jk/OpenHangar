"""Pilot logbook import — parsing, normalisation, mapping, and execution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import openpyxl  # pyright: ignore[reportMissingImports]

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_FIELDS: list[str] = [
    "date",
    "aircraft_type",
    "aircraft_registration",
    "departure_place",
    "departure_time",
    "arrival_place",
    "arrival_time",
    "pic_name",
    "night_time",
    "instrument_time",
    "landings_day",
    "landings_night",
    "single_pilot_se",
    "single_pilot_me",
    "multi_pilot",
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
    "remarks",
]

# Normalised source column name → target field.
# Positional disambiguation suffixes (_2, _3 …) are applied before lookup,
# so "time" is the first TIME column (departure) and "time_2" is the second (arrival).
_ALIASES: dict[str, str] = {
    # EASA standard logbook column names (normalised)
    "date dd/mm/yy": "date",
    "date": "date",
    "aircraft type": "aircraft_type",
    "type": "aircraft_type",
    "aircraft registration number": "aircraft_registration",
    "registration": "aircraft_registration",
    "reg": "aircraft_registration",
    "from": "departure_place",
    "departure": "departure_place",
    "dep": "departure_place",
    "time": "departure_time",  # first TIME → departure
    "time_2": "arrival_time",  # second TIME → arrival
    "departure time": "departure_time",
    "arrival time": "arrival_time",
    "to": "arrival_place",
    "arrival": "arrival_place",
    "arr": "arrival_place",
    "pic name": "pic_name",
    "pic name (if not student pilot)": "pic_name",
    "captain": "pic_name",
    # Landings group — first DAY / NIGHT pair
    "day": "landings_day",
    "night": "landings_night",
    # Operational conditions group — second DAY / NIGHT pair (positional suffix)
    "day_2": "ignore",  # cross-country day — not a logbook field
    "night_2": "night_time",  # night flying time
    # Aircraft category
    "se": "single_pilot_se",
    "single engine": "single_pilot_se",
    "single pilot se": "single_pilot_se",
    "me": "single_pilot_me",
    "multi engine": "single_pilot_me",
    "single pilot me": "single_pilot_me",
    "multi pilot": "multi_pilot",
    "mp": "multi_pilot",
    # Pilot function
    "pic": "function_pic",
    "p1": "function_pic",
    "co-pic": "function_copilot",
    "co-pilot": "function_copilot",
    "copilot": "function_copilot",
    "p2": "function_copilot",
    "dual received": "function_dual",
    "dual": "function_dual",
    "student": "function_dual",
    "instructor": "function_instructor",
    "fi": "function_instructor",
    # Catch-all
    "total flight time": "ignore",
    "total": "ignore",
    "cross-country": "ignore",
    "no. istr. appr.": "ignore",
    "ifr approaches": "ignore",
    "remarks": "remarks",
    "notes": "remarks",
    "comments": "remarks",
    "instrument time": "instrument_time",
    "ifr": "instrument_time",
    "instrument": "instrument_time",
    "night time": "night_time",
}

# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class ParsedFile:
    """Result of parsing an uploaded logbook file."""

    norm_cols: list[str]  # normalised + disambiguated column keys
    raw_cols: list[str]  # original column labels (for display)
    header_row_index: int  # 0-based row index of the detected header
    data_rows: list[list[Any]]  # all rows after the header (including subtotals)
    fingerprint: str  # SHA-256 of norm_cols


@dataclass
class MappingProposal:
    """Proposed column mapping and how it was derived."""

    mapping: dict[str, str]  # norm_col_key → target_field or "ignore"
    match_type: str  # "exact", "fuzzy", "alias"
    fuzzy_score: float = 0.0  # 0.0–1.0, only meaningful for "fuzzy"
    matched_mapping_id: int | None = None


@dataclass
class ImportResult:
    """Summary returned after executing an import."""

    imported: int = 0
    subtotals: int = 0
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (row_num, reason)
    has_opening_balance: bool = False


# ── Normalisation ─────────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    """Strip, collapse whitespace, lower-case."""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _disambiguate(names: list[str]) -> list[str]:
    """Append _2, _3 … to duplicate names to make them unique."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for n in names:
        if n in seen:
            seen[n] += 1
            result.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 1
            result.append(n)
    return result


def _fingerprint(norm_cols: list[str]) -> str:
    payload = json.dumps(norm_cols, ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Header detection ──────────────────────────────────────────────────────────


def _is_header_row(row: list[Any]) -> bool:
    """True if ≥ 50 % of non-empty cells are non-numeric strings and ≥ 4 non-empty."""
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if len(non_empty) < 4:
        return False
    string_like = [
        c for c in non_empty if isinstance(c, str) and not _is_numeric_str(str(c))
    ]
    return len(string_like) / len(non_empty) >= 0.5


def _is_numeric_str(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _find_header_row(rows: list[list[Any]], max_scan: int = 20) -> int | None:
    """Return 0-based index of the header row, or None if not found."""
    for i, row in enumerate(rows[:max_scan]):
        if _is_header_row(row):
            return i
    return None


# ── File parsing ──────────────────────────────────────────────────────────────


def parse_file(data: bytes, filename: str) -> ParsedFile:
    """Parse bytes from an uploaded file into a ParsedFile.

    Raises ValueError with a user-friendly message on format errors.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("xlsx", "xls"):
        return _parse_excel(data, filename)
    if ext == "csv":
        return _parse_csv(data, filename)
    raise ValueError(
        f"Unsupported file format: .{ext} — please upload a .csv or .xlsx file."
    )


def _parse_excel(data: bytes, filename: str) -> ParsedFile:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Could not open Excel file: {exc}") from exc

    ws = wb.active
    all_rows: list[list[Any]] = []
    for row in ws.iter_rows(values_only=True):
        all_rows.append(list(row))
    wb.close()

    return _build_parsed_file(all_rows, filename)


def _parse_csv(data: bytes, filename: str) -> ParsedFile:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    all_rows: list[list[Any]] = list(reader)
    return _build_parsed_file(all_rows, filename)


def _build_parsed_file(all_rows: list[list[Any]], filename: str) -> ParsedFile:
    header_idx = _find_header_row(all_rows)
    if header_idx is None:
        raise ValueError(
            "Could not detect a header row in this file. "
            "Make sure the file contains column names."
        )

    raw_header = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
    norm_raw = [_norm(c) for c in raw_header]
    norm_cols = _disambiguate(norm_raw)
    data_rows = all_rows[header_idx + 1 :]

    return ParsedFile(
        norm_cols=norm_cols,
        raw_cols=raw_header,
        header_row_index=header_idx,
        data_rows=data_rows,
        fingerprint=_fingerprint(norm_cols),
    )


# ── Mapping proposal ──────────────────────────────────────────────────────────


def propose_mapping(
    parsed: ParsedFile,
    existing_mappings: list[Any],  # list[LogbookImportMapping]
) -> MappingProposal:
    """Return the best mapping proposal for *parsed*, checking saved mappings first."""
    # 1. Exact fingerprint match
    for m in existing_mappings:
        if m.source_fingerprint == parsed.fingerprint:
            return MappingProposal(
                mapping=json.loads(m.column_mapping),
                match_type="exact",
                matched_mapping_id=m.id,
            )

    # 2. Fuzzy match — best overlap among saved mappings
    best_score = 0.0
    best_m = None
    new_set = set(parsed.norm_cols)
    for m in existing_mappings:
        saved_cols: list[str] = json.loads(m.source_columns)
        saved_set = set(saved_cols)
        if not saved_set:
            continue
        overlap = len(new_set & saved_set)
        score = overlap / max(len(new_set), len(saved_set))
        if score > best_score:
            best_score = score
            best_m = m

    if best_m is not None and best_score >= 0.6:
        saved_map: dict[str, str] = json.loads(best_m.column_mapping)
        # Build a mapping for the new columns, falling back to alias for unmatched ones
        merged = _alias_mapping(parsed.norm_cols)
        for col in parsed.norm_cols:
            if col in saved_map:
                merged[col] = saved_map[col]
        return MappingProposal(
            mapping=merged,
            match_type="fuzzy",
            fuzzy_score=best_score,
            matched_mapping_id=best_m.id,
        )

    # 3. Alias-only auto-mapping
    return MappingProposal(
        mapping=_alias_mapping(parsed.norm_cols),
        match_type="alias",
    )


def _alias_mapping(norm_cols: list[str]) -> dict[str, str]:
    """Apply the built-in alias table; unknown columns default to 'ignore'."""
    result: dict[str, str] = {}
    for col in norm_cols:
        result[col] = _ALIASES.get(col, "ignore")
    return result


# ── Subtotal detection ────────────────────────────────────────────────────────


def _is_subtotal_row(row: list[Any], date_col_idx: int | None) -> bool:
    if date_col_idx is None:
        return False
    if date_col_idx >= len(row):
        return True
    val = row[date_col_idx]
    if isinstance(val, timedelta):
        return True
    if val is None or (isinstance(val, str) and not val.strip()):
        return True
    if isinstance(val, str) and "total" in val.lower():
        return True
    return False


# ── Value parsing ─────────────────────────────────────────────────────────────


def parse_date_value(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, (int, float)):
        # Excel serial date number — openpyxl returns datetime; guard anyway
        return None
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_time_value(val: Any) -> time | None:
    """Parse a time-of-day value (HH:MM or Python time)."""
    if isinstance(val, time):
        return val
    if isinstance(val, datetime):
        return val.time()
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        try:
            return time(int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def parse_duration_value(val: Any) -> float | None:
    """Parse a duration into decimal hours (e.g. time(0,42) → 0.7, '1:24' → 1.4)."""
    if isinstance(val, timedelta):
        # Subtotal marker — caller should have already filtered these out
        return None
    if isinstance(val, time):
        return round(val.hour + val.minute / 60, 1)
    if isinstance(val, datetime):
        # Excel sometimes returns a dummy date + the time-of-day
        t = val.time()
        return round(t.hour + t.minute / 60, 1)
    if isinstance(val, (int, float)):
        if val < 0:
            return None
        return round(float(val), 1)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        m = re.match(r"^(\d+):(\d{2})$", s)
        if m:
            return round(int(m.group(1)) + int(m.group(2)) / 60, 1)
        try:
            v = float(s)
            return round(v, 1) if v >= 0 else None
        except ValueError:
            return None
    return None


def parse_int_value(val: Any) -> int | None:
    if isinstance(val, int):
        return val if val >= 0 else None
    if isinstance(val, float):
        return int(val) if val >= 0 else None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


# ── Preview rows ──────────────────────────────────────────────────────────────


def preview_rows(
    parsed: ParsedFile,
    mapping: dict[str, str],
    n: int = 5,
) -> list[dict[str, Any]]:
    """Return up to *n* non-subtotal data rows mapped to target field names."""
    date_idx = _date_col_index(parsed.norm_cols, mapping)
    result: list[dict[str, Any]] = []
    for row in parsed.data_rows:
        if _is_subtotal_row(row, date_idx):
            continue
        mapped: dict[str, Any] = {}
        for i, col in enumerate(parsed.norm_cols):
            target = mapping.get(col, "ignore")
            if target == "ignore":
                continue
            mapped[target] = row[i] if i < len(row) else None
        result.append(mapped)
        if len(result) >= n:
            break
    return result


def _date_col_index(norm_cols: list[str], mapping: dict[str, str]) -> int | None:
    for i, col in enumerate(norm_cols):
        if mapping.get(col) == "date":
            return i
    return None


# ── Import execution ──────────────────────────────────────────────────────────


def execute_import(
    parsed: ParsedFile,
    mapping: dict[str, str],
    pilot_user_id: int,
    batch_id: int,
    opening_balance: dict[str, Any] | None = None,
) -> ImportResult:
    """Create PilotLogbookEntry rows from *parsed* using *mapping*.

    Returns an ImportResult describing what happened.  Entries are added to
    db.session but NOT committed — the caller commits after also saving the
    batch/mapping records.
    """
    from models import PilotLogbookEntry, db  # pyright: ignore[reportMissingImports]

    result = ImportResult()
    date_idx = _date_col_index(parsed.norm_cols, mapping)
    col_index = {col: i for i, col in enumerate(parsed.norm_cols)}

    def _get(row: list[Any], col: str) -> Any:
        i = col_index.get(col)
        return row[i] if i is not None and i < len(row) else None

    entries_to_add: list[PilotLogbookEntry] = []

    for row_num, row in enumerate(parsed.data_rows, start=1):
        if _is_subtotal_row(row, date_idx):
            result.subtotals += 1
            continue

        # Find and parse date
        date_val: date | None = None
        for col, target in mapping.items():
            if target == "date":
                raw = _get(row, col)
                date_val = parse_date_value(raw)
                break

        if date_val is None:
            raw_date = (
                row[date_idx] if date_idx is not None and date_idx < len(row) else None
            )
            result.skipped.append((row_num, f"unparseable date: {raw_date!r}"))
            continue

        # Build entry fields from mapping
        kwargs: dict[str, Any] = {
            "pilot_user_id": pilot_user_id,
            "import_batch_id": batch_id,
            "source": "import",
            "date": date_val,
        }

        for col, target in mapping.items():
            if target in ("ignore", "date"):
                continue
            raw = _get(row, col)
            if target in ("departure_time", "arrival_time"):
                kwargs[target] = parse_time_value(raw)
            elif target in (
                "night_time",
                "instrument_time",
                "single_pilot_se",
                "single_pilot_me",
                "multi_pilot",
                "function_pic",
                "function_copilot",
                "function_dual",
                "function_instructor",
            ):
                kwargs[target] = parse_duration_value(raw)
            elif target in ("landings_day", "landings_night"):
                kwargs[target] = parse_int_value(raw)
            elif target in (
                "aircraft_type",
                "aircraft_registration",
                "departure_place",
                "arrival_place",
                "pic_name",
                "remarks",
            ):
                kwargs[target] = str(raw).strip() if raw is not None else None

        entries_to_add.append(PilotLogbookEntry(**kwargs))

    result.imported = len(entries_to_add)
    for e in entries_to_add:
        db.session.add(e)

    # Opening balance — synthetic entry dated one day before earliest imported
    if opening_balance and any(v for v in opening_balance.values() if v):
        earliest = (
            min(e.date for e in entries_to_add) if entries_to_add else date.today()
        )
        from datetime import timedelta as _td

        balance_date = earliest - _td(days=1)
        balance_entry = PilotLogbookEntry(
            pilot_user_id=pilot_user_id,
            import_batch_id=batch_id,
            source="import",
            date=balance_date,
            remarks="Opening balance (imported)",
            night_time=opening_balance.get("night_time"),
            instrument_time=opening_balance.get("instrument_time"),
            single_pilot_se=opening_balance.get("single_pilot_se"),
            single_pilot_me=opening_balance.get("single_pilot_me"),
            multi_pilot=opening_balance.get("multi_pilot"),
            function_pic=opening_balance.get("function_pic"),
            function_copilot=opening_balance.get("function_copilot"),
            function_dual=opening_balance.get("function_dual"),
            function_instructor=opening_balance.get("function_instructor"),
        )
        db.session.add(balance_entry)
        result.has_opening_balance = True

    return result
