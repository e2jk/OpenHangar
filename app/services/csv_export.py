"""Text-cell protection for CSV files intended to be opened in spreadsheets."""

import unicodedata

_FORMULA_PREFIXES = ("=", "+", "-", "@", "＝", "＋", "－", "＠")


def safe_csv_text(value: str) -> str:
    """Prefix formula-like text with an apostrophe, preserving the original text.

    Spreadsheet readers can ignore leading whitespace/control/format characters.
    Inspect past those characters too, without stripping them from the export.
    Call only for text fields: actual numeric amounts, including negative ones,
    must retain their normal numeric representation. csv.writer still handles
    delimiters, quotes and embedded line breaks separately.
    """
    index = 0
    while index < len(value) and (
        value[index].isspace() or unicodedata.category(value[index]) in ("Cc", "Cf")
    ):
        index += 1
    if value.startswith(("\t", "\r", "\n")) or value.startswith(
        _FORMULA_PREFIXES, index
    ):
        return "'" + value
    return value
