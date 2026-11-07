"""One flight, several GPS logs — and the logbook stays authoritative.

- services.gps_link.link_track: a flight's first track, the same files
  again (no change), the same time again (replaced), another part of the
  flight (combined in time order, files listed together);
- linking GPS to a recorded flight never changes its times or date;
- a single close candidate is a match, not a doubt;
- "Accept all best matches" on the aircraft review page.
"""

import decimal
import io
import json
from datetime import UTC, date, datetime, time

from models import Flight, GpsTrack, db  # pyright: ignore[reportMissingImports]
from services.gps_link import (  # pyright: ignore[reportMissingImports]
    combine_geojson,
    link_track,
)

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _gpx_bytes,
    _login,
    _make_user_and_aircraft,
    _session_segments,
)


def _line(*pts):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(p) for p in pts]},
        "properties": {"k": 1},
    }


def _t(h, m=0):
    return datetime(2024, 6, 1, h, m, tzinfo=UTC)


class TestCombineGeojson:
    def test_joins_lines_in_order(self):
        out = combine_geojson(_line((1, 1)), _line((2, 2), (3, 3)))
        assert out["geometry"]["coordinates"] == [[1, 1], [2, 2], [3, 3]]
        assert out["properties"] == {"k": 1}
        bare = combine_geojson(
            {"type": "LineString", "coordinates": [[0, 0]]}, _line((9, 9))
        )
        assert bare["geometry"]["coordinates"] == [[0, 0], [9, 9]]
        assert bare["properties"] == {}

    def test_non_lines_keep_the_second(self):
        point = {"type": "Point", "coordinates": [0, 0]}
        assert combine_geojson(point, _line((1, 1))) == _line((1, 1))
        assert combine_geojson(None, "x") == "x"
        bad = {"type": "LineString", "coordinates": "nope"}
        assert combine_geojson(bad, _line((1, 1))) == _line((1, 1))


def _flight(app):
    _uid, _tid, ac_id = _make_user_and_aircraft(app)
    f = Flight(
        aircraft_id=ac_id,
        date=date(2024, 6, 1),
        departure_icao="EBAW",
        arrival_icao="EBAW",
        departure_time=time(10, 0),
        arrival_time=time(13, 0),
    )
    db.session.add(f)
    db.session.flush()
    return f


class TestLinkTrack:
    def test_first_same_files_replace_and_combine(self, app):
        with app.app_context():
            f = _flight(app)
            first = link_track(
                f,
                geojson=_line((1, 1)),
                files=["a.csv"],
                block_off=_t(10),
                block_on=_t(10, 30),
                departure_icao="EBAW",
            )
            assert f.gps_track_id == first.id
            assert first.source_files == ["a.csv"]

            # The same file again: nothing changes.
            again = link_track(
                f,
                geojson=_line((5, 5)),
                files=["a.csv"],
                block_off=_t(10),
                block_on=_t(10, 30),
            )
            assert again.id == first.id
            assert again.geojson == _line((1, 1))

            # Later part of the flight, from another file: combined.
            first.cached_png = b"old"
            later = link_track(
                f,
                geojson=_line((2, 2)),
                files=["b.csv"],
                block_off=_t(11),
                block_on=_t(12),
            )
            assert later.id == first.id
            assert later.source_files == ["a.csv", "b.csv"]
            assert later.geojson["geometry"]["coordinates"] == [[1, 1], [2, 2]]
            assert later.cached_png is None
            # Earlier part (stored naive, as SQLite returns it): put in front.
            later.block_off_utc = later.block_off_utc.replace(tzinfo=None)
            earlier = link_track(
                f,
                geojson=_line((0, 0)),
                files=["z.csv", "a.csv"],
                block_off=_t(9),
                block_on=_t(9, 30),
            )
            assert earlier.source_files == ["z.csv", "a.csv", "b.csv"]
            assert earlier.geojson["geometry"]["coordinates"][0] == [0, 0]
            assert f.block_off_utc == _t(9) and f.block_on_utc == _t(12)
            # The recorded times are the logbook's — untouched.
            assert (f.departure_time, f.arrival_time) == (time(10, 0), time(13, 0))

            # Same time again from a re-processed log: replaces the track.
            old_id = earlier.id
            replaced = link_track(
                f,
                geojson=_line((7, 7)),
                files=["c.csv"],
                block_off=_t(9, 10),
                block_on=_t(11, 30),
            )
            db.session.flush()
            assert replaced.id != old_id
            assert db.session.get(GpsTrack, old_id) is None
            assert replaced.source_files == ["c.csv"]

            # Unknown times can't be told apart: replaced too.
            nofiles = link_track(
                f, geojson=None, files=[], block_off=None, block_on=None
            )
            assert nofiles.source_files is None
            assert f.gps_track_id == nofiles.id


def _upload(client, ac_id):
    gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
    client.post(
        f"/aircraft/{ac_id}/gps-import",
        data={"gps_files": (io.BytesIO(gpx), "flight.gpx")},
        content_type="multipart/form-data",
    )
    client.get(f"/aircraft/{ac_id}/gps-import/review")
    with client.session_transaction() as sess:
        return _session_segments(sess), sess["gps_import"]["spill_path"]


def _logged_flight(app, ac_id, seg):
    with app.app_context():
        f = Flight(
            aircraft_id=ac_id,
            date=datetime.fromisoformat(seg["block_off_utc"]).date(),
            departure_icao="EBNM",
            arrival_icao="EBAW",
            departure_time=time(9, 54),
            arrival_time=time(10, 12),
            flight_time=decimal.Decimal(str(seg["flight_time_rounded_h"])),
            landing_count=seg["landing_count"],
            source="logbook_import",
        )
        db.session.add(f)
        db.session.commit()
        return f.id


class TestAcceptAllBestMatches:
    def test_links_matches_keeps_logbook_times_leaves_the_rest(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        (seg0,), _ = _upload(client, ac_id)
        fid = _logged_flight(app, ac_id, seg0)

        segs, spill = _upload(client, ac_id)
        assert segs[0]["matched_flight_id"] == fid
        assert segs[0]["matched_ambiguous"] is False
        page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
        assert "Accept all best matches" in page

        # Add a ground-only segment and one without any match.
        with open(spill, encoding="utf-8") as fh:
            state = json.load(fh)
        ground = dict(state["segments"][0], is_ground_only=True)
        unmatched = dict(state["segments"][0], matched_flight_id=None)
        state["segments"] += [ground, unmatched]
        with open(spill, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

        resp = client.post(f"/aircraft/{ac_id}/gps-import/confirm-all")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/gps-import/review")
        with app.app_context():
            f = db.session.get(Flight, fid)
            assert f.gps_track_id is not None
            assert (f.departure_time, f.arrival_time) == (time(9, 54), time(10, 12))
            assert Flight.query.filter_by(aircraft_id=ac_id).count() == 1
        with client.session_transaction() as sess:
            confirmed = sess["gps_import"]["confirmed_segments"]
        assert confirmed["1"] == "skip" and "2" not in confirmed

        # Again: the unmatched one still waits for a decision.
        client.post(f"/aircraft/{ac_id}/gps-import/confirm-all")
        # Skipping the last one completes the import.
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/confirm-one",
            data={"seg_idx": "2", "skip": "1"},
        )
        assert resp.headers["Location"].endswith("/flights")

    def test_all_matched_finishes_the_import(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        (seg0,), _ = _upload(client, ac_id)
        _logged_flight(app, ac_id, seg0)
        _upload(client, ac_id)
        resp = client.post(f"/aircraft/{ac_id}/gps-import/confirm-all")
        assert resp.headers["Location"].endswith("/flights")
        with client.session_transaction() as sess:
            assert "gps_import" not in sess

    def test_session_expired(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/gps-import/confirm-all")
        assert resp.headers["Location"].endswith("/gps-import")


class TestDownloadSecondFile:
    def test_each_file_listed_and_served(self, client, app):
        import os

        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        folder = app.config["UPLOAD_FOLDER"]
        rels = [f"_pilots/{uid}/gps_logs/p1.csv", f"_pilots/{uid}/gps_logs/p2.csv"]
        for rel in rels:
            os.makedirs(os.path.dirname(os.path.join(folder, rel)), exist_ok=True)
            with open(os.path.join(folder, rel), "w") as fh:
                fh.write(rel)
        with app.app_context():
            track = GpsTrack(source_files=rels, geojson=_line((4.0, 51.0), (4.1, 51.1)))
            db.session.add(track)
            db.session.flush()
            f = Flight(
                aircraft_id=ac_id,
                date=date(2024, 6, 1),
                departure_icao="EBAW",
                arrival_icao="EBAW",
                gps_track_id=track.id,
                pic_user_id=uid,
            )
            db.session.add(f)
            db.session.commit()
            fid = f.id
        resp = client.get(f"/flights/{fid}/gps-file/1")
        assert resp.status_code == 200 and resp.data == rels[1].encode()
        assert client.get(f"/flights/{fid}/gps-file/2").status_code == 404


class TestQuickTurnaroundLegs:
    """The next leg, minutes after a flight that already has GPS times, is
    not that flight's data again — unless the route agrees."""

    def test_block_match(self):
        from types import SimpleNamespace

        from services.gps_link import block_match  # pyright: ignore[reportMissingImports]

        first_leg = SimpleNamespace(
            block_off_utc=_t(7, 56),
            block_on_utc=_t(8, 20),
            departure_icao="EBNM",
            arrival_icao="EBST",
        )
        # Overlapping: the same flight whatever the route says.
        assert block_match(first_leg, _t(8, 0), _t(8, 30), "EBST", "EBNM")
        # Just after it: only on the same route (or an unknown one).
        assert not block_match(first_leg, _t(8, 27), _t(8, 56), "EBST", "EBNM")
        assert block_match(first_leg, _t(8, 27), _t(8, 56), "EBNM", "EBST")
        assert block_match(first_leg, _t(8, 27), _t(8, 56), "????", None)

    def test_next_leg_goes_to_its_own_flight(self, client, app):
        from datetime import timedelta

        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        (seg0,), _ = _upload(client, ac_id)
        seg_off = datetime.fromisoformat(seg0["block_off_utc"])
        with app.app_context():
            # The previous leg, GPS-linked, ending 7 minutes before.
            prev = Flight(
                aircraft_id=ac_id,
                date=seg_off.date(),
                departure_icao="EBAW",
                arrival_icao="EBNM",
                block_off_utc=seg_off - timedelta(minutes=40),
                block_on_utc=seg_off - timedelta(minutes=7),
            )
            db.session.add(prev)
            db.session.commit()
            prev_id = prev.id
        fid = _logged_flight(app, ac_id, seg0)
        segs, _ = _upload(client, ac_id)
        assert segs[0]["matched_flight_id"] == fid != prev_id


class TestGroundSessionsDaysApart:
    """A ground-only recording days before or after a flight (an engine
    run at the parking spot) is its own session, not that flight's taxi."""

    def test_split_at_recording_gaps_outside_the_flight(self):
        from datetime import timedelta

        from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
            TrackPoint,
            _split_into_raw_groups,
        )

        def run(start, speeds):
            return [
                TrackPoint(0.0, 0.0, 0.0, kt, start + timedelta(minutes=i))
                for i, kt in enumerate(speeds)
            ]

        before = run(_t(8) - timedelta(days=2), [0, 5, 0])
        flight = run(_t(8), [5, 10, 90, 100, 90, 10, 5])
        taxi_in = run(_t(8, 9), [5, 3])  # same session: 3 min later
        after = run(_t(8) + timedelta(days=3), [0, 8, 0])
        groups = _split_into_raw_groups(before + flight + taxi_in + after)
        assert [len(g) for g in groups] == [3, 9, 3]
        assert groups[1] == flight + taxi_in


class TestLaterFileOfATrackedFlight:
    """A later file of a flight that already has a track, uploaded on its
    own, is offered as another part of that flight — not as a new one."""

    def test_continuation_candidates_and_adds_to_track(self):
        from types import SimpleNamespace

        from services.gps_link import (  # pyright: ignore[reportMissingImports]
            adds_to_track,
            continuation_candidates,
        )

        def flight(dep, arr, t0=None, t1=None, track=True):
            return SimpleNamespace(
                date=date(2024, 6, 1),
                departure_icao=dep,
                arrival_icao=arr,
                departure_time=t0,
                arrival_time=t1,
                takeoff_time=None,
                landing_time=None,
                gps_track_id=1 if track else None,
                block_off_utc=_t(9) if track else None,
                block_on_utc=_t(9, 40) if track else None,
            )

        same_route = flight("EBNM", "EBAW", time(8, 55), time(10, 15))
        no_times = flight("EBNM", "EBST")
        night = flight("EBNM", "EBAW", time(23, 0), time(1, 0))  # past midnight
        other_route = flight("EBST", "EBZW", time(8, 55), time(10, 15))
        too_early = flight("EBNM", "EBAW", time(8, 0), time(9, 40))
        untracked = flight("EBNM", "EBAW", track=False)
        flights = [same_route, no_times, night, other_route, too_early, untracked]
        got = continuation_candidates(flights, _t(10), _t(10, 5), "EBNM", "EBAW")
        assert got == [same_route, no_times]
        # Overlapping its track: that's the same data again, not another part.
        assert (
            continuation_candidates([same_route], _t(9, 30), _t(9, 45), "EBNM", "EBAW")
            == []
        )

        assert adds_to_track(same_route, _t(10), _t(10, 5))
        assert not adds_to_track(same_route, _t(9, 30), _t(9, 45))
        assert not adds_to_track(untracked, _t(10), _t(10, 5))

    def _tracked_flight(self, app, ac_id, uid=None):
        with app.app_context():
            track = GpsTrack(
                block_off_utc=_t(9), block_on_utc=_t(9, 40), source_files=["a.csv"]
            )
            db.session.add(track)
            db.session.flush()
            f = Flight(
                aircraft_id=ac_id,
                date=date(2024, 6, 1),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                departure_time=time(8, 55),
                arrival_time=time(10, 15),
                block_off_utc=_t(9),
                block_on_utc=_t(9, 40),
                gps_track_id=track.id,
                pic_user_id=uid,
            )
            db.session.add(f)
            db.session.commit()
            return f.id

    def test_aircraft_review_offers_it_and_says_it_adds(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        fid = self._tracked_flight(app, ac_id)
        segs, _ = _upload(client, ac_id)
        assert segs[0]["matched_flight_id"] == fid
        assert segs[0]["matched_ambiguous"] is False
        assert segs[0]["matched_adds_to_track"] is True
        page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
        assert "this segment will be added to it" in page
        client.post(f"/aircraft/{ac_id}/gps-import/confirm-all")
        with app.app_context():
            track = db.session.get(Flight, fid).gps_track
            assert track.source_files[0] == "a.csv" and len(track.source_files) == 2
            assert Flight.query.filter_by(aircraft_id=ac_id).count() == 1

        # Uploaded again: now within its track — the same data, no new flight.
        segs, _ = _upload(client, ac_id)
        assert segs[0]["matched_flight_id"] == fid
        assert segs[0]["matched_adds_to_track"] is False

    def test_two_possible_flights_are_left_to_choose(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        ids = {self._tracked_flight(app, ac_id), self._tracked_flight(app, ac_id)}
        segs, _ = _upload(client, ac_id)
        assert segs[0]["matched_ambiguous"] is True
        assert {c["id"] for c in segs[0]["matched_candidates"]} == ids

    def test_pilot_review_offers_it(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        fid = self._tracked_flight(app, ac_id, uid=uid)
        gpx = _gpx_bytes(speeds_ms=[0.0, 20.0, 20.0, 20.0, 20.0, 0.0])
        client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(gpx), "flight.gpx")},
            content_type="multipart/form-data",
        )
        page = client.get("/pilot/gps-import/review").data.decode()
        with client.session_transaction() as sess:
            (seg,) = sess["pilot_gps_import"]["segments"]
        assert seg["matched_flight_id"] == fid
        assert seg["matched_adds_to_track"] is True
        assert "this segment will be added to it" in page
