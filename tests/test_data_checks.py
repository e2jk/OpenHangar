"""Data checks: logged flights crossed with their GPS tracks.

- services.data_checks.check_flight: airfield (by distance, blank in the
  logbook), date, GPS track outside the logged times;
- per-aircraft cache invalidated by a flight change, dismissals, the stored
  open count behind the navbar bell;
- the Data checks page: list, "use the GPS value" (with a note of what
  the paper logbook says), dismiss, tenant isolation;
- the daily checks refresh the count.
"""

from datetime import UTC, date, datetime, time
from types import SimpleNamespace

from models import (  # pyright: ignore[reportMissingImports]
    AppSetting,
    DataCheckDismissal,
    Flight,
    GpsTrack,
    db,
)
from services import data_checks as dc  # pyright: ignore[reportMissingImports]

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)

EBAW = (51.190667, 4.463153)
EDBK = (52.918888, 12.425278)
EDRK = (50.325558, 7.528611)
_AIRPORTS = {"EBAW": EBAW, "EDBK": EDBK, "EDRK": EDRK}


def _line(*latlons):
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat, 100.0] for lat, lon in latlons],
        },
    }


def _t(h, m=0, day=18):
    return datetime(2025, 3, day, h, m, tzinfo=UTC)


def _flight(**kw):
    base = {
        "id": 1,
        "aircraft_id": 1,
        "date": date(2025, 3, 18),
        "departure_icao": "EBAW",
        "arrival_icao": "EDBK",
        "departure_time": time(9, 0),
        "arrival_time": time(11, 0),
        "takeoff_time": None,
        "landing_time": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _track(geojson=None, off=None, on=None):
    return SimpleNamespace(
        geojson=_line(EBAW, EDRK) if geojson is None else geojson,
        block_off_utc=_t(9, 5) if off is None else off,
        block_on_utc=_t(10, 50) if on is None else on,
    )


class TestCheckFlight:
    def test_misread_airfield_found_with_suggestion(self):
        (f,) = dc.check_flight(_flight(), _track(), _AIRPORTS)
        assert (f.kind, f.logged, f.gps, f.suggestion) == (
            "arrival",
            "EDBK",
            "EDRK",
            "EDRK",
        )
        assert f.distance_km > 400
        assert f.key == "arrival:EDRK"
        assert f.flight_date == "2025-03-18"

    def test_agreeing_flight_has_no_findings(self):
        assert dc.check_flight(_flight(arrival_icao="EDRK"), _track(), _AIRPORTS) == []

    def test_blank_or_unknown_logged_airfield(self):
        # Blank in the logbook, an airfield where the GPS landed: suggest it.
        (f,) = dc.check_flight(_flight(arrival_icao="????"), _track(), _AIRPORTS)
        assert (f.logged, f.gps, f.distance_km) == ("", "EDRK", None)
        # Blank, and nothing known where the GPS landed: nothing to say.
        far = _track(geojson=_line(EBAW, (0.0, 0.0)))
        assert dc.check_flight(_flight(arrival_icao=None), far, _AIRPORTS) == []
        # A field not in the list (private strip): nothing to measure against.
        assert dc.check_flight(_flight(arrival_icao="XXXX"), far, _AIRPORTS) == []
        # Landed away from the logged field, no airfield there: no suggestion.
        (f,) = dc.check_flight(_flight(arrival_icao="EDRK"), far, _AIRPORTS)
        assert (f.gps, f.suggestion) == ("", "")

    def test_date_and_times(self):
        # GPS one day later than logged.
        late = _track(off=_t(9, 5, day=19), on=_t(10, 50, day=19))
        (f,) = dc.check_flight(_flight(arrival_icao="EDRK"), late, _AIRPORTS)
        assert (f.kind, f.logged, f.gps, f.suggestion) == (
            "date",
            "2025-03-18",
            "2025-03-19",
            "2025-03-19",
        )
        # An hour before the logged times (local time logged as UTC?).
        early = _track(off=_t(8, 0), on=_t(9, 50))
        (f,) = dc.check_flight(_flight(arrival_icao="EDRK"), early, _AIRPORTS)
        assert (f.kind, f.logged, f.gps, f.suggestion) == (
            "outside_times",
            "09:00–11:00",
            "08:00–09:50",
            "",
        )
        # Within the 30 min margin, or past midnight: fine.
        ok = _track(off=_t(8, 40), on=_t(11, 25))
        assert dc.check_flight(_flight(arrival_icao="EDRK"), ok, _AIRPORTS) == []
        night = _flight(
            arrival_icao="EDRK",
            departure_time=None,
            arrival_time=None,
            takeoff_time=time(23, 30),
            landing_time=time(0, 30),
        )
        assert (
            dc.check_flight(night, _track(off=_t(23, 20), on=_t(23, 59)), _AIRPORTS)
            == []
        )
        # No logged times, no track times, no date: those checks are skipped.
        no_times = _flight(arrival_icao="EDRK", departure_time=None)
        assert dc.check_flight(no_times, early, _AIRPORTS) == []
        no_track_times = SimpleNamespace(
            geojson=_line(EBAW, EDRK), block_off_utc=None, block_on_utc=None
        )
        assert (
            dc.check_flight(_flight(arrival_icao="EDRK"), no_track_times, _AIRPORTS)
            == []
        )
        naive = _track(
            off=datetime(2025, 3, 18, 9, 5), on=datetime(2025, 3, 18, 10, 50)
        )
        assert (
            dc.check_flight(_flight(arrival_icao="EDRK", date=None), naive, _AIRPORTS)
            == []
        )

    def test_unusable_geojson_skips_the_airfield_check(self):
        for geojson in (
            "nope",
            {"type": "Feature", "geometry": "nope"},
            {"type": "LineString", "coordinates": []},
            {"type": "LineString", "coordinates": [["a", "b"]]},
        ):
            assert dc.check_flight(_flight(), _track(geojson=geojson), _AIRPORTS) == []
        bare = {
            "type": "LineString",
            "coordinates": [[EBAW[1], EBAW[0]], [EDRK[1], EDRK[0]]],
        }
        assert len(dc.check_flight(_flight(), _track(geojson=bare), _AIRPORTS)) == 1


def _tracked_flight(app, ac_id, arr="EDBK", **kw):
    with app.app_context():
        track = GpsTrack(
            geojson=_line(EBAW, EDRK), block_off_utc=_t(9, 5), block_on_utc=_t(10, 50)
        )
        db.session.add(track)
        db.session.flush()
        f = Flight(
            aircraft_id=ac_id,
            date=date(2025, 3, 18),
            departure_icao="EBAW",
            arrival_icao=arr,
            departure_time=time(9, 0),
            arrival_time=time(11, 0),
            gps_track_id=track.id,
            **kw,
        )
        db.session.add(f)
        db.session.commit()
        return f.id


class TestCacheAndCounts:
    def test_cache_dismissals_and_count(self, app):
        _uid, tid, ac_id = _make_user_and_aircraft(app)
        fid = _tracked_flight(app, ac_id)
        with app.app_context():
            assert dc.open_count(tid) == 0  # never refreshed
            assert dc.open_count(None) == 0
            assert dc.refresh_open_count(tid) == 1
            db.session.commit()
            assert dc.open_count(tid) == 1
            cache = db.session.get(AppSetting, f"data_checks:aircraft:{ac_id}")
            assert '"arrival"' in cache.value

            # Unchanged flights: the cached findings are used.
            cache.value = cache.value.replace("EDRK", "EDRX")
            db.session.commit()
            assert [f.gps for f in dc.aircraft_findings(ac_id)] == ["EDRX"]
            # A change to a tracked flight: checked again.
            db.session.get(Flight, fid).notes = "edited"
            db.session.commit()
            assert [f.gps for f in dc.aircraft_findings(ac_id)] == ["EDRK"]
            # An unreadable cache: checked again too.
            cache.value = "{broken"
            assert [f.gps for f in dc.aircraft_findings(ac_id)] == ["EDRK"]

            # An aircraft without tracked flights has nothing to report.
            from models import Aircraft  # pyright: ignore[reportMissingImports]

            db.session.add(
                Aircraft(tenant_id=tid, registration="OO-NOT", make="X", model="Y")
            )
            db.session.add(DataCheckDismissal(flight_id=fid, key="arrival:EDRK"))
            db.session.commit()
            assert dc.open_findings(tid) == []
            dc.refresh_all_open_counts()
            assert dc.open_count(tid) == 0

            db.session.get(AppSetting, f"data_checks:open:{tid}").value = "x"
            assert dc.open_count(tid) == 0

    def test_find_and_apply_suggestion_notes_the_paper_value(self, app):
        _uid, _tid, ac_id = _make_user_and_aircraft(app)
        fid = _tracked_flight(app, ac_id, notes="Nice day")
        with app.test_request_context():
            f = db.session.get(Flight, fid)
            finding = dc.find(f, "arrival:EDRK")
            assert dc.apply_suggestion(f, finding)
            assert f.arrival_icao == "EDRK"
            assert f.notes == (
                "Nice day\nArrival airfield corrected from the GPS track; "
                "the paper logbook says EDBK."
            )
            assert dc.find(f, "arrival:EDRK") is None  # corrected
            dep = dc.Finding(fid, ac_id, "", "departure", "", "EBAW", "EBAW")
            assert dc.apply_suggestion(f, dep) and f.departure_icao == "EBAW"
            assert f.notes.endswith("the paper logbook says —.")
            day = dc.Finding(
                fid, ac_id, "", "date", "2025-03-18", "2025-03-19", "2025-03-19"
            )
            assert dc.apply_suggestion(f, day) and f.date == date(2025, 3, 19)
            assert not dc.apply_suggestion(
                f, dc.Finding(fid, ac_id, "", "outside_times", "a", "b", "")
            )
            assert not dc.apply_suggestion(
                f, dc.Finding(fid, ac_id, "", "other", "a", "b", "c")
            )
            f.gps_track_id = None
            db.session.flush()
            db.session.expire(f, ["gps_track"])
            assert dc.find(f, "arrival:EDRK") is None


class TestDataChecksPage:
    def test_list_fix_dismiss(self, client, app):
        uid, tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        fid = _tracked_flight(app, ac_id)
        other = _tracked_flight(app, ac_id, arr="????")
        page = client.get("/config/data-checks").data.decode()
        assert "EDBK" in page and "Use the GPS value" in page
        assert "Changes only the arrival airfield." in page
        assert "Change only the arrival airfield from EDBK to EDRK?" in page
        # The page refreshed the count: bell and warning on the config page.
        config = client.get("/config/").data.decode()
        assert "Data checks to review" in config
        assert "2 differences found between flights and their GPS tracks." in config

        rv = client.post(f"/config/data-checks/{fid}/fix", data={"key": "arrival:EDRK"})
        assert rv.headers["Location"].endswith("/config/data-checks")
        with app.app_context():
            f = db.session.get(Flight, fid)
            assert f.arrival_icao == "EDRK" and "paper logbook says EDBK" in f.notes
            # Only the airfield: the logged times and date are untouched.
            assert (f.date, f.departure_time, f.arrival_time) == (
                date(2025, 3, 18),
                time(9, 0),
                time(11, 0),
            )
            assert f.departure_icao == "EBAW"
        # Again: no longer applies.
        client.post(f"/config/data-checks/{fid}/fix", data={"key": "arrival:EDRK"})
        with client.session_transaction() as sess:
            assert "no longer applies" in str(sess.get("_flashes"))

        client.post(
            f"/config/data-checks/{other}/dismiss", data={"key": "arrival:EDRK"}
        )
        client.post(
            f"/config/data-checks/{other}/dismiss", data={"key": "arrival:EDRK"}
        )
        with app.app_context():
            assert DataCheckDismissal.query.filter_by(flight_id=other).count() == 1
            assert dc.open_count(tid) == 0
        page = client.get("/config/data-checks").data.decode()
        assert "No differences found" in page
        assert "Data checks to review" not in client.get("/config/").data.decode()

    def test_run_checks_tool_rechecks_everything(self, client, app):
        """Data tools' "Run data checks": every aircraft checked anew, the
        cached results ignored, then the Data checks page."""
        from models import User  # pyright: ignore[reportMissingImports]

        uid, tid, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            db.session.get(User, uid).is_instance_admin = True  # Data tools
            db.session.commit()
        _login(client, uid)
        # Listed on the configuration page, even with nothing to review.
        config = client.get("/config/").data.decode()
        assert "Run data checks" in config and "/config/data-checks/run" in config

        rv = client.post("/config/data-checks/run", follow_redirects=True)
        page = rv.data.decode()
        assert "Every flight with a GPS track was checked again." in page
        assert "No differences found" in page

        _tracked_flight(app, ac_id)
        with app.app_context():
            dc.refresh_open_count(tid)
            db.session.commit()
            cache = db.session.get(AppSetting, f"data_checks:aircraft:{ac_id}")
            # A stale cache the flight's fingerprint doesn't see changing.
            cache.value = cache.value.replace("EDRK", "EDRX")
            db.session.commit()
        page = client.post("/config/data-checks/run", follow_redirects=True)
        page = page.data.decode()
        assert "EDRK" in page and "EDRX" not in page
        with app.app_context():
            assert dc.open_count(tid) == 1

    def test_other_tenant_and_no_tenant(self, client, app):
        from models import Aircraft, Tenant  # pyright: ignore[reportMissingImports]

        uid, _tid, _ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            t2 = Tenant(name="Other")
            db.session.add(t2)
            db.session.flush()
            ac2 = Aircraft(tenant_id=t2.id, registration="OO-OTH", make="X", model="Y")
            db.session.add(ac2)
            db.session.commit()
            ac2_id = ac2.id
        fid = _tracked_flight(app, ac2_id)
        _login(client, uid)
        assert (
            client.post(f"/config/data-checks/{fid}/fix", data={"key": "x"}).status_code
            == 404
        )
        assert (
            client.post(
                "/config/data-checks/999999/dismiss", data={"key": "x"}
            ).status_code
            == 404
        )

    def test_instance_admin_without_tenant(self, client, app):
        from models import User  # pyright: ignore[reportMissingImports]

        with app.app_context():
            u = User(
                email="ia@example.com",
                password_hash="x",
                is_active=True,
                is_instance_admin=True,
            )
            db.session.add(u)
            db.session.commit()
            uid = u.id
        _login(client, uid)
        assert "No differences found" in client.get("/config/data-checks").data.decode()
        rv = client.post("/config/data-checks/run", follow_redirects=True)
        assert "No differences found" in rv.data.decode()


class TestDailyRefresh:
    def test_daily_checks_refresh_the_count(self, app, monkeypatch):
        from services import notification_service  # pyright: ignore[reportMissingImports]

        calls = []
        monkeypatch.setattr(
            "services.data_checks.refresh_all_open_counts", lambda: calls.append(1)
        )
        notification_service.run_daily_checks(app)
        assert calls == [1]

    def test_a_failure_does_not_stop_the_other_checks(self, app, monkeypatch):
        from services import notification_service  # pyright: ignore[reportMissingImports]

        def boom():
            raise RuntimeError("x")

        monkeypatch.setattr("services.data_checks.refresh_all_open_counts", boom)
        ran = []
        monkeypatch.setattr(
            notification_service, "_check_maintenance", lambda a: ran.append(1)
        )
        notification_service.run_daily_checks(app)
        assert ran == [1]


class TestPageWording:
    def test_each_kind_says_what_changes(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        with app.app_context():
            track = GpsTrack(
                geojson=_line(EDRK, EBAW),  # departure off as well
                block_off_utc=_t(6, 0, day=19),
                block_on_utc=_t(7, 0, day=19),
            )
            early = GpsTrack(
                geojson=_line(EBAW, EBAW), block_off_utc=_t(6, 0), block_on_utc=_t(7, 0)
            )
            db.session.add_all([track, early])
            db.session.flush()
            for t in (track, early):
                db.session.add(
                    Flight(
                        aircraft_id=ac_id,
                        date=date(2025, 3, 18),
                        departure_icao="EBAW",
                        arrival_icao="EBAW",
                        departure_time=time(9, 0),
                        arrival_time=time(11, 0),
                        gps_track_id=t.id,
                    )
                )
            db.session.commit()
        page = client.get("/config/data-checks").data.decode()
        assert "Changes only the departure airfield." in page
        assert "Changes only the date." in page
        assert "Change only the date from 2025-03-18 to 2025-03-19?" in page
        assert "Nothing to correct automatically" in page
