#!/usr/bin/env python3
"""
One-time script: parse Garmin bulk GPS logs → app/data/seed_tracks.json

Run from the repo root:
    python scripts/extract_seed_tracks.py

Reads:  import-samples/airplane-log/Garmin/bulk/*.csv
Writes: app/data/seed_tracks.json

Each flight segment is assigned a unique random date offset so no actual
flight dates are preserved.  Dates are stored relative to _SEED_REF_DATE
(2026-05-09) so _seed_helpers._d() can shift them to real dates at runtime.
"""

import json
import os
import random
import sys
from datetime import date, timedelta

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "app"))

from aircraft.gps_import import (  # noqa: E402
    detect_segments,
    merge_and_sort,
    parse_gps_file,
)

_SEED_REF_DATE = date(2026, 5, 9)
_BULK_DIR = os.path.join(_ROOT, "import-samples", "airplane-log", "Garmin", "bulk")
_OUT_PATH = os.path.join(_ROOT, "app", "data", "seed_tracks.json")

_MAX_COORDS = 200  # points per track in the stored JSON


def _downsample(coords: list, max_pts: int) -> list:
    n = len(coords)
    if n <= max_pts:
        return coords
    stride = n / max_pts
    indices = sorted({round(i * stride) for i in range(max_pts)} | {0, n - 1})
    return [coords[i] for i in indices if i < n]


def main() -> None:
    random.seed(42)

    parsed = []
    for fname in sorted(os.listdir(_BULK_DIR)):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(_BULK_DIR, fname)
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            result = parse_gps_file(data, fname)
        except ValueError as exc:
            print(f"  SKIP {fname}: {exc}")
            continue
        if result.classification == "empty":
            print(f"  empty  {fname}")
        else:
            print(
                f"  {result.classification:17s} {fname}  ({len(result.trackpoints)} pts)"
            )
            parsed.append(result)

    all_pts = merge_and_sort(parsed)
    print(f"\nMerged trackpoints: {len(all_pts)}")

    segments = detect_segments(all_pts)
    print(f"Segments detected:  {len(segments)}")

    tracks = []
    for i, seg in enumerate(segments):
        label = f"{seg.departure_icao or '????'}→{seg.arrival_icao or '????'}"
        if seg.is_ground_only:
            print(f"  [{i:02d}] SKIP (ground-only)  {label}")
            continue

        # Unique random offset per flight: spread up to ~3.5 years before ref date
        days_ago = random.randint(60, int(365 * 3.5))
        anon_date = _SEED_REF_DATE - timedelta(days=days_ago)

        coords = _downsample(seg.track_geojson["geometry"]["coordinates"], _MAX_COORDS)

        track = {
            "seed_date": anon_date.isoformat(),
            "dep": seg.departure_icao or "",
            "arr": seg.arrival_icao or "",
            "flight_time_h": seg.flight_time_rounded_h,
            "landing_count": seg.landing_count,
            "coordinates": coords,
        }
        tracks.append(track)
        print(
            f"  [{i:02d}] {label:12s}  {seg.flight_time_rounded_h:.1f} h"
            f"  {seg.landing_count} ldg  {len(coords)} pts  → {anon_date}"
        )

    with open(_OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(tracks, fh, separators=(",", ":"))

    size_kb = os.path.getsize(_OUT_PATH) / 1024
    print(f"\nWrote {len(tracks)} tracks → {_OUT_PATH}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
