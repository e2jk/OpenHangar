"""Fuzz the GeoJSON coordinate extractor (utils.py's _coords_from_geojson).

GpsTrack.geojson is a DB-stored JSON field with no enforced schema — always
well-formed when written by aircraft/gps_import.py's build_geojson(), but
this function is the read-side of that contract and shouldn't crash
track-image/GIF rendering with an unhandled 500 if it ever isn't (corrupted
data, a future write path that doesn't go through build_geojson()). Feeds
fuzzed text through json.loads() so the input covers the full range of JSON
shapes a corrupted field could hold, not just already-well-formed GeoJSON.
"""

import json
import math
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["utils"]):
    from utils import _coords_from_geojson  # noqa: E402


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    text = fdp.ConsumeUnicodeNoSurrogates(256)
    try:
        geojson = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return  # not valid JSON — GpsTrack.geojson is always parsed JSON already

    if not (geojson is None or isinstance(geojson, dict)):
        return  # function's own contract is dict | None; a bare list/str/etc.
        # isn't a shape build_geojson() or this function's .get()-based
        # dispatch ever produces — skip rather than assert on an input
        # outside the documented type

    result = _coords_from_geojson(geojson)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, tuple) and len(item) == 2
        assert isinstance(item[0], float) and isinstance(item[1], float)
        assert math.isfinite(item[0]) and math.isfinite(item[1]), (
            f"non-finite coordinate leaked: {item!r}"
        )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
