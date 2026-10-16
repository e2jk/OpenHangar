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

# Fallback for offset_ring_step_minutes when no aircraft-specific
# flight_counter_offset is available (e.g. same-pair matching against a
# standalone entry with no managed aircraft) — matches Aircraft.flight_counter_offset's
# own column default in models.py.
DEFAULT_OFFSET_HOURS = 0.3


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


def taxi_out_score(
    block_off: time | None, takeoff: time | None, offset_hours: float
) -> float:
    """Cross-pair score for a block-off time (pilot-log departure_time)
    against a takeoff time (airframe-log takeoff_time) of the same flight.

    The aircraft's flight_counter_offset is the *total* block-to-airborne
    gap (taxi-out plus taxi-in), so block-off falls anywhere from the whole
    offset before takeoff up to takeoff itself: full credit inside
    [takeoff - offset, takeoff], degrading in time_band_score's rings
    outside it. (Shifting each edge by the whole offset instead — as this
    used to — counts that gap twice, so a real flight with an ordinary
    6-minute taxi each way ended up two rings out on both edges.)
    Symmetric: the same window, seen from the takeoff side, is
    [block_off, block_off + offset].
    """
    if block_off is None or takeoff is None:
        return 0.0
    offset_minutes = round(offset_hours * 60)
    return time_band_score(
        block_off,
        (shift_time(takeoff, -offset_minutes), takeoff),
        offset_ring_step_minutes(offset_hours),
    )


def taxi_in_score(
    landing: time | None, block_on: time | None, offset_hours: float
) -> float:
    """Cross-pair score for a landing time (airframe log) against a block-on
    time (pilot-log arrival_time): full credit inside
    [landing, landing + offset] — see taxi_out_score."""
    if landing is None or block_on is None:
        return 0.0
    offset_minutes = round(offset_hours * 60)
    return time_band_score(
        block_on,
        (landing, shift_time(landing, offset_minutes)),
        offset_ring_step_minutes(offset_hours),
    )
