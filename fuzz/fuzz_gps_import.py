"""Fuzz the GPS track file parser (aircraft/gps_import.py).

Untrusted-file-input surface: arbitrary uploaded GPX/KML/Garmin-CSV bytes
reach parse_gps_file() directly from the aircraft GPS-import upload route.
Classic fuzz target for malformed-file crashes — distinct risk profile from
the spreadsheet parsing covered by fuzz_logbook_parse_file.
"""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# instrument_imports() gives Atheris real coverage-guided feedback from inside
# gps_import.py itself, not just the harness wrapper — verified locally to
# take coverage from a flat 2 to 51+ and turn on genuine corpus-driven
# exploration (plain @instrument_func on TestOneInput alone left the fuzzer
# blind to every branch inside the parser).
with atheris.instrument_imports():
    from aircraft.gps_import import ParsedGpsFile, parse_gps_file  # noqa: E402

_FILENAMES = ("track.gpx", "track.kml", "track.csv", "track")


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    filename = _FILENAMES[fdp.ConsumeIntInRange(0, len(_FILENAMES) - 1)]
    body = fdp.ConsumeBytes(fdp.remaining_bytes())

    try:
        result = parse_gps_file(body, filename)
    except ValueError:
        return  # expected: unsupported format or invalid file content rejected
    assert isinstance(result, ParsedGpsFile), f"unexpected return type: {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
