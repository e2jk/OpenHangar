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


# ── SkyDemon exports and format detection by content ───────────────────────


def _skydemon_gpx(points):
    """A SkyDemon for Android GPX export: BOM, GPX 1.1, speed in m/s, a
    track name with the route — saved as "….gpx.bin"."""
    pts = "".join(
        f'<trkpt lat="{lat}" lon="{lon}"><ele>{alt}</ele><speed>{speed}</speed>'
        f"<time>{when.strftime('%Y-%m-%dT%H:%M:%SZ')}</time></trkpt>"
        for lat, lon, alt, speed, when in points
    )
    return (
        '﻿<?xml version="1.0" encoding="utf-8"?>\n'
        '<gpx version="1.1" creator="SkyDemon for Android" '
        'xmlns="http://www.topografix.com/GPX/1/1"><trk>'
        "<name>EBNM NAMUR  Suarlée - EBNM NAMUR  Suarlée</name>"
        f"<trkseg>{pts}</trkseg></trk></gpx>"
    ).encode()


def _skydemon_kml(points):
    """The same track as SkyDemon's KML (gx:Track, no speed, no route name)."""
    whens = "".join(
        f"<when>{w.strftime('%Y-%m-%dT%H:%M:%SZ')}</when>" for *_x, w in points
    )
    coords = "".join(
        f"<gx:coord>{lon} {lat} {alt}</gx:coord>" for lat, lon, alt, _s, _w in points
    )
    return (
        '﻿<?xml version="1.0" encoding="utf-8"?>'
        '<kml xmlns:gx="http://www.google.com/kml/ext/2.2" creator="SkyDemon for Android" '
        'xmlns="http://www.opengis.net/kml/2.2"><Document><name>SkyDemon Track Log</name>'
        f"<Placemark><gx:Track>{whens}{coords}</gx:Track></Placemark></Document></kml>"
    ).encode()


def _takeoff_roll_flight():
    """Logging starts mid takeoff roll (already 26 kt) at Namur (EBNM),
    flies 10 min at ~108 kt and 300 m up, lands back and stops 4 s after."""
    lat0, lon0, field = 50.4867, 4.7686, 180.0
    pts = []
    t = _T0
    for i in range(20):  # takeoff roll and climb: accelerating
        pts.append((lat0 + 0.00005 * i * i / 2, lon0, field + i * 15, 13.0 + i * 2, t))
        t += timedelta(seconds=1)
    lat = pts[-1][0]
    for _i in range(600):  # flying
        lat += 0.0005 * (1 if _i < 300 else -1)
        pts.append((lat, lon0, field + 300, 55.6, t))
        t += timedelta(seconds=1)
    start = lat
    for i in range(1, 61):  # descent and landing roll, back to the field
        frac = min(1.0, i / 40)
        pts.append(
            (
                start + (lat0 - start) * frac,
                lon0,
                field + max(0.0, 300 - i * 10),
                max(3.6, 55.6 - i * 1.2),
                t,
            )
        )
        t += timedelta(seconds=1)
    return pts


class TestSkyDemonExports:
    def test_detect_by_content(self):
        from aircraft.gps_import import detect_format, is_gps_upload_name

        gpx = _skydemon_gpx(_takeoff_roll_flight())
        kml = _skydemon_kml(_takeoff_roll_flight())
        assert detect_format(gpx, "EBNM - EBNM 202603201607.gpx.bin") == "gpx"
        assert detect_format(kml, "EBNM - EBNM 202603201607.kml.bin") == "kml"
        assert detect_format(b"<!-- x --><ns0:gpx xmlns:ns0='u'/>", "t.bin") == "gpx"
        assert is_gps_upload_name("a.gpx.bin") and is_gps_upload_name("a.KML")
        assert not is_gps_upload_name("a.txt")
        import pytest

        with pytest.raises(ValueError, match="SkyDemon's own log format"):
            detect_format(b"\x00\x01binary", "x 202603201607.flightlog.bin")
        with pytest.raises(ValueError, match="Unsupported GPS file format"):
            detect_format(b"<html></html>", "page.bin")
        with pytest.raises(ValueError, match="Unsupported GPS file format"):
            detect_format(b"time,lat,lon\n", "other.csv")

    def test_gpx_and_kml_read_the_same(self):
        pts = _takeoff_roll_flight()
        for doc, name in (
            (_skydemon_gpx(pts), "flight.gpx.bin"),
            (_skydemon_kml(pts), "flight.kml"),
        ):
            parsed = parse_gps_file(doc, name)
            (seg,) = detect_segments(merge_and_sort([parsed]))
            # Started mid takeoff roll — on the ground, not in flight: the
            # field is named from the position (no route name in the KML).
            assert (seg.departure_icao, seg.arrival_icao) == ("EBNM", "EBNM"), name
            assert seg.landing_count == 1 and not seg.is_ground_only

    def test_upload_gpx_bin_on_both_import_pages(self, client, app):
        import io

        from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
            _login,
            _make_user_and_aircraft,
        )

        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        doc = _skydemon_gpx(_takeoff_roll_flight())
        name = "EBNM NAMUR - EBNM NAMUR 202603201607.gpx.bin"
        rv = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(doc), name)},
            content_type="multipart/form-data",
        )
        assert rv.headers["Location"].endswith("/gps-import/review")
        rv = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(doc), name)},
            content_type="multipart/form-data",
        )
        assert rv.headers["Location"].endswith("/pilot/gps-import/review")
        # Still refused by name: neither a GPS extension nor .bin.
        page = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(doc), "notes.txt")},
            content_type="multipart/form-data",
        ).data.decode()
        assert "unsupported file type" in page


class TestXmlRootName:
    def test_shapes(self):
        from aircraft.gps_import import _xml_root_name

        assert _xml_root_name('<?xml version="1.0"?>\n<gpx version="1.1">') == "gpx"
        assert (
            _xml_root_name("<!-- a --><!DOCTYPE kml><ns0:kml xmlns:ns0='u'/>")
            == "ns0:kml"
        )
        assert _xml_root_name("  \n <kml>") == "kml"
        assert _xml_root_name("time,lat,lon") == ""
        assert _xml_root_name("<?xml never closed") == ""
        assert _xml_root_name("<!-- never closed") == ""
        assert _xml_root_name("<!DOCTYPE never closed") == ""
        assert _xml_root_name("") == ""

    def test_crafted_input_is_linear(self):
        """The inputs CodeQL flagged for the regular expression this
        replaced (py/redos) — handled at once, not in exponential time."""
        import time

        from aircraft.gps_import import _xml_root_name

        for crafted in (
            "<!--" + "--><!--" * 20000,
            "<?" + "?><?" * 20000,
            "<??>" * 20000,
        ):
            started = time.perf_counter()
            assert _xml_root_name(crafted) == ""
            assert time.perf_counter() - started < 1.0
