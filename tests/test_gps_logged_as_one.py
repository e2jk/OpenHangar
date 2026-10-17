"""
Tests for joining GPS segments that one logbook flight records as a single
flight (e.g. a landing to check a FADEC warning with the engine running,
then on to the destination — logged as one flight with two landings):
  - merge_segments_logged_as_one: only within the logged block times, along
    the logged route, and when the recording never stopped in between
  - logged_flight_windows: which logbook flights can vouch for a merge
  - segment_times: a joined segment's airborne time leaves out the ground stop
  - the aircraft and pilot GPS reviews apply it
"""

import io
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from textwrap import dedent
from types import SimpleNamespace

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
    LoggedFlightWindow,
    TrackPoint,
    detect_segments,
    logged_flight_windows,
    merge_segments_logged_as_one,
    segment_times,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)

_BASE = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _BASE + timedelta(minutes=minutes)


# Minute-by-minute ground speeds (kt): a 10-minute circuit, landing, taxi
# back and a 5-minute stop with the engine running, then a 28-minute leg.
_TWO_LEGS = (
    [(m, 10.0) for m in range(3)]  # taxi out
    + [(m, 80.0) for m in range(3, 13)]  # circuit
    + [(m, 10.0) for m in range(13, 16)]  # taxi back
    + [(m, 0.0) for m in range(16, 21)]  # stop, engine running
    + [(m, 10.0) for m in range(21, 23)]  # taxi out again
    + [(m, 80.0) for m in range(23, 51)]  # on to the destination
    + [(m, 10.0) for m in range(51, 53)]  # taxi in
)


def _points(timeline, skip=()):
    return [
        TrackPoint(lat=0.0, lon=0.0, alt_m=0.0, speed_kt=kt, utc_dt=_at(m))
        for m, kt in timeline
        if m not in skip
    ]


def _window(start=-5, end=55, dep=None, arr=None):
    return LoggedFlightWindow(_at(start), _at(end), dep, arr)


def _segments(skip=()):
    pts = _points(_TWO_LEGS, skip)
    segs = detect_segments(pts)
    assert len(segs) == 2  # what the logbook has to overrule
    return segs, pts


class TestMergeSegmentsLoggedAsOne:
    def test_joins_legs_one_logged_flight_covers(self):
        segs, pts = _segments()
        (merged,) = merge_segments_logged_as_one(segs, pts, [_window()])
        assert merged.block_off_utc == segs[0].block_off_utc
        assert merged.block_on_utc == segs[1].block_on_utc
        assert merged.landing_count == 2
        # The ground stop is part of the track and the block time…
        assert merged.trackpoints == pts
        assert merged.flight_time_rounded_h == 0.9
        # …but not of the airborne time.
        assert merged.airborne_raw_h == (9 + 27) / 60

    def test_three_legs(self):
        third = (
            [(m, 0.0) for m in range(53, 58)]  # stop again
            + [(m, 10.0) for m in range(58, 60)]
            + [(m, 80.0) for m in range(60, 80)]
            + [(m, 10.0) for m in range(80, 82)]
        )
        pts = _points(_TWO_LEGS + third)
        segs = detect_segments(pts)
        assert len(segs) == 3
        (merged,) = merge_segments_logged_as_one(segs, pts, [_window(end=90)])
        assert merged.landing_count == 3

    def test_nothing_logged_keeps_the_split(self):
        segs, pts = _segments()
        assert merge_segments_logged_as_one(segs, pts, []) is segs

    def test_single_segment_left_alone(self):
        segs, pts = _segments()
        assert merge_segments_logged_as_one(segs[:1], pts, [_window()]) == segs[:1]

    def test_logged_flight_ending_before_second_leg(self):
        segs, pts = _segments()
        assert len(merge_segments_logged_as_one(segs, pts, [_window(end=25)])) == 2

    def test_logged_flight_starting_after_first_leg(self):
        segs, pts = _segments()
        assert len(merge_segments_logged_as_one(segs, pts, [_window(start=20)])) == 2

    def test_recording_gap_keeps_the_split(self):
        """Avionics switched off at the stop (one data-log file per power
        cycle) — the engine most likely was too: two flights."""
        segs, pts = _segments(skip=range(15, 22))
        assert len(merge_segments_logged_as_one(segs, pts, [_window()])) == 2

    def test_route_must_chain(self):
        segs, pts = _segments()
        segs[0].arrival_icao = "LFGI"
        segs[1].departure_icao = "EBNM"
        assert len(merge_segments_logged_as_one(segs, pts, [_window()])) == 2

    def test_route_must_match_logged_departure_and_arrival(self):
        segs, pts = _segments()
        segs[0].departure_icao = segs[0].arrival_icao = "LFGI"
        segs[1].departure_icao, segs[1].arrival_icao = "LFGI", "EBNM"
        for window, expected in (
            (_window(dep="LFGI", arr="EBNM"), 1),
            (_window(dep="EBAW", arr="EBNM"), 2),
            (_window(dep="LFGI", arr="LFGI"), 2),
        ):
            got = merge_segments_logged_as_one(segs, pts, [window])
            assert len(got) == expected, window

    def test_ground_only_segments_never_joined(self):
        segs, pts = _segments()
        segs[1].is_ground_only = True
        assert len(merge_segments_logged_as_one(segs, pts, [_window()])) == 2
        segs[1].is_ground_only = False
        segs[0].is_ground_only = True
        assert len(merge_segments_logged_as_one(segs, pts, [_window()])) == 2


class TestLoggedFlightWindows:
    def _flight(self, **kw):
        base = {
            "date": date(2024, 6, 1),
            "departure_time": None,
            "arrival_time": None,
            "takeoff_time": None,
            "landing_time": None,
            "departure_icao": "LFGI",
            "arrival_icao": "EBNM",
        }
        return SimpleNamespace(**(base | kw))

    def test_block_times_preferred_then_takeoff_landing(self):
        block, airborne = logged_flight_windows(
            [
                self._flight(
                    departure_time=time(16, 18),
                    arrival_time=time(18, 30),
                    takeoff_time=time(16, 25),
                ),
                self._flight(takeoff_time=time(9, 0), landing_time=time(10, 0)),
            ]
        )
        assert (block.start_utc.time(), block.end_utc.time()) == (
            time(16, 18),
            time(18, 30),
        )
        assert block.departure_icao == "LFGI"
        assert airborne.start_utc == datetime(2024, 6, 1, 9, 0, tzinfo=UTC)

    def test_flights_without_both_times_skipped(self):
        assert (
            logged_flight_windows(
                [
                    self._flight(departure_time=time(9, 0)),
                    self._flight(date=None, departure_time=time(9, 0)),
                ]
            )
            == []
        )

    def test_past_midnight(self):
        (w,) = logged_flight_windows(
            [self._flight(departure_time=time(23, 30), arrival_time=time(0, 40))]
        )
        assert w.end_utc - w.start_utc == timedelta(minutes=70)


class TestSegmentTimesOfJoinedSegment:
    def test_summed_airborne_time_used(self):
        seg = {
            "flight_time_raw_h": 0.9,
            "takeoff_utc": _at(3).isoformat(),
            "landing_utc": _at(50).isoformat(),
            "airborne_raw_h": 0.6,
        }
        times = segment_times(seg, "tenth_hour")
        assert times.airborne_time == Decimal("0.6")
        assert times.block_time == Decimal("0.9")
        assert times.takeoff_time == time(10, 3)


# ── Route integration ─────────────────────────────────────────────────────────


def _gpx(timeline) -> bytes:
    trkpts = "".join(
        f"""
      <trkpt lat="45.{m:02d}" lon="5.0">
        <ele>100</ele>
        <speed>{kt / 1.94384:.3f}</speed>
        <time>{_at(m).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>
      </trkpt>"""
        for m, kt in timeline
    )
    return dedent(f"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>FADEC check</name><trkseg>{trkpts}
      </trkseg></trk>
    </gpx>
    """).encode()


def _world(app, email, pilot_flight):
    with app.app_context():
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name="Logged As One Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-ONE",
            make="Robin",
            model="DR401",
            logbook_time_precision="tenth_hour",
        )
        db.session.add(ac)
        db.session.flush()
        flight = Flight(
            aircraft_id=ac.id,
            date=date(2024, 6, 1),
            departure_time=time(10, 0),
            arrival_time=time(10, 55),
            landings_day=2,
            pic_user_id=user.id if pilot_flight else None,
        )
        db.session.add(flight)
        db.session.commit()
        return user.id, ac.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["pilot_access"] = True


class TestReviewsJoinLoggedLegs:
    def test_aircraft_review(self, client, app):
        uid, ac_id = _world(app, "one1@example.com", pilot_flight=False)
        _login(client, uid)
        client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={"gps_files": (io.BytesIO(_gpx(_TWO_LEGS)), "fadec.gpx")},
            content_type="multipart/form-data",
        )
        client.get(f"/aircraft/{ac_id}/gps-import/review")
        with client.session_transaction() as sess:
            spill_path = sess["gps_import"]["spill_path"]
        with open(spill_path, encoding="utf-8") as fh:
            segs = json.load(fh)["segments"]
        assert [s["landing_count"] for s in segs] == [2]
        assert segs[0]["airborne_raw_h"] == (9 + 27) / 60

    def test_pilot_review(self, client, app):
        uid, _ac_id = _world(app, "one2@example.com", pilot_flight=True)
        _login(client, uid)
        client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(_gpx(_TWO_LEGS)), "fadec.gpx")},
            content_type="multipart/form-data",
        )
        client.get("/pilot/gps-import/review")
        with client.session_transaction() as sess:
            segs = sess["pilot_gps_import"]["segments"]
        assert [s["landing_count"] for s in segs] == [2]
