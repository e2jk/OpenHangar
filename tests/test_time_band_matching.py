"""Tests for the shared graduated time-proximity scorer (app/services/time_band_matching.py)."""

from datetime import time

from services.time_band_matching import (  # pyright: ignore[reportMissingImports]
    offset_ring_step_minutes,
    shift_time,
    time_band_score,
    widened_span_score,
)


class TestOffsetRingStepMinutes:
    def test_default_offset(self):
        assert offset_ring_step_minutes(0.3) == 6

    def test_zero_offset_floors_at_one_minute(self):
        assert offset_ring_step_minutes(0.0) == 1

    def test_rounds_up(self):
        # 0.2h / 3 = 4 min exactly
        assert offset_ring_step_minutes(0.2) == 4
        # 0.25h / 3 = 5 min exactly
        assert offset_ring_step_minutes(0.25) == 5
        # 0.4h / 3 = 8 min exactly
        assert offset_ring_step_minutes(0.4) == 8


class TestShiftTime:
    def test_forward_within_day(self):
        assert shift_time(time(9, 0), 18) == time(9, 18)

    def test_backward_within_day(self):
        assert shift_time(time(9, 0), -18) == time(8, 42)

    def test_forward_wraps_past_midnight(self):
        assert shift_time(time(23, 50), 20) == time(0, 10)

    def test_backward_wraps_before_midnight(self):
        assert shift_time(time(0, 10), -20) == time(23, 50)


class TestTimeBandScore:
    def test_none_new_time_scores_zero(self):
        assert time_band_score(None, (time(9, 0),), 6) == 0.0

    def test_no_edges_scores_zero(self):
        assert time_band_score(time(9, 0), (), 6) == 0.0

    def test_point_band_exact_match(self):
        assert time_band_score(time(9, 0), (time(9, 0),), 6) == 1.0

    def test_point_band_within_first_ring(self):
        assert time_band_score(time(9, 6), (time(9, 0),), 6) == 0.75

    def test_point_band_within_second_ring(self):
        assert time_band_score(time(9, 12), (time(9, 0),), 6) == 0.5

    def test_point_band_beyond_second_ring(self):
        assert time_band_score(time(9, 13), (time(9, 0),), 6) == 0.0

    def test_point_band_boundary_is_inclusive(self):
        # Exactly at the ring boundaries, both sides.
        assert time_band_score(time(9, 6), (time(9, 0),), 6) == 0.75
        assert time_band_score(time(9, 12), (time(9, 0),), 6) == 0.5

    def test_point_band_symmetric_before(self):
        assert time_band_score(time(8, 54), (time(9, 0),), 6) == 0.75

    def test_span_band_full_credit_inside_window(self):
        # Anywhere between the two edges (inclusive) scores full credit,
        # not just at the edges themselves — this is what lets a GPS track
        # starting before engine-on still match.
        assert time_band_score(time(9, 6), (time(9, 0), time(9, 18)), 6) == 1.0
        assert time_band_score(time(9, 0), (time(9, 0), time(9, 18)), 6) == 1.0
        assert time_band_score(time(9, 18), (time(9, 0), time(9, 18)), 6) == 1.0

    def test_span_band_edge_order_does_not_matter(self):
        assert time_band_score(time(9, 6), (time(9, 18), time(9, 0)), 6) == 1.0

    def test_span_band_rings_taper_from_nearest_edge(self):
        assert time_band_score(time(9, 24), (time(9, 0), time(9, 18)), 6) == 0.75
        assert time_band_score(time(8, 54), (time(9, 0), time(9, 18)), 6) == 0.75
        assert time_band_score(time(9, 30), (time(9, 0), time(9, 18)), 6) == 0.5
        assert time_band_score(time(9, 31), (time(9, 0), time(9, 18)), 6) == 0.0


class TestWidenedSpanScore:
    def test_no_reference_edges_scores_zero(self):
        assert widened_span_score(time(9, 0), (), 6) == 0.0

    def test_two_edges_full_credit_spans_beyond_both(self):
        # departure_time=9:00, takeoff_time=9:18, widen=6 → full credit
        # anywhere from 8:54 to 9:24, not just between the two edges.
        assert widened_span_score(time(8, 54), (time(9, 0), time(9, 18)), 6) == 1.0
        assert widened_span_score(time(9, 0), (time(9, 0), time(9, 18)), 6) == 1.0
        assert widened_span_score(time(9, 9), (time(9, 0), time(9, 18)), 6) == 1.0
        assert widened_span_score(time(9, 18), (time(9, 0), time(9, 18)), 6) == 1.0
        assert widened_span_score(time(9, 24), (time(9, 0), time(9, 18)), 6) == 1.0

    def test_two_edges_rings_taper_beyond_widened_window(self):
        assert widened_span_score(time(9, 30), (time(9, 0), time(9, 18)), 6) == 0.75
        assert widened_span_score(time(8, 48), (time(9, 0), time(9, 18)), 6) == 0.75
        assert widened_span_score(time(9, 36), (time(9, 0), time(9, 18)), 6) == 0.5
        assert widened_span_score(time(9, 37), (time(9, 0), time(9, 18)), 6) == 0.0

    def test_single_edge_widened_symmetrically(self):
        # Only one of departure_time/takeoff_time known — still gets a
        # widened window, not an exact-match requirement.
        assert widened_span_score(time(8, 54), (time(9, 0),), 6) == 1.0
        assert widened_span_score(time(9, 6), (time(9, 0),), 6) == 1.0
        assert widened_span_score(time(9, 12), (time(9, 0),), 6) == 0.75
        assert widened_span_score(time(8, 42), (time(9, 0),), 6) == 0.5
        assert widened_span_score(time(8, 41), (time(9, 0),), 6) == 0.0
