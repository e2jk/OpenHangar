"""
Tests for the kept original GPS files beyond storing them
(tests/test_gps_archive.py covers that):
  - each imported track links to the file it came from (GpsTrack.source_files),
    including the flight form's own GPS upload
  - downloading one flight's file, or zips per aircraft and per pilot
  - the configuration page's count/size of kept files
  - temporary import files: left out of backups, cleared daily
"""

import io
import os
import time
import zipfile
from datetime import UTC, date, datetime

import pytest
from models import (  # pyright: ignore[reportMissingImports]
    Flight,
    GpsTrack,
    db,
)
from services.gps_archive import (  # pyright: ignore[reportMissingImports]
    archive_relpath,
    archive_stats,
    iter_archive_files,
    resolve_pilot_archive_name,
    sources_for_window,
    write_zip,
)
from services.temp_cleanup import (  # pyright: ignore[reportMissingImports]
    cleanup_temp_uploads,
)

from tests.test_gps_archive import _login, _world
from tests.test_gps_import import (
    _gpx_bytes,
    _session_state,  # pyright: ignore[reportMissingImports]
)


@pytest.fixture
def uploads(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    return tmp_path


def _put(root, rel, data=b"x"):
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def _meta(rel, t0, t1):
    return {"archive_path": rel, "t_start": t0, "t_end": t1}


def _dt(h, m=0):
    return datetime(2024, 6, 1, h, m, tzinfo=UTC)


# ── Helpers ───────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_archive_relpath(self, tmp_path):
        assert (
            archive_relpath(str(tmp_path / "a" / "b.gpx"), str(tmp_path)) == "a/b.gpx"
        )

    def test_resolve_pilot_archive_name_only_own_existing_files(self, tmp_path):
        _put(tmp_path, "_pilots/7/gps_logs/flight.gpx")
        _put(tmp_path, "_pilots/8/gps_logs/other.gpx")
        assert (
            resolve_pilot_archive_name(str(tmp_path), 7, "flight.gpx")
            == "_pilots/7/gps_logs/flight.gpx"
        )
        for name in (None, "", "missing.gpx", "../8/gps_logs/other.gpx", "other.gpx"):
            assert resolve_pilot_archive_name(str(tmp_path), 7, name) is None

    def test_sources_for_window_lists_the_covering_files(self):
        a = _meta("a.csv", "2024-06-01T09:00:00+00:00", "2024-06-01T10:30:00+00:00")
        b = _meta("b.csv", "2024-06-01T11:00:00+00:00", "2024-06-01T13:00:00+00:00")
        assert sources_for_window([a, b], _dt(11, 10), _dt(12, 0)) == ["b.csv"]
        assert sources_for_window([b, a], _dt(9, 10), _dt(10, 0)) == ["a.csv"]
        # A flight spanning both files (logger restarted in flight): both,
        # in time order.
        assert sources_for_window([b, a], _dt(10), _dt(11, 30)) == ["a.csv", "b.csv"]
        # Outside every file, or no usable span: nothing guessed.
        assert sources_for_window([a, b], _dt(14), _dt(15)) == []
        bad = {"archive_path": "c.csv", "t_start": None, "t_end": None}
        assert (
            sources_for_window([bad, {"archive_path": "d.csv"}], _dt(9), _dt(10)) == []
        )
        # A single file is the source whatever its span.
        assert sources_for_window([bad], _dt(9), _dt(10)) == ["c.csv"]
        assert (
            sources_for_window([{"original_filename": "old.gpx"}], _dt(9), _dt(10))
            == []
        )

    def test_iter_write_zip_and_stats(self, tmp_path):
        _put(tmp_path, "club/OO-ABC/gps_logs/log.csv", b"abc")
        _put(tmp_path, "club/OO-ABC/gps_logs/.part")
        _put(tmp_path, "_pilots/7/gps_logs/log.csv", b"defg")
        _put(tmp_path, "club/OO-ABC/insurance/cert.pdf", b"not a gps log")
        assert iter_archive_files(str(tmp_path), "club/OO-ABC/gps_logs") == [
            "club/OO-ABC/gps_logs/log.csv"
        ]
        assert iter_archive_files(str(tmp_path), "club/OO-XYZ/gps_logs") == []
        buf = io.BytesIO()
        written = write_zip(
            str(tmp_path),
            [
                "club/OO-ABC/gps_logs/log.csv",
                "_pilots/7/gps_logs/log.csv",
                "club/OO-ABC/gps_logs/log.csv",  # listed twice: zipped once
                "_pilots/7/gps_logs/gone.csv",  # missing: skipped
            ],
            buf,
        )
        assert written == 2
        with zipfile.ZipFile(buf) as zf:
            assert sorted(zf.namelist()) == ["log-1.csv", "log.csv"]
        assert archive_stats(str(tmp_path)) == (2, 7)
        assert archive_stats(str(tmp_path / "nowhere")) == (0, 0)


# ── Tracks link to their file ─────────────────────────────────────────────────


def _two_file_metas(slug):
    return [
        {
            "tmp_path": "/tmp/nonexistent-a.csv",
            "original_filename": "a.csv",
            "format": "garmin_csv",
            "classification": "flight",
            "trkpt_count": 5,
            "hint_dep": None,
            "hint_arr": None,
            "device_id": None,
            **_meta(
                f"{slug}/OO-ARC/gps_logs/a.csv",
                "2024-06-01T07:00:00+00:00",
                "2024-06-01T09:00:00+00:00",
            ),
        },
        {
            "tmp_path": "/tmp/nonexistent-b.csv",
            "original_filename": "b.csv",
            "format": "garmin_csv",
            "classification": "flight",
            "trkpt_count": 5,
            "hint_dep": None,
            "hint_arr": None,
            "device_id": None,
            **_meta(
                f"{slug}/OO-ARC/gps_logs/b.csv",
                "2024-06-01T09:50:00+00:00",
                "2024-06-01T11:10:00+00:00",
            ),
        },
    ]


def _segment(**extra):
    seg = {
        "idx": 0,
        "block_off_utc": "2024-06-01T10:00:00+00:00",
        "block_on_utc": "2024-06-01T11:00:00+00:00",
        "takeoff_utc": "2024-06-01T10:06:00+00:00",
        "landing_utc": "2024-06-01T10:54:00+00:00",
        "departure_icao": "EBNM",
        "arrival_icao": "EBAW",
        "flight_time_raw_h": 1.0,
        "flight_time_rounded_h": 1.0,
        "flight_time_h": 1.0,
        "landing_count": 1,
        "is_ground_only": False,
        "track_geojson": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[4.0, 51.0], [4.1, 51.1]],
            },
            "properties": {},
        },
        "geojson_path": None,
    }
    seg.update(extra)
    return seg


def _geojson_file(root):
    path = root / "segment.geojson"
    path.write_text(
        '{"type": "Feature", "geometry": {"type": "LineString", '
        '"coordinates": [[4.0, 51.0], [4.1, 51.1]]}, "properties": {}}'
    )
    return str(path)


class TestTracksLinkToTheirFile:
    def test_aircraft_upload_records_path_and_span(self, app, client, uploads):
        uid, slug, ac_id = _world(app, "gof1@example.com")
        _login(client, uid)
        client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": [(io.BytesIO(_gpx_bytes()), "flight.gpx")]},
            content_type="multipart/form-data",
        )
        with client.session_transaction() as sess:
            state = dict(sess["gps_import"])
        if "spill_path" in state:  # large state lives in a file (see the route)
            import json

            with open(state["spill_path"], encoding="utf-8") as fh:
                state.update(json.load(fh))
        meta = state["files"][0]
        assert meta["archive_path"] == f"{slug}/OO-ARC/gps_logs/flight.gpx"
        assert meta["t_start"].startswith("2024-06-01T10:00")

    def test_aircraft_confirm_links_the_covering_file(self, app, client, uploads):
        uid, slug, ac_id = _world(app, "gof2@example.com")
        _login(client, uid)
        with client.session_transaction() as sess:
            sess["gps_import"] = {
                "user_id": uid,
                "aircraft_id": ac_id,
                "files": _two_file_metas(slug),
                "segments": [_segment()],
                "skipped_empty": 0,
            }
        client.post(f"/aircraft/{ac_id}/gps-import/confirm-one", data={"seg_idx": "0"})
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=ac_id).one()
            assert fe.gps_track.source_files == [f"{slug}/OO-ARC/gps_logs/b.csv"]

    def test_pilot_upload_and_confirm(self, app, client, uploads):
        uid, slug, _ac_id = _world(app, "gof3@example.com")
        _login(client, uid)
        client.post(
            "/pilot/gps-import",
            data={"gps_files": [(io.BytesIO(_gpx_bytes()), "own.gpx")]},
            content_type="multipart/form-data",
        )
        with client.session_transaction() as sess:
            assert _session_state(sess, "pilot_gps_import")["files"][0][
                "archive_path"
            ] == (f"_pilots/{uid}/gps_logs/own.gpx")
            sess["pilot_gps_import"] = {
                "user_id": uid,
                "files": _two_file_metas(slug),
                "segments": [
                    _segment(
                        matched_flight_id=None,
                        matched_candidates=[],
                        geojson_path=_geojson_file(uploads),
                    )
                ],
                "skipped_empty": 0,
            }
        client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "resolution": "other_aircraft",
                "other_reg": "OO-TEST",
                "other_make_model": "Cessna 172",
            },
        )
        with app.app_context():
            fe = Flight.query.filter_by(pic_user_id=uid).one()
            assert fe.gps_track.source_files == [f"{slug}/OO-ARC/gps_logs/b.csv"]


class TestFlightFormUpload:
    def _form(self, ac_id, **extra):
        data = {
            "aircraft_id": str(ac_id),
            "date": "2024-06-01",
            "departure_icao": "EBNM",
            "arrival_icao": "EBAW",
            "flight_time_counter_start": "100.0",
            "flight_time_counter_end": "101.0",
            "crew_name_0": "Test Pilot",
            "crew_role_0": "PIC",
            "gps_filename": "flight.gpx",
            "gps_geojson": '{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[4.0, 51.0], [4.1, 51.1]]}, "properties": {}}',
        }
        data.update(extra)
        return data

    def test_parse_keeps_the_file_and_save_links_it(self, app, client, uploads):
        uid, _slug, ac_id = _world(app, "gof4@example.com")
        _login(client, uid)
        resp = client.post(
            "/flights/parse-gps",
            data={"gps_file": (io.BytesIO(_gpx_bytes()), "flight.gpx")},
            content_type="multipart/form-data",
        )
        assert resp.get_json()["data"]["archive_name"] == "flight.gpx"
        assert (uploads / "_pilots" / str(uid) / "gps_logs" / "flight.gpx").is_file()

        client.post("/flights/new", data=self._form(ac_id, gps_archive="flight.gpx"))
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=ac_id).one()
            assert fe.gps_track.source_files == [f"_pilots/{uid}/gps_logs/flight.gpx"]
            fid = fe.id

        resp = client.get(f"/flights/{fid}/gps-file/0")
        assert resp.status_code == 200
        assert resp.data == _gpx_bytes()
        assert "attachment" in resp.headers["Content-Disposition"]
        page = client.get(f"/aircraft/{ac_id}/flights/{fid}").data.decode()
        assert f'href="/flights/{fid}/gps-file/0" hx-boost="false"' in page

    def test_unparseable_upload_is_still_kept(self, app, client, uploads):
        uid, _slug, _ac_id = _world(app, "gof5@example.com")
        _login(client, uid)
        client.post(
            "/flights/parse-gps",
            data={"gps_file": (io.BytesIO(b"<gpx/>"), "broken.gpx")},
            content_type="multipart/form-data",
        )
        assert (uploads / "_pilots" / str(uid) / "gps_logs" / "broken.gpx").is_file()

    def test_forged_archive_name_is_not_linked(self, app, client, uploads):
        uid, _slug, ac_id = _world(app, "gof6@example.com")
        _login(client, uid)
        client.post(
            "/flights/new",
            data=self._form(ac_id, gps_archive="../../etc/passwd"),
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=ac_id).one()
            assert fe.gps_track is not None
            assert fe.gps_track.source_files is None

    def test_existing_track_gets_linked_on_edit(self, app, client, uploads):
        uid, _slug, ac_id = _world(app, "gof7@example.com")
        _login(client, uid)
        client.post("/flights/new", data=self._form(ac_id))
        _put(uploads, f"_pilots/{uid}/gps_logs/later.gpx")
        with app.app_context():
            fid = Flight.query.filter_by(aircraft_id=ac_id).one().id
        client.post(
            f"/flights/{fid}/edit", data=self._form(ac_id, gps_archive="later.gpx")
        )
        with app.app_context():
            assert db.session.get(Flight, fid).gps_track.source_files == [
                f"_pilots/{uid}/gps_logs/later.gpx"
            ]

    def test_upload_on_an_existing_flight_is_added_never_replacing(
        self, app, client, uploads
    ):
        """A file uploaded on a saved flight joins its GPS track, as a taxi
        part when it's ground-only; the flight's recorded data stays."""
        uid, _slug, ac_id = _world(app, "gof9@example.com")
        _login(client, uid)
        flight_part = '{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[4.0, 51.0], [4.1, 51.1]]}, "properties": {}}'
        client.post(
            "/flights/new",
            data=self._form(
                ac_id,
                gps_geojson=flight_part,
                gps_block_off_utc="2024-06-01T10:00:00+00:00",
                gps_block_on_utc="2024-06-01T11:00:00+00:00",
                departure_time="10:05",
                arrival_time="10:55",
            ),
        )
        _put(uploads, f"_pilots/{uid}/gps_logs/taxi.gpx")
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=ac_id).one()
            fid, first_files = fe.id, list(fe.gps_track.source_files or [])

        # Saved again without a new file: the echoed track is left alone.
        client.post(
            f"/flights/{fid}/edit",
            data=self._form(
                ac_id,
                gps_filename="",
                departure_time="10:05",
                arrival_time="10:55",
            ),
        )
        # A ground run later that day, uploaded on the flight.
        client.post(
            f"/flights/{fid}/edit",
            data=self._form(
                ac_id,
                gps_filename="taxi.gpx",
                gps_archive="taxi.gpx",
                gps_ground_only="1",
                gps_geojson='{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[4.2, 51.2], [4.21, 51.21]]}, "properties": {}}',
                gps_block_off_utc="2024-06-01T13:00:00+00:00",
                gps_block_on_utc="2024-06-01T13:05:00+00:00",
                departure_time="10:05",
                arrival_time="10:55",
            ),
        )
        with app.app_context():
            fe = db.session.get(Flight, fid)
            parts = fe.gps_track.geojson["properties"]["parts"]
            assert [p["taxi"] for p in parts] == [False, True]
            assert fe.gps_track.source_files == [
                *first_files,
                f"_pilots/{uid}/gps_logs/taxi.gpx",
            ]
            # The flight's own times and GPS block times are unchanged.
            assert fe.departure_time.strftime("%H:%M") == "10:05"
            assert fe.block_on_utc.replace(tzinfo=UTC) == datetime(
                2024, 6, 1, 11, tzinfo=UTC
            )

    def test_upload_on_a_flight_without_a_track_starts_one(self, app, client, uploads):
        uid, _slug, ac_id = _world(app, "gof10@example.com")
        _login(client, uid)
        client.post(
            "/flights/new", data=self._form(ac_id, gps_filename="", gps_geojson="")
        )
        with app.app_context():
            fe = Flight.query.filter_by(aircraft_id=ac_id).one()
            assert fe.gps_track_id is None
            fid = fe.id
        client.post(f"/flights/{fid}/edit", data=self._form(ac_id))
        with app.app_context():
            assert db.session.get(Flight, fid).gps_track_id is not None

    def test_the_form_knows_when_the_flight_exists(self, app, client, uploads):
        uid, _slug, ac_id = _world(app, "gof11@example.com")
        _login(client, uid)
        assert (
            'data-existing-flight="1"' not in client.get("/flights/new").data.decode()
        )
        client.post("/flights/new", data=self._form(ac_id))
        with app.app_context():
            fid = Flight.query.filter_by(aircraft_id=ac_id).one().id
        page = client.get(f"/flights/{fid}/edit").data.decode()
        assert 'data-existing-flight="1"' in page
        assert "the flight&#39;s own data stays as entered" in page or (
            "own data stays as entered" in page
        )
        resp = client.post(
            "/flights/parse-gps",
            data={"gps_file": (io.BytesIO(_gpx_bytes()), "flight.gpx")},
            content_type="multipart/form-data",
        )
        assert resp.get_json()["data"]["ground_only"] is False

    def test_pilot_entry_form_links_too(self, app, client, uploads):
        uid, _slug, _ac_id = _world(app, "gof8@example.com")
        _login(client, uid)
        _put(uploads, f"_pilots/{uid}/gps_logs/rental.gpx")
        client.post(
            "/pilot/logbook/new",
            data={
                "date": "2024-06-01",
                "aircraft_type": "C172",
                "aircraft_registration": "OO-RNT",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "single_pilot_se": "1.0",
                "function_pic": "1.0",
                "landings_day": "1",
                "gps_filename": "rental.gpx",
                "gps_archive": "rental.gpx",
                "gps_geojson": '{"type": "Feature", "geometry": {"type": "LineString", "coordinates": []}, "properties": {}}',
            },
        )
        with app.app_context():
            fe = Flight.query.filter_by(pic_user_id=uid).one()
            assert fe.gps_track.source_files == [f"_pilots/{uid}/gps_logs/rental.gpx"]
            fid = fe.id

        # Uploading another file on the entry adds it to its track — here in
        # place of the first, whose times were unknown (services.gps_link).
        _put(uploads, f"_pilots/{uid}/gps_logs/better.gpx")
        client.post(
            f"/pilot/logbook/{fid}/edit",
            data={
                "date": "2024-06-01",
                "aircraft_type": "C172",
                "aircraft_registration": "OO-RNT",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "single_pilot_se": "1.0",
                "function_pic": "1.0",
                "landings_day": "1",
                "gps_filename": "better.gpx",
                "gps_archive": "better.gpx",
                "gps_geojson": '{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[4, 51], [4.1, 51.1]]}, "properties": {}}',
            },
        )
        with app.app_context():
            assert db.session.get(Flight, fid).gps_track.source_files == [
                f"_pilots/{uid}/gps_logs/better.gpx"
            ]


def test_pilot_entry_edit_keeps_or_adds_to_its_track(app, client, uploads):
    """Saving a pilot logbook entry without a new file leaves its track
    alone; a ground-only file is added to it as a taxi part."""
    uid, _slug, _ac_id = _world(app, "gof12@example.com")
    _login(client, uid)
    form = {
        "date": "2024-06-01",
        "aircraft_type": "C172",
        "aircraft_registration": "OO-RNT",
        "departure_place": "EBNM",
        "arrival_place": "EBAW",
        "single_pilot_se": "1.0",
        "function_pic": "1.0",
        "landings_day": "1",
    }
    line = '{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[4, 51], [4.1, 51.1]]}, "properties": {}}'
    _put(uploads, f"_pilots/{uid}/gps_logs/flight.gpx")
    client.post(
        "/pilot/logbook/new",
        data={
            **form,
            "gps_filename": "flight.gpx",
            "gps_archive": "flight.gpx",
            "gps_geojson": line,
            "gps_block_off_utc": "2024-06-01T10:00:00+00:00",
            "gps_block_on_utc": "2024-06-01T11:00:00+00:00",
        },
    )
    with app.app_context():
        fe = Flight.query.filter_by(pic_user_id=uid).one()
        fid, track_id = fe.id, fe.gps_track_id
    client.post(f"/pilot/logbook/{fid}/edit", data={**form, "gps_geojson": line})
    _put(uploads, f"_pilots/{uid}/gps_logs/taxi.gpx")
    client.post(
        f"/pilot/logbook/{fid}/edit",
        data={
            **form,
            "gps_filename": "taxi.gpx",
            "gps_archive": "taxi.gpx",
            "gps_ground_only": "1",
            "gps_geojson": line,
            "gps_block_off_utc": "2024-06-01T13:00:00+00:00",
            "gps_block_on_utc": "2024-06-01T13:05:00+00:00",
        },
    )
    with app.app_context():
        fe = db.session.get(Flight, fid)
        assert fe.gps_track_id == track_id
        assert [p["taxi"] for p in fe.gps_track.geojson["properties"]["parts"]] == [
            False,
            True,
        ]


# ── Downloads ─────────────────────────────────────────────────────────────────


def _flight_with_file(app, uploads, ac_id, rel, **flight):
    _put(uploads, rel, b"log-" + rel.encode())
    with app.app_context():
        track = GpsTrack(source_filename=rel.rsplit("/", 1)[-1], source_files=[rel])
        db.session.add(track)
        db.session.flush()
        fe = Flight(
            aircraft_id=ac_id,
            date=date(2024, 6, 1),
            departure_icao="EBNM",
            arrival_icao="EBAW",
            gps_track_id=track.id,
            **flight,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


class TestDownloads:
    def test_single_file_access(self, app, client, uploads):
        uid, slug, ac_id = _world(app, "gof9@example.com")
        other_uid, _s, _a = _world(app, "gof9b@example.com")
        fid = _flight_with_file(app, uploads, ac_id, f"{slug}/OO-ARC/gps_logs/x.csv")
        _login(client, uid)
        assert client.get(f"/flights/{fid}/gps-file/0").status_code == 200
        # Someone from another club can't.
        _login(client, other_uid)
        assert client.get(f"/flights/{fid}/gps-file/0").status_code == 404
        assert client.get("/flights/999999/gps-file/0").status_code == 404

    def test_single_file_for_the_flights_own_pilot_and_missing_cases(
        self, app, client, uploads
    ):
        uid, _slug, _ac = _world(app, "gof10@example.com")
        other_uid, _s, _a = _world(app, "gof10b@example.com")
        rel = f"_pilots/{uid}/gps_logs/own.gpx"
        _put(uploads, rel)
        with app.app_context():
            track = GpsTrack(source_files=[rel])
            no_file = GpsTrack(source_files=None)
            gone = GpsTrack(source_files=[f"_pilots/{uid}/gps_logs/gone.gpx"])
            escape = GpsTrack(source_files=["../outside.gpx"])
            db.session.add_all([track, no_file, gone, escape])
            db.session.flush()
            ids = []
            for t in (track, no_file, gone, escape):
                fe = Flight(
                    date=date(2024, 6, 1),
                    pic_user_id=uid,
                    other_aircraft_registration="OO-RNT",
                    gps_track_id=t.id,
                )
                db.session.add(fe)
                db.session.flush()
                ids.append(fe.id)
            plain = Flight(date=date(2024, 6, 2), pic_user_id=uid)
            db.session.add(plain)
            db.session.commit()
            ids.append(plain.id)
        _login(client, uid)
        own, no_file_id, gone_id, escape_id, plain_id = ids
        assert client.get(f"/flights/{own}/gps-file/0").status_code == 200
        for fid in (no_file_id, gone_id, escape_id, plain_id):
            assert client.get(f"/flights/{fid}/gps-file/0").status_code == 404
        # A standalone flight is only the pilot's own.
        _login(client, other_uid)
        assert client.get(f"/flights/{own}/gps-file/0").status_code == 404

    def test_aircraft_zip(self, app, client, uploads):
        uid, slug, ac_id = _world(app, "gof11@example.com")
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/gps-logs.zip")
        assert resp.status_code == 302  # nothing kept yet
        _put(uploads, f"{slug}/OO-ARC/gps_logs/unlinked.csv")
        _flight_with_file(app, uploads, ac_id, f"_pilots/{uid}/gps_logs/form.gpx")
        resp = client.get(f"/aircraft/{ac_id}/gps-logs.zip")
        assert resp.status_code == 200
        assert "OO-ARC-gps-logs.zip" in resp.headers["Content-Disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert sorted(zf.namelist()) == ["form.gpx", "unlinked.csv"]
        page = client.get(f"/aircraft/{ac_id}/gps-import/history").data.decode()
        assert 'href="/aircraft/OO-ARC/gps-logs.zip"' in page

    def test_pilot_zip(self, app, client, uploads):
        uid, slug, ac_id = _world(app, "gof12@example.com")
        _login(client, uid)
        resp = client.get("/pilot/gps-logs.zip")
        assert resp.status_code == 302
        _put(uploads, f"_pilots/{uid}/gps_logs/own.gpx")
        _flight_with_file(
            app, uploads, ac_id, f"{slug}/OO-ARC/gps_logs/club.csv", pic_user_id=uid
        )
        _flight_with_file(app, uploads, ac_id, f"{slug}/OO-ARC/gps_logs/not-mine.csv")
        resp = client.get("/pilot/gps-logs.zip")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            assert sorted(zf.namelist()) == ["club.csv", "own.gpx"]
        assert 'href="/pilot/gps-logs.zip"' in client.get("/pilot/tracks").data.decode()


# ── Configuration page ────────────────────────────────────────────────────────


class TestConfigPage:
    def test_counts_kept_gps_files(self, app, client, uploads):
        uid, slug, _ac = _world(app, "gof13@example.com")
        with app.app_context():
            from models import User  # pyright: ignore[reportMissingImports]

            db.session.get(User, uid).is_instance_admin = True
            db.session.commit()
        _login(client, uid)
        _put(uploads, f"{slug}/OO-ARC/gps_logs/a.csv", b"x" * 2048)
        _put(uploads, f"_pilots/{uid}/gps_logs/b.gpx", b"y" * 2048)
        page = client.get("/config/").data.decode()
        assert "Original GPS files" in page
        assert "4.0 KB across 2 files" in page


# ── Temporary import files ────────────────────────────────────────────────────


class TestTemporaryFiles:
    def test_daily_cleanup_removes_only_stale_temp_files(self, tmp_path):
        old = _put(tmp_path, "import_tmp/abandoned.xlsx")
        fresh = _put(tmp_path, "gps_import_tmp/in-progress.gpx")
        nested = _put(tmp_path, "amp_import_tmp/sub/old.xlsx")
        kept = _put(tmp_path, "club/OO-ARC/gps_logs/old.csv")
        two_days_ago = time.time() - 48 * 3600
        for rel in (old, nested, kept):
            os.utime(tmp_path / rel, (two_days_ago, two_days_ago))
        assert cleanup_temp_uploads(str(tmp_path)) == 2
        assert not (tmp_path / old).exists() and not (tmp_path / nested).exists()
        assert (tmp_path / fresh).exists() and (tmp_path / kept).exists()
        assert cleanup_temp_uploads(str(tmp_path / "nowhere")) == 0

    def test_cleanup_tolerates_a_file_removed_meanwhile(self, tmp_path, monkeypatch):
        _put(tmp_path, "import_tmp/racing.xlsx")
        monkeypatch.setattr(
            os.path, "getmtime", lambda p: (_ for _ in ()).throw(FileNotFoundError(p))
        )
        assert cleanup_temp_uploads(str(tmp_path)) == 0

    def test_backup_leaves_temp_folders_out(self, tmp_path):
        from config.routes import _add_uploads_to_zip  # pyright: ignore[reportMissingImports]

        _put(tmp_path, "import_tmp/a.xlsx")
        _put(tmp_path, "gps_import_tmp/b.gpx")
        _put(tmp_path, "club/OO-ARC/gps_logs/c.csv")
        _put(tmp_path, "club/import_tmp/not-top-level.txt")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            _add_uploads_to_zip(zf, str(tmp_path))
        with zipfile.ZipFile(buf) as zf:
            assert sorted(zf.namelist()) == [
                "uploads/club/OO-ARC/gps_logs/c.csv",
                "uploads/club/import_tmp/not-top-level.txt",
            ]

    def test_daily_checks_run_the_cleanup(self, app, uploads, monkeypatch):
        from services import notification_service  # pyright: ignore[reportMissingImports]

        calls = []
        monkeypatch.setattr(
            "services.temp_cleanup.cleanup_temp_uploads",
            lambda folder: calls.append(folder),
        )
        notification_service.run_daily_checks(app)
        assert calls == [str(uploads)]
