"""Tests for pilot GPS import (airplane-agnostic batch upload from pilot logbook)."""

import io
import os
import tempfile
from datetime import datetime, timezone
from textwrap import dedent

import bcrypt

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    FlightCrew,
    FlightEntry,
    PilotLogbookEntry,
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
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
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
        assert f"/aircraft/{ac_id}/gps-import/review" in resp.headers["Location"]
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

    def test_match_via_flight_crew(self, client, app):
        """FlightEntry the pilot is crew on is returned as a match for the segment."""
        import decimal

        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name

        try:
            with app.app_context():
                entry = FlightEntry(
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
                db.session.flush()
                db.session.add(
                    FlightCrew(
                        flight_id=entry.id, user_id=uid, name="Test Pilot", role="pic"
                    )
                )
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

    def test_match_via_logbook_entry(self, client, app):
        """PilotLogbookEntry linked flight is returned as a match for the segment."""
        import decimal

        uid, tenant_id, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)

        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tf:
            tf.write(gpx)
            tmp_path = tf.name

        try:
            with app.app_context():
                entry = FlightEntry(
                    aircraft_id=ac_id,
                    date=datetime(2024, 6, 1).date(),
                    departure_icao="EBNM",
                    arrival_icao="EBAW",
                    flight_time=decimal.Decimal("1.0"),
                    source="manual",
                    block_off_utc=_utc(10, 5),
                    block_on_utc=_utc(10, 55),
                )
                db.session.add(entry)
                db.session.flush()
                pentry = PilotLogbookEntry(
                    pilot_user_id=uid,
                    flight_id=entry.id,
                    date=datetime(2024, 6, 1).date(),
                    source="manual",
                )
                db.session.add(pentry)
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

    def test_confirm_creates_pilot_logbook_entry_pic(self, client, app):
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
            pentry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pentry is not None
            assert pentry.function_pic is not None
            assert pentry.function_dual is None
            assert pentry.aircraft_registration == "OO-TEST"

    def test_confirm_creates_pilot_logbook_entry_dual(self, client, app):
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
            pentry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pentry is not None
            assert pentry.function_dual is not None
            assert pentry.function_pic is None

    def test_confirm_managed_aircraft_creates_flight_and_logbook_entries(
        self, client, app
    ):
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
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1
            pentry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pentry is not None
            # PilotLogbookEntry should be linked to the new FlightEntry
            fe = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert pentry.flight_id == fe.id

    def test_confirm_matched_flight_links_track_to_existing_entry(self, client, app):
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
            updated = db.session.get(FlightEntry, entry_id)
            assert updated.gps_track_id is not None

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
            pentry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pentry is not None

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
            try:
                os.unlink(geojson_path)
            except FileNotFoundError:
                pass  # _gps_cleanup already deleted it

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
            pentry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert pentry is not None
            assert pentry.function_pic is not None


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
            fe = FlightEntry(
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
                fe = FlightEntry(
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
