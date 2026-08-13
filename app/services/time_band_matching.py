"""
Shared graduated time-proximity scoring for near-match candidate detection,
used by pilot-logbook import, airframe-logbook import, and GPS-track import
review flows — one shared implementation instead of three that could drift.

Two kinds of comparison show up across all three:
  - "same-pair": a freshly parsed row's time against an existing row's time
    of the *same* kind (pilot departure_time vs departure_time, airframe
    takeoff_time vs takeoff_time) — expected to match closely, since it's
    (if nothing changed) the same real-world instant reported twice.
  - "cross-pair"/span: comparing across the pilot-log-facing
    (departure_time/arrival_time, engine start/end) and airframe-log-facing
    (takeoff_time/landing_time, wheels-up/down) pairs, which are never the
    same instant. Used when reconciling an import against a placeholder
    created from the other side (only the aircraft's own
    flight_counter_offset is available as an estimate of the gap between
    them), and by GPS matching (which spans both edges when known, since a
    GPS track can start recording before engine start).
"""

from __future__ import annotations

import math
from datetime import time

# Same-pair matches are expected to be near-exact (the same real instant
# reported twice) — a few minutes of tolerance covers rounding/manual-entry
# corrections without accepting an unrelated flight.
SAME_PAIR_STEP_MINUTES = 5


def offset_ring_step_minutes(offset_hours: float) -> int:
    """Ring width for offset-derived graduated time bands: 1/3 of the
    aircraft's flight_counter_offset, rounded up to a whole minute (minimum
    1 minute, so a 0-offset aircraft still gets a usable band)."""
    return max(1, math.ceil(offset_hours * 60 / 3))


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def shift_time(t: time, delta_minutes: int) -> time:
    """*t* plus/minus *delta_minutes*, wrapping around midnight."""
    total = (_minutes(t) + delta_minutes) % 1440
    return time(total // 60, total % 60)


def time_band_score(
    new_time: time | None,
    band_edges: tuple[time, ...],
    step_minutes: int,
) -> float:
    """Graduated proximity score for *new_time* against the inclusive
    [min(band_edges), max(band_edges)] window.

    Pass one edge for a zero-width/point band (same-pair matching, or
    cross-pair matching centred on a reference time shifted by an offset),
    or two for a real span (GPS matching between departure_time and
    takeoff_time). *band_edges* may be empty when nothing on the existing
    side is available to compare against.

    Full credit (1.0) inside the window; degrades in two step_minutes-wide
    rings (0.75, then 0.5) outside it; 0 beyond that.

    Distance is computed in linear minutes-of-day, so a band that straddles
    midnight isn't handled specially — a pre-existing limitation carried
    over from the flat-tolerance check this replaces, and immaterial for
    the short local flights these imports cover.
    """
    if new_time is None or not band_edges:
        return 0.0
    new_m = _minutes(new_time)
    edge_ms = [_minutes(t) for t in band_edges]
    lo, hi = min(edge_ms), max(edge_ms)
    if lo <= new_m <= hi:
        return 1.0
    dist = lo - new_m if new_m < lo else new_m - hi
    if dist <= step_minutes:
        return 0.75
    if dist <= step_minutes * 2:
        return 0.5
    return 0.0


def widened_span_score(
    new_time: time | None,
    reference_edges: tuple[time, ...],
    widen_minutes: int,
) -> float:
    """time_band_score, but the core credit window is *reference_edges*
    widened outward by widen_minutes on each side first, before the two
    further widen_minutes-wide rings are applied.

    For GPS-track matching: reference_edges is whichever of
    (departure_time, takeoff_time) — or (landing_time, arrival_time) — the
    existing row has. A GPS track can start recording before engine start
    (setting up a flight plan on a tablet) or after, so the credit window
    needs to span both known edges rather than being anchored to one; if
    only one edge is known, the window is still widened around it rather
    than requiring an exact match, since a single GPS-detected instant is
    less precise than a logged clock time either way. reference_edges may
    be empty when the existing row has neither edge.
    """
    if not reference_edges:
        return 0.0
    widened = tuple(shift_time(t, -widen_minutes) for t in reference_edges) + tuple(
        shift_time(t, widen_minutes) for t in reference_edges
    )
    return time_band_score(new_time, widened, widen_minutes)
