"""
Tests for matching a GPS segment to a flight logged a day or two off — a
typo in a hand-entered logbook date (the GPS date is the reliable one):
  - rank_near_date_candidates: same route at both ends, at most
    NEAR_DATE_DAYS days off, closest date first
  - only a fallback for segments nothing matched on their own date, always
    offered for an explicit choice, never a flight years away
  - confirming it corrects the flight's date to the GPS one (aircraft and
    pilot GPS imports)
"""

import io
import json
from datetime import UTC, date, datetime, time, timedelta
from textwrap import dedent
from types import SimpleNamespace

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
    NEAR_DATE_DAYS,
    rank_near_date_candidates,
)
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

_GPS_DAY = date(2024, 6, 1)


class TestRankNearDateCandidates:
    def _flight(self, days, dep="EGSU", arr="EGSC", fid=None):
        return SimpleNamespace(
            id=fid or days,
            date=_GPS_DAY + timedelta(days=days),
            departure_icao=dep,
            arrival_icao=arr,
        )

    def _rank(self, flights, dep="EGSU", arr="EGSC"):
        fields = {"departure_icao": dep, "arrival_icao": arr}
        return rank_near_date_candidates(
            fields, _GPS_DAY, flights, lambda _f, _c: 100, 50
        )

    def test_closest_date_first_within_the_window(self):
        flights = [
            self._flight(2),
            self._flight(-1),
            self._flight(NEAR_DATE_DAYS + 1),
            self._flight(-3 * 365),
            self._flight(0),  # same day — handled by the same-day match
        ]
        assert [f.id for f in self._rank(flights)] == [-1, 2]

    def test_route_must_match_at_both_ends(self):
        flights = [
            self._flight(1, arr="EGHO"),
            self._flight(1, dep="EGHO"),
            self._flight(-1, fid=99),
        ]
        assert [f.id for f in self._rank(flights)] == [99]

    def test_unknown_route_never_matches(self):
        flights = [self._flight(1)]
        assert self._rank(flights, dep="") == []
        assert self._rank(flights, arr=None) == []

    def test_score_threshold_still_applies(self):
        fields = {"departure_icao": "EGSU", "arrival_icao": "EGSC"}
        got = rank_near_date_candidates(
            fields, _GPS_DAY, [self._flight(1)], lambda _f, _c: 10, 50
        )
        assert got == []


# ── Route integration ─────────────────────────────────────────────────────────


def _gpx(day=_GPS_DAY) -> bytes:
    """A 25-minute EGSU → EGSC flight from 14:08 UTC, away from any airport
    so the route comes from the track name."""
    start = datetime(day.year, day.month, day.day, 14, 8, tzinfo=UTC)
    speeds = [5.0] * 3 + [45.0] * 20 + [5.0] * 3
    trkpts = "".join(
        f"""
      <trkpt lat="{47.0 + i * 0.01:.2f}" lon="-30.0">
        <ele>300</ele>
        <speed>{spd}</speed>
        <time>{(start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>
      </trkpt>"""
        for i, spd in enumerate(speeds)
    )
    return dedent(f"""<?xml version="1.0"?>
    <gpx xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>EGSU - EGSC</name><trkseg>{trkpts}
      </trkseg></trk>
    </gpx>
    """).encode()


def _world(app, email, logged, pilot=False):
    """*logged*: (date, departure, arrival) of each flight in the logbook."""
    with app.app_context():
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name="Near Date Hangar")
        db.session.add(tenant)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-NDT",
            make="Robin",
            model="DR401",
            logbook_time_precision="tenth_hour",
        )
        db.session.add(ac)
        db.session.flush()
        ids = []
        for day, dep, arr in logged:
            f = Flight(
                aircraft_id=ac.id,
                date=day,
                departure_icao=dep,
                arrival_icao=arr,
                takeoff_time=time(14, 15),
                landing_time=time(14, 29),
                flight_time=0.3,
                pic_user_id=user.id if pilot else None,
            )
            db.session.add(f)
            db.session.flush()
            ids.append(f.id)
        db.session.commit()
        return user.id, ac.id, ids


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["pilot_access"] = True


def _aircraft_review(client, ac_id):
    client.post(
        f"/aircraft/{ac_id}/gps-import",
        data={"gps_files": (io.BytesIO(_gpx()), "flight.gpx")},
        content_type="multipart/form-data",
    )
    page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
    with client.session_transaction() as sess:
        spill_path = sess["gps_import"]["spill_path"]
    with open(spill_path, encoding="utf-8") as fh:
        (seg,) = json.load(fh)["segments"]
    return seg, page


class TestAircraftGpsImport:
    def test_date_typo_offered_and_corrected_on_confirm(self, client, app):
        uid, ac_id, (typo_id,) = _world(
            app, "nd1@example.com", [(date(2024, 5, 31), "EGSU", "EGSC")]
        )
        _login(client, uid)
        seg, page = _aircraft_review(client, ac_id)
        assert seg["matched_near_date"] is True
        assert seg["matched_ambiguous"] is True
        assert [c["id"] for c in seg["matched_candidates"]] == [typo_id]
        assert "possibly a date typo" in page
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "none",
                "matched_flight_id": str(typo_id),
            },
        )
        with app.app_context():
            f = db.session.get(Flight, typo_id)
            assert f.date == _GPS_DAY
            assert f.gps_track_id is not None
            assert Flight.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_none_of_these_keeps_the_logged_date(self, client, app):
        uid, ac_id, (typo_id,) = _world(
            app, "nd2@example.com", [(date(2024, 6, 2), "EGSU", "EGSC")]
        )
        _login(client, uid)
        _aircraft_review(client, ac_id)
        client.post(
            f"/aircraft/{ac_id}/gps-import/confirm-one",
            data={"seg_idx": "0", "pilot_role": "none", "matched_flight_id": ""},
        )
        with app.app_context():
            assert db.session.get(Flight, typo_id).date == date(2024, 6, 2)

    def test_same_day_match_wins(self, client, app):
        uid, ac_id, (same_day, _near) = _world(
            app,
            "nd3@example.com",
            [(_GPS_DAY, "EGSU", "EGSC"), (date(2024, 5, 31), "EGSU", "EGSC")],
        )
        _login(client, uid)
        seg, _page = _aircraft_review(client, ac_id)
        assert seg["matched_near_date"] is False
        assert [c["id"] for c in seg["matched_candidates"]] == [same_day]

    def test_far_or_other_route_never_offered(self, client, app):
        uid, ac_id, _ids = _world(
            app,
            "nd4@example.com",
            [
                (date(2021, 6, 1), "EGSU", "EGSC"),  # three years earlier
                (date(2024, 5, 28), "EGSU", "EGSC"),  # four days off
                (date(2024, 5, 31), "EGSU", "EGHO"),  # other destination
            ],
        )
        _login(client, uid)
        seg, _page = _aircraft_review(client, ac_id)
        assert seg["matched_flight_id"] is None
        assert seg["matched_near_date"] is False


class TestPilotGpsImport:
    def test_date_typo_offered_and_corrected_on_confirm(self, client, app):
        uid, _ac_id, (typo_id,) = _world(
            app, "nd5@example.com", [(date(2024, 6, 2), "EGSU", "EGSC")], pilot=True
        )
        _login(client, uid)
        client.post(
            "/pilot/gps-import",
            data={"gps_files": (io.BytesIO(_gpx()), "flight.gpx")},
            content_type="multipart/form-data",
        )
        page = client.get("/pilot/gps-import/review").data.decode()
        with client.session_transaction() as sess:
            (seg,) = sess["pilot_gps_import"]["segments"]
        assert seg["matched_near_date"] is True
        assert [c["id"] for c in seg["matched_candidates"]] == [typo_id]
        assert "possibly a date typo" in page
        client.post(
            "/pilot/gps-import/confirm-one",
            data={
                "seg_idx": "0",
                "pilot_role": "pic",
                "matched_flight_id": str(typo_id),
            },
        )
        with app.app_context():
            f = db.session.get(Flight, typo_id)
            assert f.date == _GPS_DAY
            assert db.session.get(GpsTrack, f.gps_track_id) is not None
