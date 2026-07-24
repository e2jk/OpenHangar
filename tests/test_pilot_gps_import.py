"""Tests for pilot GPS import (airplane-agnostic batch upload from pilot logbook)."""

import contextlib
import io
import os
import tempfile
from datetime import datetime, timezone
from textwrap import dedent

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    GpsTrack,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utc(h: int, m: int = 0) -> datetime:
    return datetime(2024, 6, 1, h, m, tzinfo=timezone.utc)


def _gpx_bytes(speeds_ms=None) -> bytes:
    if speeds_ms is None:
        speeds_ms = [0.0, 20.0, 20.0, 0.0]
    trkpts = ""
    for i, spd in enumerate(speeds_ms):
        t = f"2024-06-01T10:0{i}:00Z"
        trkpts += (
            f'\n      <trkpt lat="51.{i}" lon="4.{i}">'
            f"\n        <ele>100</ele>"
            f"\n        <speed>{spd}</speed>"
            f"\n        <time>{t}</time>"
            f"\n      </trkpt>"
        )
    return dedent(f"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>EBNM - EBAW</name><trkseg>{trkpts}
      </trkseg></trk>
    </gpx>
    """).encode()


def _make_user_and_aircraft(app):
    with app.app_context():
        user = User(
            email="pgps@example.com",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name="Pilot GPS Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-PIL",
            make="Piper",
            model="PA-28",
            logbook_time_precision="tenth_hour",
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, tenant.id, ac.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["pilot_access"] = True


def _seg_dict(idx=0, matched_flight_id=None):
    """Build a segment dict suitable for session['pilot_gps_import']['segments']."""
    return {
        "idx": idx,
        "block_off_utc": "2024-06-01T10:00:00+00:00",
        "block_on_utc": "2024-06-01T11:00:00+00:00",
        "takeoff_utc": "2024-06-01T10:02:00+00:00",
        "landing_utc": "2024-06-01T10:58:00+00:00",
        "departure_icao": "EBNM",
        "arrival_icao": "EBAW",
        "flight_time_raw_h": 1.0,
        "flight_time_rounded_h": 1.0,
        "flight_time_h": 1.0,
        "landing_count": 1,
        "is_ground_only": False,
        "track_geojson": None,
        "geojson_path": None,
        "matched_flight_id": matched_flight_id,
        "matched_flight_str": None,
        "matched_has_existing_track": False,
        "matched_aircraft_id": None,
        "matched_aircraft_reg": None,
        "matched_ambiguous": False,
        "matched_candidates": [],
    }


def _set_upload_session(client, uid, segments=None, files=None):
    with client.session_transaction() as sess:
        sess["pilot_gps_import"] = {
            "user_id": uid,
            "files": files
            or [
                {
                    "tmp_path": "/tmp/nonexistent.gpx",
                    "original_filename": "flight.gpx",
                    "format": "gpx",
                    "classification": "flight",
                    "trkpt_count": 5,
                    "hint_dep": None,
                    "hint_arr": None,
                    "device_id": None,
                }
            ],
            "segments": segments or [_seg_dict()],
            "skipped_empty": 0,
        }


# ── Upload route ─────────────────────────────────────────────────────────────


class TestPilotGpsUpload:
    def test_get_renders_page(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.get("/pilot/gps-import")
        assert resp.status_code == 200
        assert b"GPS" in resp.data

    def test_unauthenticated_redirects(self, client, app):
        resp = client.get("/pilot/gps-import")
        assert resp.status_code == 302

    def test_post_no_file_flashes_warning(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            "/pilot/gps-import",
            data={},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"GPS" in resp.data

    def test_post_unsupported_ext_flashes_error(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(b"data"), "track.nmea")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"unsupported" in resp.data.lower()

    def test_post_empty_gpx_flashes_skipped(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        empty_gpx = b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg></trkseg></trk></gpx>'
        resp = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(empty_gpx), "empty.gpx")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_post_valid_gpx_agnostic_sets_session_and_redirects(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(gpx), "flight.gpx"), "mode": "agnostic"},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "pilot/gps-import/review" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "pilot_gps_import" in sess

    def test_post_valid_gpx_one_aircraft_sets_gps_import_session(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            "/pilot/gps-import",
            data={
                "gps_files": (io.BytesIO(gpx), "flight.gpx"),
                "mode": "one_aircraft",
                "aircraft_id": str(ac_id),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        # Redirect target now uses the registration (AircraftRefConverter)
        # rather than the numeric id.
        assert "/aircraft/OO-PIL/gps-import/review" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "gps_import" in sess
            assert sess["gps_import"]["aircraft_id"] == ac_id

    def test_post_one_aircraft_no_aircraft_id_flashes_warning(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        resp = client.post(
            "/pilot/gps-import",
            data={
                "gps_files": (io.BytesIO(gpx), "flight.gpx"),
                "mode": "one_aircraft",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"aircraft" in resp.data.lower()

    def test_post_empty_filename_silently_skipped(self, client, app):
        """Werkzeug routes empty-filename parts to request.form, not request.files.
        The continue branch is excluded via pragma; this test documents the behavior.
        """
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(b"data"), "")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_post_file_too_large_flashes_error(self, client, app):
        """Files exceeding the byte limit produce an error flash (lines 1126-1127)."""
        from unittest.mock import patch

        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        with patch("pilots.routes._GPS_MAX_BYTES", 5):
            resp = client.post(
                "/pilot/gps-import",
                data={"gps_files": (io.BytesIO(b"1234567"), "track.gpx")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert b"too large" in resp.data.lower()

    def test_post_parse_error_flashes_error(self, client, app):
        """ValueError from parse_gps_file produces an error flash (lines 1130-1132)."""
        from unittest.mock import patch

        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        with patch(
            "aircraft.gps_import.parse_gps_file",
            side_effect=ValueError("bad format"),
        ):
            resp = client.post(
                "/pilot/gps-import",
                data={"gps_files": (io.BytesIO(b"garbage"), "track.gpx")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert b"bad format" in resp.data


# ── Review route ──────────────────────────────────────────────────────────────


class TestPilotGpsReview:
    def test_no_session_redirects_to_upload(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.get("/pilot/gps-import/review", follow_redirects=False)
        assert resp.status_code == 302
        assert "pilot/gps-import" in resp.headers["Location"]

    def test_session_with_real_gpx_renders_review(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name
        try:
            with client.session_transaction() as sess:
                sess["pilot_gps_import"] = {
                    "user_id": uid,
                    "files": [
                        {
                            "tmp_path": tmp_path,
                            "original_filename": "flight.gpx",
                            "format": "gpx",
                            "classification": "flight",
                            "trkpt_count": 6,
                            "hint_dep": "EBNM",
                            "hint_arr": "EBAW",
                            "device_id": None,
                        }
                    ],
                    "skipped_empty": 0,
                }
            resp = client.get("/pilot/gps-import/review")
            assert resp.status_code == 200
            assert b"GPS" in resp.data
        finally:
            os.unlink(tmp_path)

    def test_missing_tmp_file_redirects_to_upload(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["pilot_gps_import"] = {
                "user_id": uid,
                "files": [
                    {
                        "tmp_path": "/tmp/no-such-file-99999.gpx",
                        "original_filename": "missing.gpx",
                        "format": "gpx",
                        "classification": "flight",
                        "trkpt_count": 4,
                        "hint_dep": None,
                        "hint_arr": None,
                        "device_id": None,
                    }
                ],
                "skipped_empty": 0,
            }
        resp = client.get("/pilot/gps-import/review", follow_redirects=False)
        assert resp.status_code == 302
        assert "pilot/gps-import" in resp.headers["Location"]

    def test_match_via_pic_user_id(self, client, app):
        """A Flight where the pilot occupies the pic_user_id slot is
        returned as a match for the segment."""
        import decimal

        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name

        try:
            with app.app_context():
                entry = Flight(
                    aircraft_id=ac_id,
                    date=datetime(2024, 6, 1).date(),
                    departure_icao="EBNM",
                    arrival_icao="EBAW",
                    flight_time=decimal.Decimal("1.0"),
                    source="manual",
                    block_off_utc=_utc(10, 0),
                    block_on_utc=_utc(11, 0),
                    pic_user_id=uid,
                    pic_name="Test Pilot",
                )
                db.session.add(entry)
                db.session.commit()
                entry_id = entry.id

            with client.session_transaction() as sess:
                sess["pilot_gps_import"] = {
                    "user_id": uid,
                    "files": [
                        {
                            "tmp_path": tmp_path,
                            "original_filename": "flight.gpx",
                            "format": "gpx",
                            "classification": "flight",
                            "trkpt_count": 6,
                            "hint_dep": None,
                            "hint_arr": None,
                            "device_id": None,
                        }
                    ],
                    "skipped_empty": 0,
                }

            resp = client.get("/pilot/gps-import/review")
            assert resp.status_code == 200
            # Session should have segments with matched_flight_id set
            with client.session_transaction() as sess:
                segs = sess["pilot_gps_import"].get("segments", [])
                assert len(segs) > 0
                assert segs[0].get("matched_flight_id") == entry_id
        finally:
            os.unlink(tmp_path)

    def test_match_via_second_crew_user_id(self, client, app):
        """A Flight where the pilot occupies the second_crew_user_id slot
        (e.g. a student on a dual flight) is also returned as a match."""
        import decimal

        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name

        try:
            with app.app_context():
                entry = Flight(
                    aircraft_id=ac_id,
                    date=datetime(2024, 6, 1).date(),
                    departure_icao="EBNM",
                    arrival_icao="EBAW",
                    flight_time=decimal.Decimal("1.0"),
                    source="manual",
                    block_off_utc=_utc(10, 5),
                    block_on_utc=_utc(10, 55),
                    pic_name="Instructor",
                    second_crew_user_id=uid,
                    second_crew_name="Test Pilot",
                    second_crew_role="student",
                )
                db.session.add(entry)
                db.session.commit()
                entry_id = entry.id

            with client.session_transaction() as sess:
                sess["pilot_gps_import"] = {
                    "user_id": uid,
                    "files": [
                        {
                            "tmp_path": tmp_path,
                            "original_filename": "flight.gpx",
                            "format": "gpx",
                            "classification": "flight",
                            "trkpt_count": 6,
                            "hint_dep": None,
                            "hint_arr": None,
                            "device_id": None,
                        }
                    ],
                    "skipped_empty": 0,
                }
            resp = client.get("/pilot/gps-import/review")
            assert resp.status_code == 200
            with client.session_transaction() as sess:
                segs = sess["pilot_gps_import"].get("segments", [])
                assert any(s.get("matched_flight_id") == entry_id for s in segs)
        finally:
            os.unlink(tmp_path)


# ── Confirm-one route ──────────────────────────────────────────────────────────


class TestPilotGpsConfirmOne:
    def test_no_session_redirects_to_upload(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "pilot/gps-import" in resp.headers["Location"]
        assert "review" not in resp.headers["Location"]

    def test_empty_segments_redirects_to_upload(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["pilot_gps_import"] = {
                "user_id": uid,
                "files": [],
                "segments": [],
                "skipped_empty": 0,
            }
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" not in resp.headers["Location"]

    def test_invalid_seg_idx_redirects_to_review(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "99"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" in resp.headers["Location"]

    def test_non_numeric_seg_idx_redirects_to_review(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "abc"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" in resp.headers["Location"]

    def test_already_confirmed_segment_redirects(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        # Two-segment session with seg 0 already confirmed
        with client.session_transaction() as sess:
            sess["pilot_gps_import"] = {
                "user_id": uid,
                "files": [],
                "segments": [_seg_dict(0), _seg_dict(1)],
                "confirmed_segments": {"0": 42},
                "skipped_empty": 0,
            }
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" in resp.headers["Location"]

    def test_skip_partial_marks_skipped_redirects_to_review(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid, segments=[_seg_dict(0), _seg_dict(1)])
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0", "skip": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess["pilot_gps_import"]["confirmed_segments"]["0"] == "skip"

    def test_skip_all_clears_session_redirects_to_logbook(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid, segments=[_seg_dict(0)])
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0", "skip": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "logbook" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "pilot_gps_import" not in sess

    def test_skip_last_with_prior_import_flashes_success(self, client, app):
        """Skipping the last segment when others were imported flashes success (lines 1337, 1356)."""
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["pilot_gps_import"] = {
                "user_id": uid,
                "files": [],
                "segments": [_seg_dict(0), _seg_dict(1)],
                "confirmed_segments": {"0": 42},
                "skipped_empty": 0,
            }
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "1", "skip": "1", "pilot_role": "pic"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "logbook" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "pilot_gps_import" not in sess

    def test_confirm_creates_flight_pic(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
                "other_reg": "OO-TEST",
                "other_make_model": "Cessna 172",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flight = Flight.query.filter_by(pic_user_id=uid).first()
            assert flight is not None
            assert flight.function_pic is not None
            assert flight.function_dual is None
            assert flight.other_aircraft_registration == "OO-TEST"

    def test_confirm_other_aircraft_stores_remarks(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
                "other_reg": "OO-TEST",
                "other_make_model": "Cessna 172",
                "remarks": "Local sightseeing flight",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flight = Flight.query.filter_by(pic_user_id=uid).first()
            assert flight is not None
            assert flight.notes == "Local sightseeing flight"

    def test_confirm_creates_flight_dual(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "dual",
                "resolution": "other_aircraft",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flight = Flight.query.filter_by(second_crew_user_id=uid).first()
            assert flight is not None
            assert flight.function_dual is not None
            assert flight.function_pic is None

    def test_confirm_managed_aircraft_creates_single_flight_row(self, client, app):
        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "managed_aircraft",
                "aircraft_id": str(ac_id),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flights = Flight.query.filter_by(aircraft_id=ac_id).all()
            # Airframe side and pilot-log side are the same unified row —
            # no separate linked pilot-logbook entry to check for.
            assert len(flights) == 1
            assert flights[0].pic_user_id == uid

    def test_confirm_managed_aircraft_rejects_cross_tenant_aircraft_id(
        self, client, app
    ):
        """A pilot cannot create a Flight on another tenant's aircraft (N-26)."""
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            other_tenant = Tenant(name="Other Tenant Hangar")
            db.session.add(other_tenant)
            db.session.flush()
            other_ac = Aircraft(
                tenant_id=other_tenant.id,
                registration="OO-OTHER",
                make="Cessna",
                model="172",
                logbook_time_precision="tenth_hour",
            )
            db.session.add(other_ac)
            db.session.commit()
            other_ac_id = other_ac.id

        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "managed_aircraft",
                "aircraft_id": str(other_ac_id),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=other_ac_id).count() == 0
            # Falls through to the external-aircraft branch: standalone
            # pilot-only row (aircraft_id NULL).
            flight = Flight.query.filter_by(pic_user_id=uid).first()
            assert flight is not None
            assert flight.aircraft_id is None

    def test_confirm_matched_flight_links_track_to_existing_entry(self, client, app):
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            entry = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
                block_off_utc=_utc(10, 0),
                block_on_utc=_utc(11, 0),
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        _set_upload_session(
            client, uid, segments=[_seg_dict(0, matched_flight_id=entry_id)]
        )
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            updated = db.session.get(Flight, entry_id)
            assert updated.gps_track_id is not None

    def test_confirm_matched_flight_replaces_track_and_updates_row_in_place(
        self, client, app
    ):
        """Re-confirming a matched flight (e.g. re-uploading the same GPS
        file) must delete the superseded GpsTrack and update the existing
        Flight row in place, not create a second one — there's only one
        unified row per real-world flight, so "not duplicating the pilot's
        own entry" and "not duplicating the flight" are the same guarantee
        now."""
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            old_track = GpsTrack(
                block_off_utc=_utc(9, 55),
                block_on_utc=_utc(10, 55),
                departure_icao="EBNM",
                arrival_icao="EBAW",
            )
            db.session.add(old_track)
            db.session.flush()
            entry = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="gps_import",
                block_off_utc=_utc(9, 55),
                block_on_utc=_utc(10, 55),
                gps_track_id=old_track.id,
                pic_user_id=uid,
                pic_name="Test Pilot",
                single_pilot_se=decimal.Decimal("1.0"),
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id
            old_track_id = old_track.id

        _set_upload_session(
            client, uid, segments=[_seg_dict(0, matched_flight_id=entry_id)]
        )
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={"seg_idx": "0", "pilot_role": "pic"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            # Still exactly one row for this pilot — not duplicated.
            assert Flight.query.filter_by(pic_user_id=uid).count() == 1
            updated = db.session.get(Flight, entry_id)
            assert updated is not None  # updated in place, not replaced
            assert db.session.get(GpsTrack, old_track_id) is None  # superseded, deleted
            assert updated.gps_track_id != old_track_id

    def test_confirm_matched_flight_preserves_other_crew_slot(self, client, app):
        """Confirming a matched flight where the current pilot occupies the
        second-crew slot must link the GPS track (a single field on the
        shared row) without disturbing the other crew member's identity —
        the old two-table design needed to propagate the track link to a
        second PilotLogbookEntry row; the unified row makes that automatic."""
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            other_user = User(
                email="renter@pgps.example.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(other_user)
            db.session.flush()
            other_uid = other_user.id

            entry = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
                block_off_utc=_utc(10, 0),
                block_on_utc=_utc(11, 0),
                pic_user_id=other_uid,
                pic_name="Other Pilot",
                second_crew_user_id=uid,
                second_crew_name="Test Pilot",
                second_crew_role="student",
            )
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id

        _set_upload_session(
            client, uid, segments=[_seg_dict(0, matched_flight_id=entry_id)]
        )
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "dual",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            updated = db.session.get(Flight, entry_id)
            assert updated.gps_track_id is not None
            # Other crew member's identity untouched by this confirmation.
            assert updated.pic_user_id == other_uid

    def test_confirm_stale_matched_flight_id_falls_through_to_external(
        self, client, app
    ):
        """If matched_flight_id no longer exists in DB, treat segment as unmatched."""
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(
            client, uid, segments=[_seg_dict(0, matched_flight_id=99999)]
        )
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flight = Flight.query.filter_by(pic_user_id=uid).first()
            assert flight is not None

    def test_confirm_external_aircraft_with_geojson_saves_gps_track(self, client, app):
        """External aircraft resolution with real geojson writes a GpsTrack (lines 1458-1468)."""
        import json
        import tempfile

        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)

        geojson_data = {"type": "LineString", "coordinates": [[4.0, 51.0], [4.1, 51.1]]}
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(geojson_data, tf)
            geojson_path = tf.name

        try:
            seg = _seg_dict()
            seg["geojson_path"] = geojson_path

            with client.session_transaction() as sess:
                sess["pilot_gps_import"] = {
                    "user_id": uid,
                    "files": [],
                    "segments": [seg],
                    "skipped_empty": 0,
                }

            resp = client.post(
                "/pilot/gps-import/confirm-one",
                data={
                    "seg_idx": "0",
                    "pilot_role": "pic",
                    "resolution": "other_aircraft",
                    "other_reg": "OO-EXT",
                    "other_make_model": "Cessna 172",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302
            with app.app_context():
                track = GpsTrack.query.order_by(GpsTrack.id.desc()).first()
                assert track is not None
                assert track.geojson is not None
        finally:
            with contextlib.suppress(
                FileNotFoundError
            ):  # _gps_cleanup may have already deleted it
                os.unlink(geojson_path)

    def test_confirm_partial_redirects_to_review(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid, segments=[_seg_dict(0), _seg_dict(1)])
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "review" in resp.headers["Location"]

    def test_confirm_all_handled_clears_session_and_redirects_to_logbook(
        self, client, app
    ):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid, segments=[_seg_dict(0)])
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "logbook" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "pilot_gps_import" not in sess

    def test_invalid_pilot_role_normalised_to_pic(self, client, app):
        uid, _, _ = _make_user_and_aircraft(app)
        _login(client, uid)
        _set_upload_session(client, uid)
        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "INVALID",
                "resolution": "other_aircraft",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            flight = Flight.query.filter_by(pic_user_id=uid).first()
            assert flight is not None
            assert flight.function_pic is not None


# ── _pilot_seg_match_dict unit tests ─────────────────────────────────────────


class TestPilotSegMatchDict:
    def test_no_matches_returns_empty_dict(self, app):
        from pilots.routes import _pilot_seg_match_dict  # pyright: ignore[reportMissingImports]

        result = _pilot_seg_match_dict([])
        assert result["matched_flight_id"] is None
        assert result["matched_ambiguous"] is False
        assert result["matched_candidates"] == []

    def test_single_match_not_ambiguous(self, app):
        from pilots.routes import _pilot_seg_match_dict  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            fe = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="manual",
            )
            db.session.add(fe)
            db.session.commit()
            result = _pilot_seg_match_dict([fe])
        assert result["matched_flight_id"] == fe.id
        assert result["matched_ambiguous"] is False
        assert len(result["matched_candidates"]) == 1

    def test_multiple_matches_sets_ambiguous(self, app):
        from pilots.routes import _pilot_seg_match_dict  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            entries = []
            for i in range(2):
                fe = Flight(
                    aircraft_id=ac_id,
                    date=datetime(2024, 6, 1).date(),
                    departure_icao="EBNM",
                    arrival_icao="EBAW",
                    flight_time=decimal.Decimal("1.0"),
                    source="manual",
                )
                db.session.add(fe)
                entries.append(fe)
            db.session.commit()
            result = _pilot_seg_match_dict(entries)
        assert result["matched_ambiguous"] is True
        assert len(result["matched_candidates"]) == 2


# ── Fuzzy match fallback (Step 2 follow-up) ─────────────────────────────────
# A flight logged manually or imported from a personal-logbook CSV never has
# block_off_utc/block_on_utc set, so _pilot_match_segment's exact overlap
# check can never find it. _pilot_fuzzy_match_segment is the fallback —
# reuses pilots.logbook_import._score_candidate (the same near-match scorer
# the CSV-import review already uses) rather than a second implementation.


class TestPilotFuzzyMatchSegment:
    def test_finds_csv_imported_flight_with_no_block_data(self, app):
        from pilots.routes import _pilot_fuzzy_match_segment  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            imported = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                single_pilot_se=decimal.Decimal("1.0"),
                pic_user_id=uid,
                source="logbook_import",
            )
            db.session.add(imported)
            db.session.commit()
            imported_id = imported.id

            seg = _seg_dict(0)
            matches = _pilot_fuzzy_match_segment(uid, seg, _utc(10, 0), _utc(11, 0))
        assert [fe.id for fe in matches] == [imported_id]

    def test_excludes_flight_with_real_gps_block_data(self, app):
        """A flight that already has its own GPS track is a different real
        flight, not the one the exact-overlap check just ruled out — must
        not show up as a fuzzy candidate too."""
        from pilots.routes import _pilot_fuzzy_match_segment  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            already_tracked = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                single_pilot_se=decimal.Decimal("1.0"),
                pic_user_id=uid,
                block_off_utc=_utc(6, 0),
                block_on_utc=_utc(7, 0),
            )
            db.session.add(already_tracked)
            db.session.commit()

            seg = _seg_dict(0)
            matches = _pilot_fuzzy_match_segment(uid, seg, _utc(10, 0), _utc(11, 0))
        assert matches == []

    def test_below_threshold_returns_empty(self, app):
        from pilots.routes import _pilot_fuzzy_match_segment  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            unrelated = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBLG",
                arrival_icao="EBCI",
                single_pilot_se=decimal.Decimal("3.5"),
                pic_user_id=uid,
            )
            db.session.add(unrelated)
            db.session.commit()

            seg = _seg_dict(0)
            matches = _pilot_fuzzy_match_segment(uid, seg, _utc(10, 0), _utc(11, 0))
        assert matches == []


class TestPilotSegMatchDictForceAmbiguous:
    def test_single_fuzzy_match_is_still_ambiguous(self, app):
        """Unlike an exact GPS-block overlap, a single fuzzy match is a
        guess and must not auto-apply — force_ambiguous keeps the picker
        (and its explicit "none of these" option) in front of the human."""
        from pilots.routes import _pilot_seg_match_dict  # pyright: ignore[reportMissingImports]
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            fe = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
                source="logbook_import",
            )
            db.session.add(fe)
            db.session.commit()
            result = _pilot_seg_match_dict([fe], force_ambiguous=True)
        assert result["matched_ambiguous"] is True
        assert len(result["matched_candidates"]) == 1
        assert result["matched_flight_id"] == fe.id


class TestPilotConfirmMatchedFlightIdOverride:
    """Regression coverage: the "select the matching flight" picker
    rendered by pilots/gps_import_review.html submits a `matched_flight_id`
    radio value, but pilot_gps_import_confirm_one never read it — the
    human's choice was silently ignored in favour of whichever candidate
    happened to be scored/listed first. Fixed alongside the fuzzy-match
    fallback since the new "possible match" flow depends on it."""

    def test_form_selection_overrides_default_candidate(self, client, app):
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            default_candidate = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
            )
            other_candidate = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.1"),
            )
            db.session.add_all([default_candidate, other_candidate])
            db.session.commit()
            default_id, other_id = default_candidate.id, other_candidate.id

        seg = _seg_dict(0, matched_flight_id=default_id)
        seg["matched_ambiguous"] = True
        seg["matched_candidates"] = [
            {
                "id": default_id,
                "str": "default",
                "aircraft_id": ac_id,
                "aircraft_reg": "OO-PIL",
                "has_existing_track": False,
            },
            {
                "id": other_id,
                "str": "other",
                "aircraft_id": ac_id,
                "aircraft_reg": "OO-PIL",
                "has_existing_track": False,
            },
        ]
        _set_upload_session(client, uid, segments=[seg])

        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "matched_flight_id": str(other_id),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            assert db.session.get(Flight, other_id).gps_track_id is not None
            assert db.session.get(Flight, default_id).gps_track_id is None

    def test_none_of_these_creates_new_flight_instead(self, client, app):
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            candidate = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                flight_time=decimal.Decimal("1.0"),
            )
            db.session.add(candidate)
            db.session.commit()
            candidate_id = candidate.id

        seg = _seg_dict(0, matched_flight_id=candidate_id)
        seg["matched_ambiguous"] = True
        seg["matched_candidates"] = [
            {
                "id": candidate_id,
                "str": "candidate",
                "aircraft_id": ac_id,
                "aircraft_reg": "OO-PIL",
                "has_existing_track": False,
            }
        ]
        _set_upload_session(client, uid, segments=[seg])

        resp = client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
                "matched_flight_id": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            assert db.session.get(Flight, candidate_id).gps_track_id is None
            new_flight = Flight.query.filter(Flight.id != candidate_id).first()
            assert new_flight is not None
            assert new_flight.pic_user_id == uid


class TestPilotFuzzyMatchEndToEnd:
    """Real upload → review → confirm round trip, proving the fuzzy
    fallback is actually wired into pilot_gps_import_review (not just unit
    tested in isolation)."""

    def test_review_surfaces_and_confirm_merges_csv_imported_flight(self, client, app):
        import decimal

        uid, _, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        with app.app_context():
            imported = Flight(
                aircraft_id=ac_id,
                date=datetime(2024, 6, 1).date(),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                single_pilot_se=decimal.Decimal("1.0"),
                pic_user_id=uid,
                pic_name="Test Pilot",
                source="logbook_import",
            )
            db.session.add(imported)
            db.session.commit()
            imported_id = imported.id

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name
        try:
            with client.session_transaction() as sess:
                sess["pilot_gps_import"] = {
                    "user_id": uid,
                    "files": [
                        {
                            "tmp_path": tmp_path,
                            "original_filename": "flight.gpx",
                            "format": "gpx",
                            "classification": "flight",
                            "trkpt_count": 6,
                            "hint_dep": "EBNM",
                            "hint_arr": "EBAW",
                            "device_id": None,
                        }
                    ],
                    "skipped_empty": 0,
                }
            resp = client.get("/pilot/gps-import/review")
            assert resp.status_code == 200

            with client.session_transaction() as sess:
                segs = sess["pilot_gps_import"]["segments"]
            assert segs[0]["matched_ambiguous"] is True
            assert segs[0]["matched_flight_id"] == imported_id

            confirm = client.post(
                "/pilot/gps-import/confirm-one",
                data={
                    "seg_idx": "0",
                    "pilot_role": "pic",
                    "matched_flight_id": str(imported_id),
                },
                follow_redirects=True,
            )
            assert confirm.status_code == 200
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        with app.app_context():
            assert Flight.query.filter_by(aircraft_id=ac_id).count() == 1
            fe = db.session.get(Flight, imported_id)
            assert fe.gps_track_id is not None
            assert fe.pic_name == "Test Pilot"
