"""GPX files as cameras and phones write them (e.g. a GoPro's GPS exported
with ExifTool): GPX 1.0 or no namespace at all, no speed element, a missing
closing tag, bad fixes while the GPS acquires — and their noise.
"""

from datetime import UTC, datetime, timedelta

from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
    _drop_bad_fixes,
    _speeds_from_positions,
    detect_segments,
    merge_and_sort,
    parse_gps_file,
    resolve_icao,
)

_T0 = datetime(2025, 3, 14, 8, 0, tzinfo=UTC)


def _points(n, lat0=50.46, dlat_per_s=0.0, start=_T0, step_s=1.0):
    return [
        (
            lat0 + dlat_per_s * i * step_s,
            4.46,
            100.0,
            start + timedelta(seconds=i * step_s),
        )
        for i in range(n)
    ]


def _gpx(points, ns="http://www.topografix.com/GPX/1/0", close=True):
    xmlns = f' xmlns="{ns}"' if ns else ""
    pts = "".join(
        f'<trkpt lat="{lat}" lon="{lon}"><ele>{alt}</ele>'
        f"<time>{when.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z</time></trkpt>"
        for lat, lon, alt, when in points
    )
    doc = f'<?xml version="1.0"?><gpx version="1.0"{xmlns}><trk><trkseg>{pts}</trkseg></trk>'
    return (doc + ("</gpx>" if close else "")).encode()


# A flight: 2 min parked, 10 min at ~108 kt (0.0005° lat/s), 2 min parked.
def _flight_points():
    parked = _points(120)
    flying = _points(
        600, lat0=50.46, dlat_per_s=0.0005, start=_T0 + timedelta(seconds=120)
    )
    end_lat = flying[-1][0]
    after = _points(120, lat0=end_lat, start=_T0 + timedelta(seconds=720))
    return parked + flying + after


class TestGpxVariants:
    def test_gpx10_no_namespace_and_missing_closing_tag(self):
        for doc in (
            _gpx(_flight_points()),
            _gpx(_flight_points(), ns=None),
            _gpx(_flight_points(), ns="http://www.topografix.com/GPX/1/1", close=False),
        ):
            parsed = parse_gps_file(doc, "flight.gpx")
            assert parsed.classification == "flight"
            (seg,) = detect_segments(merge_and_sort([parsed]))
            assert not seg.is_ground_only and seg.landing_count == 1

    def test_unreadable_from_the_start_still_fails(self):
        import pytest

        with pytest.raises(ValueError, match="Invalid GPX XML"):
            parse_gps_file(b"<?xml version='1.0'?><gpx", "broken.gpx")

    def test_speed_derived_over_a_few_seconds(self):
        pts = [
            (lat, lon, when) for lat, lon, _alt, when in _points(20, dlat_per_s=0.0005)
        ]
        speeds = _speeds_from_positions(pts)
        assert speeds[0] == 0.0
        assert 105 < speeds[-1] < 111  # 0.0005° lat = 55.6 m per s ≈ 108 kt
        # Two timestamps a fraction of a second apart: no division blow-up.
        same = [(50.0, 4.0, _T0), (50.0, 4.0, _T0)]
        assert _speeds_from_positions(same) == [0.0, 0.0]


class TestBadFixes:
    def test_no_fix_and_stale_start_dropped(self):
        stale = [
            (50.822, 4.489, 83.0, None, _T0 + timedelta(seconds=i)) for i in range(5)
        ]
        no_fix = [
            (0.0, 0.0, -17.0, None, datetime(2025, 3, 7, 0, 0, i, tzinfo=UTC))
            for i in range(5)
        ]
        real = [
            (lat, lon, alt, None, when)
            for lat, lon, alt, when in _points(120, start=_T0 + timedelta(seconds=5))
        ]
        kept = _drop_bad_fixes(no_fix + stale + real)
        assert kept == real

    def test_jitter_between_close_timestamps_is_not_a_jump(self):
        a = (50.46, 4.46, 100.0, None, _T0)
        b = (50.46005, 4.46, 100.0, None, _T0 + timedelta(milliseconds=50))  # 5 m
        c = (50.4601, 4.46, 100.0, None, _T0 + timedelta(seconds=90))
        assert _drop_bad_fixes([a, b, c]) == [a, b, c]
        # A far point at the same instant is a jump too — dropped (with
        # the lone point it cut off), the real track kept.
        spike = (51.0, 4.46, 100.0, None, _T0)
        real = [
            (50.46, 4.46, 100.0, None, _T0 + timedelta(seconds=i))
            for i in range(1, 120)
        ]
        assert _drop_bad_fixes([a, spike, *real]) == real

    def test_a_short_file_without_jumps_is_kept_whole(self):
        one = [(50.46, 4.46, 100.0, None, _T0)]
        assert _drop_bad_fixes(one) == one


def test_heliports_are_not_landing_fields():
    # On Ostend's runway, nearer to the Oostende Heliport's reference point.
    assert resolve_icao(51.196, 2.86) == "EBOS"
