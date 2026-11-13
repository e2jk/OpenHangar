"""Budgets for imported spreadsheets, checked before they are expanded (audit S08).

An .xlsx file is a zip archive: a 10 MB upload can declare gigabytes of
XML, or a worksheet a million rows wide. The upload-size limits only bound
the compressed bytes, so these budgets bound what parsing may expand to.
They are generous for real logbooks and maintenance programmes (a few
thousand rows, a few dozen columns) and refuse a crafted file cheaply,
before openpyxl loads it.

Declared entry sizes can be trusted: Python's zip reader never returns more
than an entry's declared size (and fails its CRC check if the data says
otherwise), so checking them bounds what openpyxl can read.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Iterator
from typing import Any

from flask_babel import format_number  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

MAX_ARCHIVE_ENTRIES = 2000
MAX_EXPANDED_BYTES = 100 * 1024 * 1024  # all entries together, once expanded
# Expansion ratio above which a sizeable entry is refused (real XLSX ~ 5-20).
MAX_ENTRY_RATIO = 200
_RATIO_MIN_BYTES = 1024 * 1024  # small entries may compress very well

MAX_ROWS = 50_000  # rows kept per sheet (a lifetime logbook is far below)
MAX_COLUMNS = 120  # columns read per row; columns beyond are ignored
MAX_SHEETS_SCANNED = 20  # sheets examined when guessing the logbook sheet


def too_large_message() -> str:
    message: str = _(
        "This spreadsheet is too large to import (at most %(rows)s rows). "
        "Split it into several files, or remove unused sheets and formatting.",
        rows=format_number(MAX_ROWS),
    )
    return message


def check_xlsx_archive(data: bytes, *, max_expanded: int = MAX_EXPANDED_BYTES) -> None:
    """Refuse (ValueError) an .xlsx whose archive would expand beyond the
    budgets. Not a zip at all is left to the spreadsheet reader to report."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(too_large_message())
        expanded = 0
        for entry in entries:
            expanded += entry.file_size
            if expanded > max_expanded:
                raise ValueError(too_large_message())
            if entry.file_size > _RATIO_MIN_BYTES and entry.file_size > (
                MAX_ENTRY_RATIO * max(entry.compress_size, 1)
            ):
                raise ValueError(too_large_message())


def bounded_rows(rows: Iterable[Iterable[Any]]) -> list[list[Any]]:
    """The rows of a sheet as lists, refusing (ValueError) more than MAX_ROWS.

    Empty rows (all None) at the end are not stored, so a sheet whose
    formatting reaches far below its data costs no memory (only reading time,
    bounded by the archive budget); an empty row between data rows is kept as
    it was read. Rows longer than MAX_COLUMNS are cut: read sheets with
    `max_col=MAX_COLUMNS` so the cells beyond are not even built.
    """
    kept: list[list[Any]] = []
    pending_widths: list[int] = []  # empty rows read since the last data row
    overflow = False  # more empty rows than could ever be kept
    for row in rows:
        values = list(row)[:MAX_COLUMNS]
        if all(v is None for v in values):
            if len(kept) + len(pending_widths) < MAX_ROWS:
                pending_widths.append(len(values))
            else:
                overflow = True  # fine at the end; refused if data follows
            continue
        if overflow or len(kept) + len(pending_widths) + 1 > MAX_ROWS:
            raise ValueError(too_large_message())
        kept.extend([None] * width for width in pending_widths)
        pending_widths = []
        kept.append(values)
    return kept


def bounded_csv_rows(reader: Iterator[list[str]]) -> list[list[Any]]:
    """Like bounded_rows, for csv.reader output: also refuses a row with more
    than MAX_COLUMNS non-empty fields (columns beyond would be data lost)."""

    def checked() -> Iterator[list[str]]:
        for row in reader:
            if len(row) > MAX_COLUMNS and any(v.strip() for v in row[MAX_COLUMNS:]):
                raise ValueError(too_large_message())
            yield row

    return bounded_rows(checked())
