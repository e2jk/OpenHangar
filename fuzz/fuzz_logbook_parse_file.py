"""Fuzz the CSV/XLSX logbook file parser (pilots/logbook_import.py).

Untrusted-file-input surface: arbitrary uploaded spreadsheet bytes reach
parse_file() directly from the pilot logbook import upload route.
"""

import sys
from pathlib import Path

import atheris
from flask import Flask
from flask_babel import Babel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# instrument_imports() gives Atheris real coverage-guided feedback from inside
# logbook_import.py itself, not just the harness wrapper — verified locally
# (see fuzz_gps_import.py) to turn on genuine corpus-driven exploration
# instead of blind mutation.
with atheris.instrument_imports():
    from pilots.logbook_import import ParsedFile, parse_file  # noqa: E402

# _preferred_sheet_names() (reached via the .xlsx path) calls flask_babel's
# gettext, which needs an application context. A minimal Babel-only app is
# enough — no DB, no other extensions — and is pushed once up front rather
# than per-iteration, so it doesn't slow down the fuzzing loop.
_app = Flask(__name__)
Babel(_app)
_app.app_context().push()

_FILENAMES = ("logbook.csv", "logbook.xlsx", "logbook.xls", "logbook")


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    filename = _FILENAMES[fdp.ConsumeIntInRange(0, len(_FILENAMES) - 1)]
    body = fdp.ConsumeBytes(fdp.remaining_bytes())

    try:
        result = parse_file(body, filename)
    except ValueError:
        return  # expected: malformed/unsupported file rejected with a friendly error
    assert isinstance(result, ParsedFile), f"unexpected return type: {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
