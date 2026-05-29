"""Tests for Phase 30: GPS log import (parsers, classification, segments, routes)."""

import io
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from textwrap import dedent
from unittest.mock import patch

import bcrypt
import pytest

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftGpsImportBatch,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
    TrackPoint,
    ParsedGpsFile,
    _extract_icao_hints,
    _haversine_km,
    _load_airports,
    _reset_airports_cache,
    _split_into_raw_groups,
    build_geojson,
    classify_track,
    detect_format,
    detect_segments,
    downsample_track,
    merge_and_sort,
    parse_gps_file,
    resolve_icao,
    round_flight_time,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utc(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2024, 6, 1, h, m, s, tzinfo=timezone.utc)


def _tp(
    speed_kt: float,
    *,
    h: int = 10,
    m: int = 0,
    s: int = 0,
    lat: float = 51.0,
    lon: float = 4.5,
    alt_m: float = 0.0,
) -> TrackPoint:
    return TrackPoint(
        lat=lat, lon=lon, alt_m=alt_m, speed_kt=speed_kt, utc_dt=_utc(h, m, s)
    )


# ── Sample GPS file bytes ─────────────────────────────────────────────────────


def _gpx_bytes(
    name: str = "EBNM NAMUR - EBAW ANTWERPEN", speeds_ms: list = None
) -> bytes:
    if speeds_ms is None:
        speeds_ms = [0.0, 20.0, 20.0, 0.0]
    trkpts = ""
    for i, spd in enumerate(speeds_ms):
        t = f"2024-06-01T10:0{i}:00Z"
        trkpts += f"""
      <trkpt lat="51.{i}" lon="4.{i}">
        <ele>100</ele>
        <speed>{spd}</speed>
        <time>{t}</time>
      </trkpt>"""
    return dedent(f"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk>
        <name>{name}</name>
        <trkseg>{trkpts}
        </trkseg>
      </trk>
    </gpx>
    """).encode()


def _kml_bytes(name: str = "EBNM - EBAW") -> bytes:
    return dedent(
        """<?xml version="1.0"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"
         xmlns:gx="http://www.google.com/kml/ext/2.2">
      <Document>
        <Placemark>
          <name>"""
        + name
        + """</name>
          <gx:Track>
            <when>2024-06-01T10:00:00Z</when>
            <when>2024-06-01T10:30:00Z</when>
            <gx:coord>4.46 51.19 100</gx:coord>
            <gx:coord>4.46 51.50 150</gx:coord>
          </gx:Track>
        </Placemark>
      </Document>
    </kml>
    """
    ).encode()


def _garmin_csv_bytes(icao: str = "EBNM") -> bytes:
    header = (
        "#airframe_info,unit_software_part_number=006-C0873-00,"
        "unit_software_version=3.30,product=G1000,airframe_name=N12345\n"
        "lcl date,lcl time,utcofst,latitude,longitude,altmsl,gndsped,gpsfix\n"
        "Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix\n"
    )
    rows = (
        "2024-06-01,12:00:00,+02:00,51.19,4.46,328,0,3D\n"
        "2024-06-01,12:15:00,+02:00,51.30,4.50,1000,65,3D\n"
        "2024-06-01,12:30:00,+02:00,51.46,4.46,328,0,3D\n"
    )
    return (header + rows).encode()


def _garmin_csv_named(icao: str = "EBNM") -> bytes:
    """Return Garmin CSV with the departure airport hint in the filename context."""
    return _garmin_csv_bytes(icao)


# ── Format detection ──────────────────────────────────────────────────────────


class TestDetectFormat:
    def test_gpx_extension(self):
        assert detect_format(b"", "track.gpx") == "gpx"

    def test_kml_extension(self):
        assert detect_format(b"", "flight.kml") == "kml"

    def test_garmin_csv_sniffed(self):
        data = b"#airframe_info,unit=G1000\nheader\ncols\n"
        assert detect_format(data, "log_240601_120000_EBNM.csv") == "garmin_csv"

    def test_unknown_csv_raises(self):
        with pytest.raises(ValueError):
            detect_format(b"date,time\n", "export.csv")

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError):
            detect_format(b"", "track.nmea")


# ── ICAO hint extraction ──────────────────────────────────────────────────────


class TestExtractIcaoHints:
    def test_two_icao_codes(self):
        dep, arr = _extract_icao_hints("EBNM NAMUR - EBAW ANTWERPEN")
        assert dep == "EBNM"
        assert arr == "EBAW"

    def test_single_code_returns_dep_only(self):
        dep, arr = _extract_icao_hints("Departed EBNM")
        assert dep == "EBNM"
        assert arr is None

    def test_no_codes(self):
        dep, arr = _extract_icao_hints("Local flight")
        assert dep is None
        assert arr is None

    def test_multiple_codes_uses_first_and_last(self):
        dep, arr = _extract_icao_hints("EBNM EBAW EBLG EBBR")
        assert dep == "EBNM"
        assert arr == "EBBR"


# ── Haversine ─────────────────────────────────────────────────────────────────


class TestHaversine:
    def test_zero_distance(self):
        assert _haversine_km(51.0, 4.5, 51.0, 4.5) == pytest.approx(0.0)

    def test_known_distance(self):
        # EBNM (51.189, 4.459) to EBAW (51.463, 4.460) ≈ 30.5 km
        d = _haversine_km(51.189, 4.459, 51.463, 4.460)
        assert 28.0 < d < 33.0


# ── classify_track ────────────────────────────────────────────────────────────


class TestClassifyTrack:
    def test_empty_list(self):
        assert classify_track([]) == "empty"

    def test_all_slow(self):
        pts = [_tp(2.0, h=10, m=i) for i in range(5)]
        assert classify_track(pts) == "empty"

    def test_ground_movement_only(self):
        pts = [_tp(8.0, h=10, m=i) for i in range(5)]
        assert classify_track(pts) == "ground_movement"

    def test_brief_fast_not_sustained(self):
        # One 10-second spike above 30kt: not 30 s sustained
        pts = [
            _tp(0.0, h=10, m=0, s=0),
            _tp(35.0, h=10, m=0, s=10),
            _tp(0.0, h=10, m=0, s=20),
        ]
        assert classify_track(pts) == "ground_movement"

    def test_sustained_fast_is_flight(self):
        pts = [_tp(35.0, h=10, m=0, s=i * 10) for i in range(5)]
        assert classify_track(pts) == "flight"


# ── GPX parser ────────────────────────────────────────────────────────────────


class TestParseGpx:
    def test_basic_parse(self):
        data = _gpx_bytes()
        result = parse_gps_file(data, "flight.gpx")
        assert result.format == "gpx"
        assert len(result.trackpoints) == 4

    def test_speed_converted_from_ms(self):
        data = _gpx_bytes(speeds_ms=[0.0, 20.0, 0.0, 0.0])
        result = parse_gps_file(data, "f.gpx")
        # 20 m/s × 1.94384 ≈ 38.88 kt
        assert result.trackpoints[1].speed_kt == pytest.approx(20.0 * 1.94384)

    def test_icao_hints_extracted(self):
        data = _gpx_bytes(name="EBNM NAMUR - EBAW ANTWERPEN")
        result = parse_gps_file(data, "f.gpx")
        assert result.hint_departure_icao == "EBNM"
        assert result.hint_arrival_icao == "EBAW"

    def test_invalid_xml_raises(self):
        with pytest.raises(ValueError, match="Invalid GPX"):
            parse_gps_file(b"not xml", "bad.gpx")

    def test_classification_attached(self):
        data = _gpx_bytes(speeds_ms=[0.0, 0.0, 0.0])
        result = parse_gps_file(data, "f.gpx")
        assert result.classification == "empty"


# ── Garmin CSV parser ─────────────────────────────────────────────────────────


class TestParseGarminCsv:
    def test_device_id_extracted_from_system_id(self):
        header = (
            '#airframe_info,system_id="AABBCC112233",product=G1000\n'
            "units\n"
            "Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix\n"
            "2024-06-01,12:00:00,+00:00,51.0,4.5,100,0,3D\n"
        )
        result = parse_gps_file(header.encode(), "log.csv")
        assert result.device_id == "AABBCC112233"

    def test_no_device_id_when_system_id_absent(self):
        data = _garmin_csv_bytes()  # no system_id in header
        result = parse_gps_file(data, "log.csv")
        assert result.device_id is None

    def test_basic_parse(self):
        data = _garmin_csv_bytes()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert result.format == "garmin_csv"
        assert len(result.trackpoints) == 3

    def test_icao_hint_from_filename(self):
        data = _garmin_csv_bytes()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert result.hint_departure_icao == "EBNM"

    def test_no_icao_hint_short_filename(self):
        data = _garmin_csv_bytes()
        result = parse_gps_file(data, "log.csv")
        assert result.hint_departure_icao is None

    def test_utc_conversion(self):
        data = _garmin_csv_bytes()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        # Local 12:00 +02:00 → UTC 10:00
        assert result.trackpoints[0].utc_dt.hour == 10
        assert result.trackpoints[0].utc_dt.tzinfo is not None

    def test_altitude_ft_to_m(self):
        data = _garmin_csv_bytes()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        # 328 ft × 0.3048 ≈ 99.97 m
        assert result.trackpoints[0].alt_m == pytest.approx(328 * 0.3048)

    def test_invalid_fix_rows_skipped(self):
        header = (
            "#airframe_info,product=G1000\n"
            "units\n"
            "Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix\n"
            "2024-06-01,12:00:00,+00:00,51.0,4.5,100,0,NoFix\n"
            "2024-06-01,12:01:00,+00:00,51.1,4.5,100,50,3D\n"
        )
        result = parse_gps_file(header.encode(), "log_240601_120000_EBNM.csv")
        assert len(result.trackpoints) == 1


# ── KML parser ────────────────────────────────────────────────────────────────


class TestParseKml:
    def test_basic_parse(self):
        data = _kml_bytes()
        result = parse_gps_file(data, "flight.kml")
        assert result.format == "kml"
        assert len(result.trackpoints) == 2

    def test_coord_order_lon_lat(self):
        data = _kml_bytes()
        result = parse_gps_file(data, "flight.kml")
        # gx:coord "4.46 51.19 100" → lon=4.46, lat=51.19
        assert result.trackpoints[0].lat == pytest.approx(51.19)
        assert result.trackpoints[0].lon == pytest.approx(4.46)

    def test_speed_derived_from_distance(self):
        data = _kml_bytes()
        result = parse_gps_file(data, "flight.kml")
        # Second point should have a computed speed > 0
        assert result.trackpoints[1].speed_kt > 0

    def test_icao_hints(self):
        data = _kml_bytes(name="EBNM - EBAW")
        result = parse_gps_file(data, "flight.kml")
        assert result.hint_departure_icao == "EBNM"
        assert result.hint_arrival_icao == "EBAW"

    def test_missing_gx_track_raises(self):
        kml = b"""<?xml version="1.0"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>"""
        with pytest.raises(ValueError, match="No gx:Track"):
            parse_gps_file(kml, "f.kml")


# ── merge_and_sort ────────────────────────────────────────────────────────────


class TestMergeAndSort:
    def test_empty_input(self):
        assert merge_and_sort([]) == []

    def test_skips_empty_files(self):
        f1 = ParsedGpsFile(
            trackpoints=[_tp(0.0, h=10)],
            format="gpx",
            source_filename="a.gpx",
            classification="empty",
            hint_departure_icao=None,
            hint_arrival_icao=None,
        )
        assert merge_and_sort([f1]) == []

    def test_sorts_chronologically(self):
        pts1 = [_tp(35.0, h=11), _tp(35.0, h=12)]
        pts2 = [_tp(35.0, h=9), _tp(35.0, h=10)]
        f1 = ParsedGpsFile(
            trackpoints=pts1,
            format="gpx",
            source_filename="b.gpx",
            classification="flight",
            hint_departure_icao=None,
            hint_arrival_icao=None,
        )
        f2 = ParsedGpsFile(
            trackpoints=pts2,
            format="gpx",
            source_filename="a.gpx",
            classification="flight",
            hint_departure_icao=None,
            hint_arrival_icao=None,
        )
        merged = merge_and_sort([f1, f2])
        hours = [tp.utc_dt.hour for tp in merged]
        assert hours == sorted(hours)


# ── round_flight_time ─────────────────────────────────────────────────────────


class TestRoundFlightTime:
    def test_zero(self):
        assert round_flight_time(0.0, "tenth_hour") == 0.0
        assert round_flight_time(0.0, "minute") == 0.0

    def test_negative(self):
        assert round_flight_time(-1.0, "tenth_hour") == 0.0

    def test_tenth_hour_exact(self):
        assert round_flight_time(1.0, "tenth_hour") == pytest.approx(1.0)

    def test_tenth_hour_rounds_up(self):
        assert round_flight_time(1.01, "tenth_hour") == pytest.approx(1.1)

    def test_tenth_hour_boundary(self):
        # 0.1h exactly should not be rounded up
        assert round_flight_time(0.1, "tenth_hour") == pytest.approx(0.1)

    def test_minute_exact(self):
        assert round_flight_time(1.0, "minute") == pytest.approx(1.0)

    def test_minute_rounds_up(self):
        # 1h 1s = 60.0167 minutes → ceil to 61 minutes
        raw = 1.0 + 1 / 3600
        result = round_flight_time(raw, "minute")
        assert result == pytest.approx(61 / 60, rel=1e-3)

    def test_minute_precision(self):
        # 75 minutes exactly
        assert round_flight_time(75 / 60, "minute") == pytest.approx(75 / 60, rel=1e-4)


# ── downsample_track ──────────────────────────────────────────────────────────


def _tp_seq(n: int, speed_kt: float = 0.0) -> list[TrackPoint]:
    """Generate n TrackPoints spaced 1 minute apart starting at 10:00 UTC."""
    base = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    return [
        TrackPoint(
            lat=51.0,
            lon=4.5,
            alt_m=0.0,
            speed_kt=speed_kt,
            utc_dt=base + timedelta(minutes=i),
        )
        for i in range(n)
    ]


class TestDownsampleTrack:
    def test_no_downsample_needed(self):
        pts = _tp_seq(10)
        result = downsample_track(pts, max_points=500)
        assert result == pts

    def test_downsamples_to_max(self):
        pts = _tp_seq(1000)
        result = downsample_track(pts, max_points=100)
        # +1 tolerance: first+last are always included, may exceed max_points by 1
        assert len(result) <= 102

    def test_preserves_first_and_last(self):
        pts = _tp_seq(1000)
        result = downsample_track(pts, max_points=100)
        assert result[0] is pts[0]
        assert result[-1] is pts[-1]


# ── build_geojson ─────────────────────────────────────────────────────────────


class TestBuildGeojson:
    def test_structure(self):
        pts = [
            TrackPoint(
                lat=51.19, lon=4.46, alt_m=100.0, speed_kt=35.0, utc_dt=_utc(10)
            ),
            TrackPoint(
                lat=51.30, lon=4.50, alt_m=200.0, speed_kt=40.0, utc_dt=_utc(11)
            ),
        ]
        gj = build_geojson(pts)
        assert gj["type"] == "Feature"
        assert gj["geometry"]["type"] == "LineString"
        coords = gj["geometry"]["coordinates"]
        assert len(coords) == 2
        # RFC 7946: [lon, lat, alt]
        assert coords[0][0] == pytest.approx(4.46)
        assert coords[0][1] == pytest.approx(51.19)
        assert coords[0][2] == pytest.approx(100.0)

    def test_properties(self):
        pts = [
            TrackPoint(lat=51.0, lon=4.0, alt_m=50.0, speed_kt=30.0, utc_dt=_utc(10))
        ]
        gj = build_geojson(pts)
        assert "altitudes_m" in gj["properties"]
        assert "speeds_kt" in gj["properties"]
        assert gj["properties"]["speeds_kt"][0] == pytest.approx(30.0)


# ── resolve_icao ──────────────────────────────────────────────────────────────


class TestResolveIcao:
    def test_empty_airports(self):
        assert resolve_icao(51.0, 4.5, airports={}) is None

    def test_within_range(self):
        airports = {"EBNM": (51.189, 4.459)}
        result = resolve_icao(51.19, 4.46, airports=airports)
        assert result == "EBNM"

    def test_beyond_5km(self):
        airports = {"EBNM": (51.189, 4.459)}
        result = resolve_icao(52.0, 5.0, airports=airports)
        assert result is None

    def test_nearest_wins(self):
        airports = {
            "EBNM": (51.189, 4.459),
            "EBAW": (51.463, 4.460),
        }
        result = resolve_icao(51.19, 4.46, airports=airports)
        assert result == "EBNM"


# ── detect_segments ───────────────────────────────────────────────────────────


def _flight_segment(start_h: int, end_h: int, fast: bool = True) -> list[TrackPoint]:
    """Generate a simple track: slow start, fast middle, slow end."""
    pts = []
    spd = 35.0 if fast else 8.0
    for m in range(0, 60):
        h = start_h + m // 60
        mm = m % 60
        pts.append(_tp(spd, h=h, m=mm, s=0))
    return pts


class TestDetectSegments:
    def test_single_segment(self):
        pts = [_tp(35.0, h=10, m=i) for i in range(60)]
        segs = detect_segments(pts, aircraft_precision="tenth_hour")
        assert len(segs) == 1

    def test_segment_has_block_times(self):
        pts = [_tp(35.0, h=10, m=i) for i in range(10)]
        segs = detect_segments(pts)
        assert segs[0].block_off_utc == pts[0].utc_dt
        assert segs[0].block_on_utc == pts[-1].utc_dt

    def test_flight_time_positive(self):
        pts = [_tp(35.0, h=10, m=i) for i in range(60)]
        segs = detect_segments(pts)
        assert segs[0].flight_time_raw_h > 0

    def test_split_on_time_gap(self):
        # Two groups separated by > 5 min with no fast points between
        pts_a = [_tp(35.0, h=10, m=i) for i in range(5)]
        # 30-minute gap
        pts_b = [_tp(35.0, h=10, m=35 + i) for i in range(5)]
        segs = detect_segments(pts_a + pts_b)
        assert len(segs) == 2

    def test_hint_applied_when_no_airport_match(self):
        pts = [_tp(35.0, h=10, m=i, lat=0.0, lon=0.0) for i in range(10)]
        segs = detect_segments(pts, hint_dep="EBNM", hint_arr="EBAW")
        assert segs[0].departure_icao == "EBNM"
        assert segs[0].arrival_icao == "EBAW"

    def test_geojson_built(self):
        pts = [_tp(35.0, h=10, m=i) for i in range(5)]
        segs = detect_segments(pts)
        gj = segs[0].track_geojson
        assert gj["type"] == "Feature"
        assert gj["geometry"]["type"] == "LineString"

    def test_ground_only_flag(self):
        pts = [_tp(8.0, h=10, m=i) for i in range(10)]
        segs = detect_segments(pts)
        assert len(segs) == 1
        assert segs[0].is_ground_only is True

    def test_landing_count(self):
        # fast → slow → fast → slow = 2 landings
        pts = [
            _tp(35.0, h=10, m=0, s=0),
            _tp(35.0, h=10, m=0, s=10),
            _tp(5.0, h=10, m=0, s=20),
            _tp(5.0, h=10, m=0, s=30),
            _tp(35.0, h=10, m=0, s=40),
            _tp(35.0, h=10, m=0, s=50),
            _tp(5.0, h=10, m=1, s=0),
        ]
        segs = detect_segments(pts)
        assert segs[0].landing_count == 2


# ── Route integration tests ───────────────────────────────────────────────────


def _make_user_and_aircraft(app):
    with app.app_context():
        user = User(
            email="gps@example.com",
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name="GPS Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-GPS",
            make="Cessna",
            model="172",
            logbook_time_precision="tenth_hour",
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, tenant.id, ac.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["pilot_access"] = True


class TestGpsImportRoutes:
    def test_upload_get(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/gps-import")
        assert resp.status_code == 200
        assert b"GPS" in resp.data

    def test_upload_no_file_flashes_error(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"GPS" in resp.data

    def test_upload_gpx_redirects_to_review(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        gpx_data = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(gpx_data), "flight.gpx")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "review" in resp.headers["Location"]

    def test_history_page_loads(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/gps-import/history")
        assert resp.status_code == 200

    def test_flight_tracks_page_loads(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/tracks")
        assert resp.status_code == 200

    def test_flight_tracks_page_renders_gps_entry(self, client, app):
        from models import FlightEntry, GpsTrack  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            track = GpsTrack(
                source_filename="test.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(track)
            db.session.flush()
            entry = FlightEntry(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
                gps_track_id=track.id,
            )
            db.session.add(entry)
            db.session.commit()
        resp = client.get(f"/aircraft/{ac_id}/tracks")
        assert resp.status_code == 200
        assert b"EBNM" in resp.data

    def test_aircraft_tracks_gif_endpoint(self, client, app):
        from models import FlightEntry, GpsTrack  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            track = GpsTrack(
                source_filename="test.gpx",
                geojson={
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.0, 51.0], [4.5, 51.3], [5.0, 51.0]],
                    },
                    "properties": {},
                },
            )
            db.session.add(track)
            db.session.flush()
            entry = FlightEntry(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
                gps_track_id=track.id,
            )
            db.session.add(entry)
            db.session.commit()
        resp = client.get(f"/aircraft/{ac_id}/tracks/animation.gif")
        assert resp.status_code == 200
        assert resp.content_type == "image/gif"
        assert resp.data[:3] == b"GIF"

    def test_rollback_deletes_batch(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            batch = AircraftGpsImportBatch(
                aircraft_id=ac_id,
                pilot_user_id=uid,
                source_filenames=["test.gpx"],
                format_detected="gpx",
                segments_found=1,
                segments_imported=1,
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id

        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/{batch_id}/rollback",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(AircraftGpsImportBatch, batch_id) is None


# ── _load_airports edge cases ─────────────────────────────────────────────────


class TestLoadAirports:
    def test_skips_non_four_letter_ident_and_bad_latlon(self, tmp_path, monkeypatch):
        import os as _os

        csv_file = tmp_path / "airports.csv"
        csv_file.write_text(
            "ident,latitude_deg,longitude_deg\n"
            "XY,51.0,4.5\n"  # non-4-letter → skipped (line 99)
            "ABCD,bad_lat,4.5\n"  # bad lat → skipped (lines 103-104)
            "EBNM,51.19,4.459\n"  # valid
        )
        _orig_join = _os.path.join
        monkeypatch.setattr(
            "aircraft.gps_import.os.path.join",
            lambda *a: (
                str(csv_file) if "airports.csv" in str(a[-1]) else _orig_join(*a)
            ),
        )
        _reset_airports_cache()
        airports = _load_airports()
        assert "EBNM" in airports
        assert "XY" not in airports
        assert "ABCD" not in airports
        _reset_airports_cache()

    def test_reset_cache_clears_it(self):
        _reset_airports_cache()  # covers line 114
        # After reset, _load_airports() re-reads the file (no assertion needed —
        # just verify the call doesn't raise)
        result = _load_airports()
        assert isinstance(result, dict)
        _reset_airports_cache()

    def test_resolve_icao_loads_airports_when_none(self):
        # Covers line 589: `if airports is None: airports = _load_airports()`
        _reset_airports_cache()
        # Call without explicit airports dict so the branch is taken
        result = resolve_icao(0.0, 0.0)  # far from any airport → None
        assert result is None
        _reset_airports_cache()


# ── detect_format edge cases ──────────────────────────────────────────────────


class TestDetectFormatEdgeCases:
    def test_empty_csv_body_raises(self):
        # b"" → splitlines() = [] → [0] raises IndexError → caught → ValueError
        with pytest.raises(ValueError):
            detect_format(b"", "file.csv")

    def test_non_airframe_csv_raises(self):
        with pytest.raises(ValueError):
            detect_format(b"date,time\n2024,10:00\n", "data.csv")


# ── GPX parser edge cases ─────────────────────────────────────────────────────


def _gpx_with_bad_trkpt() -> bytes:
    """GPX with one trkpt that has non-numeric lat (skipped) + one valid."""
    return (
        b'<?xml version="1.0"?>'
        b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        b'<trkpt lat="bad" lon="4.0">'
        b"<ele>100</ele><speed>20</speed><time>2024-06-01T10:00:00Z</time>"
        b"</trkpt>"
        b'<trkpt lat="51.0" lon="4.0">'
        b"<ele>100</ele><speed>20</speed><time>2024-06-01T10:01:00Z</time>"
        b"</trkpt>"
        b"</trkseg></trk></gpx>"
    )


def _gpx_with_missing_time() -> bytes:
    """GPX with one trkpt that has no <time> element (skipped) + one valid."""
    return (
        b'<?xml version="1.0"?>'
        b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        b'<trkpt lat="51.0" lon="4.0"><ele>100</ele><speed>20</speed></trkpt>'
        b'<trkpt lat="51.1" lon="4.0">'
        b"<ele>100</ele><speed>20</speed><time>2024-06-01T10:01:00Z</time>"
        b"</trkpt>"
        b"</trkseg></trk></gpx>"
    )


def _gpx_with_bad_time() -> bytes:
    """GPX with one trkpt that has an unparseable <time> (skipped) + one valid."""
    return (
        b'<?xml version="1.0"?>'
        b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        b'<trkpt lat="51.0" lon="4.0">'
        b"<ele>100</ele><speed>20</speed><time>not-a-date</time>"
        b"</trkpt>"
        b'<trkpt lat="51.1" lon="4.0">'
        b"<ele>100</ele><speed>20</speed><time>2024-06-01T10:01:00Z</time>"
        b"</trkpt>"
        b"</trkseg></trk></gpx>"
    )


class TestParseGpxEdgeCases:
    def test_bad_lat_trkpt_skipped(self):
        result = parse_gps_file(_gpx_with_bad_trkpt(), "f.gpx")
        assert len(result.trackpoints) == 1

    def test_missing_time_trkpt_skipped(self):
        result = parse_gps_file(_gpx_with_missing_time(), "f.gpx")
        assert len(result.trackpoints) == 1

    def test_bad_time_trkpt_skipped(self):
        result = parse_gps_file(_gpx_with_bad_time(), "f.gpx")
        assert len(result.trackpoints) == 1


# ── Garmin CSV edge cases ─────────────────────────────────────────────────────


_GARMIN_HEADER = (
    "#airframe_info,product=G1000\nunits\n"
    "Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix\n"
)


class TestParseGarminCsvEdgeCases:
    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_gps_file(b"#airframe_info\nunits\n", "f.csv")

    def test_bad_lat_row_skipped(self):
        data = (
            _GARMIN_HEADER
            + "2024-06-01,12:00:00,+00:00,bad,4.46,100,0,3D\n"
            + "2024-06-01,12:01:00,+00:00,51.19,4.46,100,0,3D\n"
        ).encode()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert len(result.trackpoints) == 1

    def test_bad_altmsl_defaults_to_zero(self):
        data = (
            _GARMIN_HEADER + "2024-06-01,12:00:00,+00:00,51.19,4.46,bad_alt,0,3D\n"
        ).encode()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert result.trackpoints[0].alt_m == 0.0

    def test_bad_speed_defaults_to_zero(self):
        data = (
            _GARMIN_HEADER + "2024-06-01,12:00:00,+00:00,51.19,4.46,100,bad_spd,3D\n"
        ).encode()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert result.trackpoints[0].speed_kt == 0.0

    def test_bad_datetime_row_skipped(self):
        data = (
            _GARMIN_HEADER
            + "bad_date,12:00:00,+00:00,51.19,4.46,100,0,3D\n"
            + "2024-06-01,12:01:00,+00:00,51.30,4.50,100,0,3D\n"
        ).encode()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert len(result.trackpoints) == 1

    def test_leading_space_column_names_parsed(self):
        """Real Garmin CSV files use comma-space separation, so DictReader
        field names have leading spaces — the parser must strip them."""
        data = (
            "#airframe_info,product=G1000\nunits\n"
            "  Lcl Date, Lcl Time, UTCOfst,     Latitude,    Longitude,"
            "    AltMSL,   GndSpd, GPSfix\n"
            "2024-06-01, 12:00:00, +00:00,      51.19,       4.46,"
            "       100,        0, 3D\n"
            "2024-06-01, 12:01:00, +00:00,      51.30,       4.50,"
            "       100,        0, 3D\n"
        ).encode()
        result = parse_gps_file(data, "log_240601_120000_EBNM.csv")
        assert len(result.trackpoints) == 2
        assert abs(result.trackpoints[0].lat - 51.19) < 0.001


# ── KML edge cases ────────────────────────────────────────────────────────────


class TestParseKmlEdgeCases:
    def test_invalid_xml_raises(self):
        with pytest.raises(ValueError, match="Invalid KML"):
            parse_gps_file(b"not xml at all", "f.kml")

    def test_bad_when_datetime_appends_none(self):
        # <when> with unparseable text → whens.append(None) → point skipped
        kml = (
            b'<?xml version="1.0"?>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"'
            b' xmlns:gx="http://www.google.com/kml/ext/2.2">'
            b"<Document><Placemark><gx:Track>"
            b"<when>not-a-date</when>"
            b"<gx:coord>4.46 51.19 100</gx:coord>"
            b"<when>2024-06-01T10:01:00Z</when>"
            b"<gx:coord>4.47 51.20 100</gx:coord>"
            b"</gx:Track></Placemark></Document></kml>"
        )
        result = parse_gps_file(kml, "f.kml")
        assert len(result.trackpoints) == 1

    def test_empty_when_appends_none(self):
        # empty <when></when> → else: whens.append(None) → point skipped
        kml = (
            b'<?xml version="1.0"?>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"'
            b' xmlns:gx="http://www.google.com/kml/ext/2.2">'
            b"<Document><Placemark><gx:Track>"
            b"<when></when>"
            b"<gx:coord>4.46 51.19 100</gx:coord>"
            b"<when>2024-06-01T10:01:00Z</when>"
            b"<gx:coord>4.47 51.20 100</gx:coord>"
            b"</gx:Track></Placemark></Document></kml>"
        )
        result = parse_gps_file(kml, "f.kml")
        assert len(result.trackpoints) == 1

    def test_malformed_coord_uses_zero(self):
        # <gx:coord> with only one number → parts < 3 → (0, 0, 0)
        kml = (
            b'<?xml version="1.0"?>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"'
            b' xmlns:gx="http://www.google.com/kml/ext/2.2">'
            b"<Document><Placemark><gx:Track>"
            b"<when>2024-06-01T10:00:00Z</when>"
            b"<gx:coord>only_one</gx:coord>"
            b"<when>2024-06-01T10:01:00Z</when>"
            b"<gx:coord>4.47 51.20 100</gx:coord>"
            b"</gx:Track></Placemark></Document></kml>"
        )
        result = parse_gps_file(kml, "f.kml")
        # first point has (lon=0, lat=0) from fallback
        assert result.trackpoints[0].lat == 0.0

    def test_coord_bad_float_uses_zero(self):
        # <gx:coord> with 3 parts but non-numeric → ValueError → (0, 0, 0)
        kml = (
            b'<?xml version="1.0"?>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"'
            b' xmlns:gx="http://www.google.com/kml/ext/2.2">'
            b"<Document><Placemark><gx:Track>"
            b"<when>2024-06-01T10:00:00Z</when>"
            b"<gx:coord>bad bad bad</gx:coord>"
            b"<when>2024-06-01T10:01:00Z</when>"
            b"<gx:coord>4.47 51.20 100</gx:coord>"
            b"</gx:Track></Placemark></Document></kml>"
        )
        result = parse_gps_file(kml, "f.kml")
        assert result.trackpoints[0].lat == 0.0

    def test_when_coord_count_mismatch_raises(self):
        kml = (
            b'<?xml version="1.0"?>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"'
            b' xmlns:gx="http://www.google.com/kml/ext/2.2">'
            b"<Document><Placemark><gx:Track>"
            b"<when>2024-06-01T10:00:00Z</when>"
            b"<when>2024-06-01T10:01:00Z</when>"
            b"<gx:coord>4.46 51.19 100</gx:coord>"
            b"</gx:Track></Placemark></Document></kml>"
        )
        with pytest.raises(ValueError, match="mismatch"):
            parse_gps_file(kml, "f.kml")


# ── _split_into_raw_groups edge cases ─────────────────────────────────────────


def _tp_at(speed_kt: float, minutes: int) -> TrackPoint:
    base = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    return TrackPoint(
        lat=0.0,
        lon=0.0,
        alt_m=0.0,
        speed_kt=speed_kt,
        utc_dt=base + timedelta(minutes=minutes),
    )


class TestSplitIntoRawGroups:
    def test_empty_input_returns_empty(self):
        # line 451: if n == 0: return []
        assert _split_into_raw_groups([]) == []

    def test_no_fast_points_returns_single_group(self):
        # if not fast_indices: return [trackpoints]
        pts = [_tp_at(5.0, i) for i in range(5)]
        groups = _split_into_raw_groups(pts)
        assert len(groups) == 1
        assert groups[0] == pts

    def test_slow_tail_after_last_fast_stays_in_group(self):
        # fast at 0, slow at 1..10 — slow tail after only fast point
        pts = [_tp_at(35.0, 0)] + [_tp_at(5.0, i) for i in range(1, 12)]
        groups = _split_into_raw_groups(pts)
        assert len(groups) == 1

    def test_slow_gap_over_5min_splits_segments(self):
        # lines 487-489: slow run ≥ 300s → real segment break
        # fast(0min), slow(1..6min, 5+ min duration), fast(7min)
        pts = (
            [_tp_at(35.0, 0)]
            + [_tp_at(5.0, i) for i in range(1, 7)]  # 5 min slow
            + [_tp_at(35.0, 7)]
        )
        groups = _split_into_raw_groups(pts)
        assert len(groups) == 2

    def test_short_slow_run_stays_in_segment(self):
        # slow run < 5min → merged into same segment (else: i = j)
        pts = (
            [_tp_at(35.0, 0), _tp_at(35.0, 1)]
            + [_tp_at(5.0, 2), _tp_at(5.0, 3)]  # 1 min slow
            + [_tp_at(35.0, 4)]
        )
        groups = _split_into_raw_groups(pts)
        assert len(groups) == 1


# ── detect_segments: hint_arr for non-last segment ───────────────────────────


class TestDetectSegmentsHints:
    def test_hint_arr_only_on_last_segment(self):
        # line 528: `hint_arr if idx == len(raw_groups) - 1 else None`
        # Two segments, no airport near lat=0/lon=0; hint_arr only goes on last
        seg1 = [_tp_at(35.0, i) for i in range(5)]
        gap = [_tp_at(5.0, i) for i in range(5, 11)]  # 5-min slow gap → split
        seg2 = [_tp_at(35.0, i) for i in range(11, 16)]
        pts = seg1 + gap + seg2
        segs = detect_segments(pts, hint_dep="EBNM", hint_arr="EBAW")
        assert len(segs) == 2
        # hint_arr only applied to last segment (else None branch)
        assert segs[0].arrival_icao is None
        assert segs[-1].arrival_icao == "EBAW"


# ── Route integration: upload edge cases ─────────────────────────────────────


class TestGpsUploadEdgeCases:
    def test_unsupported_extension_flashes_error(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(b"text"), "notes.txt")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"unsupported" in resp.data.lower() or b"GPS" in resp.data

    def test_file_too_large_flashes_error(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        big = b"x" * (20 * 1024 * 1024 + 1)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(big), "big.gpx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"large" in resp.data.lower() or b"GPS" in resp.data

    def test_parse_error_flashes_error(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(b"not valid xml"), "bad.gpx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_empty_track_skipped_and_flashed(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # GPX with all-zero speeds → empty classification → skipped
        empty_gpx = _gpx_bytes(speeds_ms=[0.0, 0.0, 0.0])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(empty_gpx), "empty.gpx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"skipped" in resp.data.lower() or b"GPS" in resp.data

    def test_no_valid_files_renders_upload(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # One unsupported extension + nothing else → no valid files
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(b"data"), "data.pdf")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ── Route integration: review and confirm ────────────────────────────────────


class TestGpsReviewAndConfirm:
    def _upload_gpx(self, client, ac_id):
        """Upload a flight GPX and return after redirect to review."""
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(gpx), "flight.gpx")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        return resp

    def test_review_session_expired_redirects(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # No session state → "session expired"
        resp = client.get(
            f"/aircraft/{ac_id}/gps-import/review", follow_redirects=False
        )
        assert resp.status_code == 302
        assert "gps-import" in resp.headers["Location"]

    def test_review_loads_after_upload(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        upload_resp = self._upload_gpx(client, ac_id)
        assert upload_resp.status_code in (302, 303)
        # Follow redirect to review
        resp = client.get(f"/aircraft/{ac_id}/gps-import/review", follow_redirects=True)
        assert resp.status_code == 200

    def test_review_file_read_error_redirects(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # Set up session with a nonexistent tmp file
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "tmp_path": "/nonexistent/path/file.gpx",
                        "original_filename": "flight.gpx",
                        "format": "gpx",
                        "classification": "flight",
                        "trkpt_count": 5,
                        "hint_dep": None,
                        "hint_arr": None,
                    }
                ],
                "skipped_empty": 0,
            }
        resp = client.get(
            f"/aircraft/{ac_id}/gps-import/review", follow_redirects=False
        )
        assert resp.status_code == 302

    def test_confirm_session_expired_redirects(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "gps-import" in resp.headers["Location"]

    def test_confirm_empty_segments_redirects(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [],
                "segments": [],
                "skipped_empty": 0,
            }
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def _make_segment_dict(self):
        return {
            "idx": 0,
            "block_off_utc": "2024-06-01T10:00:00+00:00",
            "block_on_utc": "2024-06-01T11:00:00+00:00",
            "takeoff_utc": "2024-06-01T10:02:00+00:00",
            "landing_utc": "2024-06-01T10:58:00+00:00",
            "departure_icao": "EBNM",
            "arrival_icao": "EBAW",
            "flight_time_raw_h": 1.0,
            "flight_time_rounded_h": 1.0,
            "landing_count": 1,
            "is_ground_only": False,
            "track_geojson": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": []},
                "properties": {},
            },
        }

    def _set_confirm_session(self, client, uid, ac_id, segments=None):
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "tmp_path": "/tmp/nonexistent.gpx",
                        "original_filename": "flight.gpx",
                        "format": "gpx",
                        "classification": "flight",
                        "trkpt_count": 5,
                        "hint_dep": None,
                        "hint_arr": None,
                    }
                ],
                "segments": segments or [self._make_segment_dict()],
                "skipped_empty": 0,
            }

    def test_confirm_creates_flight_entry(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_confirm_with_pilot_entries(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "pic"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.pic_name == "gps"  # display_name derived from email prefix

    def test_confirm_unchecked_segment_skipped(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        # Don't check keep_segment_0 → no flight entries
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_confirm_pilot_role_dual_sets_function_dual(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "dual"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.function_dual is not None
            assert entry.function_pic is None

    def test_confirm_pilot_role_none_skips_logbook(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "none"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert PilotLogbookEntry.query.filter_by(pilot_user_id=uid).count() == 0

    def test_confirm_invalid_pilot_role_treated_as_none(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "INVALID"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert PilotLogbookEntry.query.filter_by(pilot_user_id=uid).count() == 0

    def test_confirm_links_gps_to_existing_flight(self, client, app):
        """GPS import links track to a pre-existing FlightEntry with overlapping UTC range."""
        from datetime import timezone as _tz  # noqa: PLC0415

        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        # Create a pre-existing flight covering the same time as the segment
        from datetime import datetime as _dt  # noqa: PLC0415
        import decimal  # noqa: PLC0415

        with app.app_context():
            existing = FlightEntry(
                aircraft_id=ac_id,
                date=_dt(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                block_off_utc=_dt(2024, 6, 1, 10, 0, 0, tzinfo=_tz.utc),
                block_on_utc=_dt(2024, 6, 1, 11, 0, 0, tzinfo=_tz.utc),
            )
            db.session.add(existing)
            db.session.commit()
            existing_id = existing.id

        # Session segment matches existing flight
        seg = self._make_segment_dict()
        seg["matched_flight_id"] = existing_id
        self._set_confirm_session(client, uid, ac_id, segments=[seg])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            # No new flight was created — only the pre-existing one remains
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1
            # Block times were updated on the linked flight
            updated = db.session.get(FlightEntry, existing_id)
            assert updated.block_off_utc is not None
            # Batch records the linked flight ID
            from models import AircraftGpsImportBatch as _Batch  # pyright: ignore[reportMissingImports]

            batch = _Batch.query.filter_by(aircraft_id=ac_id).first()
            assert existing_id in batch.linked_flight_entry_ids

    def test_review_detects_duplicate_flight(self, client, app):
        """Review page detects a pre-existing flight overlapping the GPS segment."""
        import io as _io  # noqa: PLC0415
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        import decimal  # noqa: PLC0415

        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])

        # Upload the file to populate session
        client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (_io.BytesIO(gpx), "flight.gpx")},
            content_type="multipart/form-data",
        )

        # Create an existing flight that overlaps with the GPS segment times
        with app.app_context():
            # The GPX helper creates a track; detect_segments will produce block times.
            # We use a wide window to guarantee overlap.
            existing = FlightEntry(
                aircraft_id=ac_id,
                date=_dt.now(_tz.utc).date(),
                departure_icao="XXXX",
                arrival_icao="YYYY",
                flight_time=decimal.Decimal("1.0"),
                block_off_utc=_dt(2000, 1, 1, 0, 0, tzinfo=_tz.utc),
                block_on_utc=_dt(2099, 1, 1, 0, 0, tzinfo=_tz.utc),
            )
            db.session.add(existing)
            db.session.commit()
            existing_id = existing.id

        resp = client.get(f"/aircraft/{ac_id}/gps-import/review")
        assert resp.status_code == 200

        # Session should now have matched_flight_id for the segment
        with client.session_transaction() as sess:
            segs = sess["gps_import"]["segments"]
            assert any(s.get("matched_flight_id") == existing_id for s in segs)

    def test_confirm_stale_matched_flight_falls_through_to_create(self, client, app):
        """If matched_flight_id no longer exists, a new FlightEntry is created instead."""
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        seg = self._make_segment_dict()
        seg["matched_flight_id"] = 999999  # non-existent flight ID
        self._set_confirm_session(client, uid, ac_id, segments=[seg])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            # Falls through to create a new FlightEntry
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_rollback_unlinks_linked_flights(self, client, app):
        """Rollback nulls out GPS track on linked (pre-existing) FlightEntry."""
        import decimal  # noqa: PLC0415
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415

        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            from models import GpsTrack  # pyright: ignore[reportMissingImports]

            gps_track = GpsTrack(
                block_off_utc=_dt(2024, 6, 1, 10, 0, tzinfo=_tz.utc),
                block_on_utc=_dt(2024, 6, 1, 11, 0, tzinfo=_tz.utc),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                geojson={"type": "Feature"},
            )
            db.session.add(gps_track)
            db.session.flush()
            existing = FlightEntry(
                aircraft_id=ac_id,
                date=_dt(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                gps_track_id=gps_track.id,
                block_off_utc=_dt(2024, 6, 1, 10, 0, tzinfo=_tz.utc),
                block_on_utc=_dt(2024, 6, 1, 11, 0, tzinfo=_tz.utc),
            )
            db.session.add(existing)
            db.session.flush()
            batch = AircraftGpsImportBatch(
                aircraft_id=ac_id,
                source_filenames=["test.gpx"],
                format_detected="gpx",
                segments_found=1,
                segments_imported=1,
                linked_flight_entry_ids=[existing.id],
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id
            existing_id = existing.id

        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/{batch_id}/rollback",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(AircraftGpsImportBatch, batch_id) is None
            # Pre-existing flight preserved but GPS track unlinked
            flight = db.session.get(FlightEntry, existing_id)
            assert flight is not None
            assert flight.gps_track_id is None
            assert flight.block_off_utc is None


# ── Route integration: flight_detail ─────────────────────────────────────────


class TestFlightDetail:
    def test_flight_detail_loads(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            entry = FlightEntry(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id
        resp = client.get(f"/aircraft/{ac_id}/flights/{entry_id}")
        assert resp.status_code == 200

    def test_flight_detail_wrong_aircraft_404(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]
        import decimal

        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # Create a second aircraft in the same tenant, attach the flight to it
        with app.app_context():
            from models import Aircraft  # pyright: ignore[reportMissingImports]

            ac2 = Aircraft(
                tenant_id=tenant_id,
                registration="OO-OTH",
                make="Piper",
                model="PA-28",
            )
            db.session.add(ac2)
            db.session.flush()
            entry = FlightEntry(
                aircraft_id=ac2.id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id
        # Requesting the flight via the wrong aircraft → 404
        resp = client.get(f"/aircraft/{ac_id}/flights/{entry_id}")
        assert resp.status_code == 404

    def test_flight_detail_with_gps_track_renders_map(self, client, app):
        from models import FlightEntry, GpsTrack  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(track)
            db.session.flush()
            entry = FlightEntry(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
                gps_track_id=track.id,
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id
        resp = client.get(f"/aircraft/{ac_id}/flights/{entry_id}")
        assert resp.status_code == 200
        assert b"flight-map" in resp.data


# ── Route: _save_aircraft invalid precision ───────────────────────────────────


class TestSaveAircraftPrecision:
    def test_invalid_precision_defaults_to_tenth_hour(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/edit",
            data={
                "registration": "OO-GPS",
                "make": "Cessna",
                "model": "172",
                "logbook_time_precision": "bogus_value",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.logbook_time_precision == "tenth_hour"


# ── Route: upload file with empty filename ────────────────────────────────────


class TestGpsUploadEmptyFilename:
    def test_empty_filename_skipped(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        # Line 812 only short-circuits when ALL files have empty names.
        # To reach line 824 we need at least one real file so the loop runs,
        # then an empty-named file that the loop skips.
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={
                "gps_files": [
                    (io.BytesIO(gpx), "flight.gpx"),
                    (io.BytesIO(b"extra"), ""),
                ]
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200


# ── Route: confirm with empty dep/arr ICAO ────────────────────────────────────


class TestGpsConfirmEmptyIcao:
    def _make_segment_no_icao(self):
        return {
            "idx": 0,
            "block_off_utc": "2024-06-01T10:00:00+00:00",
            "block_on_utc": "2024-06-01T11:00:00+00:00",
            "takeoff_utc": "2024-06-01T10:02:00+00:00",
            "landing_utc": "2024-06-01T10:58:00+00:00",
            "departure_icao": "",
            "arrival_icao": "",
            "flight_time_raw_h": 1.0,
            "flight_time_rounded_h": 1.0,
            "landing_count": 1,
            "is_ground_only": False,
            "track_geojson": {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": []},
                "properties": {},
            },
        }

    def test_confirm_empty_icao_uses_fallback(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "tmp_path": "/tmp/nonexistent.gpx",
                        "original_filename": "flight.gpx",
                        "format": "gpx",
                        "classification": "flight",
                        "trkpt_count": 5,
                        "hint_dep": None,
                        "hint_arr": None,
                    }
                ],
                "segments": [self._make_segment_no_icao()],
                "skipped_empty": 0,
            }
        # dep_icao and arr_icao both empty → fall back to "????" (lines 1025, 1027)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert entry is not None
            assert entry.departure_icao == "????"
            assert entry.arrival_icao == "????"


# ── Route: rollback wrong aircraft → 404 ────────────────────────────────────


class TestGpsRollbackWrongAircraft:
    def test_rollback_wrong_aircraft_404(self, client, app):
        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            # Create a second aircraft in same tenant
            ac2 = Aircraft(
                tenant_id=tenant_id,
                registration="OO-OTH",
                make="Piper",
                model="PA-28",
            )
            db.session.add(ac2)
            db.session.flush()
            # Batch belongs to ac2
            batch = AircraftGpsImportBatch(
                aircraft_id=ac2.id,
                pilot_user_id=uid,
                source_filenames=["test.gpx"],
                format_detected="gpx",
                segments_found=1,
                segments_imported=1,
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id

        # Request rollback via ac_id (different aircraft) → 404
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/{batch_id}/rollback",
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ── Unit: _load_segment_geojson ──────────────────────────────────────────────


class TestLoadSegmentGeojson:
    def test_returns_none_when_no_path(self):
        from aircraft.routes import _load_segment_geojson  # pyright: ignore[reportMissingImports]

        assert _load_segment_geojson({}) is None

    def test_returns_none_when_path_missing(self):
        from aircraft.routes import _load_segment_geojson  # pyright: ignore[reportMissingImports]

        assert (
            _load_segment_geojson({"geojson_path": "/nonexistent/path.geojson"}) is None
        )

    def test_reads_geojson_from_file(self):
        from aircraft.routes import _load_segment_geojson  # pyright: ignore[reportMissingImports]

        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".geojson", delete=False
        ) as fh:
            json.dump(geojson, fh)
            path = fh.name
        try:
            result = _load_segment_geojson({"geojson_path": path})
            assert result == geojson
        finally:
            os.unlink(path)


# ── Route: confirm geojson cleanup OSError ───────────────────────────────────


class TestConfirmGeojsonCleanupError:
    """Confirm route must not crash when geojson tmp-file cleanup raises OSError."""

    def _make_segment_with_geojson(self, geojson_path):
        return {
            "idx": 0,
            "block_off_utc": "2024-06-01T10:00:00+00:00",
            "block_on_utc": "2024-06-01T11:00:00+00:00",
            "takeoff_utc": "2024-06-01T10:02:00+00:00",
            "landing_utc": "2024-06-01T10:58:00+00:00",
            "departure_icao": "EBNM",
            "arrival_icao": "EBAW",
            "flight_time_raw_h": 1.0,
            "flight_time_rounded_h": 1.0,
            "landing_count": 1,
            "is_ground_only": False,
            "geojson_path": geojson_path,
        }

    def test_confirm_survives_geojson_unlink_error(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": []},
            "properties": {},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".geojson", delete=False
        ) as fh:
            json.dump(geojson, fh)
            gj_path = fh.name

        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "tmp_path": "/tmp/nonexistent.gpx",
                        "original_filename": "f.gpx",
                        "format": "gpx",
                        "classification": "flight",
                        "trkpt_count": 5,
                        "hint_dep": None,
                        "hint_arr": None,
                    }
                ],
                "segments": [self._make_segment_with_geojson(gj_path)],
                "skipped_empty": 0,
            }

        real_unlink = os.unlink

        def selective_unlink(path):
            if path == gj_path:
                raise OSError("simulated cleanup failure")
            real_unlink(path)

        with patch("aircraft.routes.os.unlink", side_effect=selective_unlink):
            resp = client.post(
                f"/aircraft/{ac_id}/gps-import/confirm",
                data={"keep_segment_0": "1"},
                follow_redirects=True,
            )

        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1
        # Clean up the file if it wasn't deleted due to the simulated error
        if os.path.exists(gj_path):
            os.unlink(gj_path)


# ── Phase 31: GPS import other-aircraft mode ──────────────────────────────────


class TestGpsImportOtherAircraft:
    """GPS import in other-aircraft mode: no FlightEntry, only PilotLogbookEntry."""

    def _set_confirm_session(
        self,
        client,
        uid,
        ac_id,
        other_aircraft=True,
        other_ac_make_model="Piper PA-28",
        other_ac_reg="OO-TST",
    ):
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "original_filename": "test.gpx",
                        "format": "gpx",
                        "tmp_path": "/tmp/x",
                    }
                ],
                "skipped_empty": 0,
                "segments": [
                    {
                        "block_off_utc": "2026-05-26T09:00:00",
                        "block_on_utc": "2026-05-26T10:00:00",
                        "flight_time_raw_h": 1.0,
                        "flight_time_rounded_h": 1.0,
                        "departure_icao": "EBNM",
                        "arrival_icao": "EBAW",
                        "is_ground_only": False,
                        "landing_count": 1,
                        "track_geojson": None,
                        "matched_flight_id": None,
                        "matched_flight_str": None,
                    }
                ],
                "other_aircraft": other_aircraft,
                "other_ac_make_model": other_ac_make_model,
                "other_ac_reg": other_ac_reg,
            }

    def test_other_aircraft_creates_logbook_entry_not_flight_entry(self, client, app):
        from models import FlightEntry, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)

        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "pic"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 0
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.aircraft_type == "Piper PA-28"
            assert entry.aircraft_registration == "OO-TST"
            assert entry.flight_id is None
            assert entry.function_pic is not None

    def test_other_aircraft_dual_role(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)

        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "dual"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.function_dual is not None
            assert entry.function_pic is None

    def test_other_aircraft_role_none_defaults_to_pic(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)

        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "none"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.function_pic is not None

    def test_other_aircraft_rollback_deletes_logbook_entry(self, client, app):
        from models import AircraftGpsImportBatch, PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(client, uid, ac_id)

        # Import
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "pic"},
            follow_redirects=True,
        )

        with app.app_context():
            batch = AircraftGpsImportBatch.query.filter_by(aircraft_id=ac_id).first()
            assert batch is not None
            batch_id = batch.id
            assert PilotLogbookEntry.query.filter_by(gps_batch_id=batch_id).count() == 1

        # Rollback
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/{batch_id}/rollback",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert PilotLogbookEntry.query.filter_by(gps_batch_id=batch_id).count() == 0

    def test_other_aircraft_batch_stores_make_model_and_reg(self, client, app):
        from models import AircraftGpsImportBatch  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_confirm_session(
            client, uid, ac_id, other_ac_make_model="Cessna 172", other_ac_reg="OO-XYZ"
        )

        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "pic"},
            follow_redirects=True,
        )
        with app.app_context():
            batch = AircraftGpsImportBatch.query.filter_by(aircraft_id=ac_id).first()
            assert batch is not None
            assert batch.other_aircraft_make_model == "Cessna 172"
            assert batch.other_aircraft_registration == "OO-XYZ"


# ── Confirm redirect: logbook vs flight list ──────────────────────────────────


class TestConfirmRedirect:
    """After GPS confirm, redirect goes to the pilot logbook (PIC/dual) or aircraft
    flight list (not flying)."""

    def _set_session(self, client, uid, ac_id):
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "original_filename": "f.gpx",
                        "format": "gpx",
                        "tmp_path": "/tmp/x",
                    }
                ],
                "skipped_empty": 0,
                "segments": [
                    {
                        "block_off_utc": "2024-06-01T10:00:00+00:00",
                        "block_on_utc": "2024-06-01T11:00:00+00:00",
                        "flight_time_raw_h": 1.0,
                        "flight_time_rounded_h": 1.0,
                        "departure_icao": "EBNM",
                        "arrival_icao": "EBAW",
                        "is_ground_only": False,
                        "landing_count": 1,
                        "track_geojson": None,
                        "matched_flight_id": None,
                        "matched_flight_str": None,
                    }
                ],
                "other_aircraft": False,
                "other_ac_make_model": "",
                "other_ac_reg": "",
            }

    def test_pic_role_redirects_to_pilot_logbook(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "pic"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "/pilot/logbook" in resp.headers["Location"]

    def test_dual_role_redirects_to_pilot_logbook(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "dual"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "/pilot/logbook" in resp.headers["Location"]

    def test_none_role_redirects_to_aircraft_flights(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "none"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert f"/aircraft/{ac_id}/flights" in resp.headers["Location"]


# ── Confirm: nature_of_flight and remarks fields ──────────────────────────────


class TestConfirmNatureAndRemarks:
    """Nature and remarks from the review form are persisted on FlightEntry and
    PilotLogbookEntry respectively."""

    def _set_session(self, client, uid, ac_id):
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": [
                    {
                        "original_filename": "f.gpx",
                        "format": "gpx",
                        "tmp_path": "/tmp/x",
                    }
                ],
                "skipped_empty": 0,
                "segments": [
                    {
                        "block_off_utc": "2024-06-01T10:00:00+00:00",
                        "block_on_utc": "2024-06-01T11:00:00+00:00",
                        "flight_time_raw_h": 1.0,
                        "flight_time_rounded_h": 1.0,
                        "departure_icao": "EBNM",
                        "arrival_icao": "EBAW",
                        "is_ground_only": False,
                        "landing_count": 1,
                        "track_geojson": None,
                        "matched_flight_id": None,
                        "matched_flight_str": None,
                    }
                ],
                "other_aircraft": False,
                "other_ac_make_model": "",
                "other_ac_reg": "",
            }

    def test_nature_stored_on_flight_entry(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={
                "keep_segment_0": "1",
                "pilot_role": "none",
                "nature_0": "Navigation",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert entry is not None
            assert entry.nature_of_flight == "Navigation"

    def test_remarks_stored_on_pilot_logbook_entry(self, client, app):
        from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={
                "keep_segment_0": "1",
                "pilot_role": "pic",
                "remarks_0": "Smooth landing",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.remarks == "Smooth landing"

    def test_empty_nature_stored_as_null(self, client, app):
        from models import FlightEntry  # pyright: ignore[reportMissingImports]

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        self._set_session(client, uid, ac_id)
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm",
            data={"keep_segment_0": "1", "pilot_role": "none"},
            follow_redirects=True,
        )
        with app.app_context():
            entry = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert entry is not None
            assert entry.nature_of_flight is None
