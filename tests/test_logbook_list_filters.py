"""Filters of the logbook lists (flights/list_filters.py): GPS track with /
without, a date range and the crew — on an aircraft's flight log and on a
pilot's logbook — kept in the list's links and remembered view."""

from datetime import date

from models import (  # pyright: ignore[reportMissingImports]
    Flight,
    GpsTrack,
    LogbookEntryType,
    User,
    db,
)

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)


def _world(app):
    """An aircraft with four flights: before the user's time (someone else
    as PIC), and the user's own — PIC with and without a track, second crew
    — plus a simulator session in the user's logbook."""
    uid, _tid, ac_id = _make_user_and_aircraft(app)
    with app.app_context():
        other = User(email="previous@example.com", password_hash="x", is_active=True)
        db.session.add(other)
        db.session.flush()
        track = GpsTrack(geojson={"type": "Feature", "geometry": None})
        db.session.add(track)
        db.session.flush()
        rows = [
            ("EBAW", "EBOS", date(2024, 1, 10), {"pic_name": "Previous Owner"}),
            ("EBAW", "EBNM", date(2025, 3, 1), {"pic_user_id": uid}),
            (
                "EBNM",
                "EBAW",
                date(2025, 3, 2),
                {"pic_user_id": uid, "gps_track_id": track.id},
            ),
            (
                "EBAW",
                "EBCI",
                date(2025, 4, 1),
                {"pic_user_id": other.id, "second_crew_user_id": uid},
            ),
        ]
        ids = []
        for dep, arr, day, extra in rows:
            f = Flight(
                aircraft_id=ac_id,
                date=day,
                departure_icao=dep,
                arrival_icao=arr,
                **extra,
            )
            db.session.add(f)
            db.session.flush()
            ids.append(f.id)
        sim = Flight(
            date=date(2025, 3, 5),
            entry_type=LogbookEntryType.FSTD,
            pic_user_id=uid,
            fstd_duration=1.0,
        )
        db.session.add(sim)
        db.session.commit()
        return uid, ac_id, ids


def _rows(page, *routes):
    return [r for r in routes if r in page]


ROUTES = ("EBAW → EBOS", "EBAW → EBNM", "EBNM → EBAW", "EBAW → EBCI")


class TestAircraftLog:
    def test_without_a_track_since_a_date(self, client, app):
        uid, ac_id, _ids = _world(app)
        _login(client, uid)
        page = client.get(
            f"/aircraft/{ac_id}/flights?gps=without&from=2025-01-01"
        ).data.decode()
        assert 'name="gps"' in page and 'value="without" selected' in page
        assert "EBNM" in page and "EBCI" in page and "EBOS" not in page
        assert "Clear filters" in page
        # Kept in the remembered list view (back from a flight's page).
        from flights.routes import _FLIGHTS_VIEW_SESSION_KEY, flights_list_url

        with client.session_transaction() as sess:
            view = sess[_FLIGHTS_VIEW_SESSION_KEY]
        assert view["gps"] == "without" and view["from"] == "2025-01-01"
        with app.test_request_context():
            from flask import session

            session[_FLIGHTS_VIEW_SESSION_KEY] = view
            assert "gps=without" in flights_list_url(ac_id)

    def test_with_a_track_mine_and_until(self, client, app):
        uid, ac_id, _ids = _world(app)
        _login(client, uid)
        page = client.get(f"/aircraft/{ac_id}/flights?gps=with&crew=me").data.decode()
        assert "EBNM" in page and "EBCI" not in page and "EBOS" not in page
        page = client.get(f"/aircraft/{ac_id}/flights?to=2024-12-31").data.decode()
        assert "EBOS" in page and "EBCI" not in page
        # Nothing matches: said so, not "no flights logged yet".
        page = client.get(f"/aircraft/{ac_id}/flights?from=2030-01-01").data.decode()
        assert "No flight matches these filters." in page
        # Invalid values are ignored.
        page = client.get(
            f"/aircraft/{ac_id}/flights?gps=maybe&from=x&crew=pic"
        ).data.decode()
        assert "EBOS" in page and "Clear filters" not in page

    def test_filters_carried_in_page_links(self, client, app):
        uid, ac_id, _ids = _world(app)
        _login(client, uid)
        page = client.get(
            f"/aircraft/{ac_id}/flights?per_page=10&gps=without"
        ).data.decode()
        assert "gps=without" in page.split('id="list-filters"')[1]


class TestPilotLogbook:
    def test_without_a_track_leaves_simulator_sessions_out(self, client, app):
        from flights.list_filters import apply_filters

        uid, _ac_id, ids = _world(app)
        with app.app_context():
            mine = Flight.query.filter(
                (Flight.pic_user_id == uid) | (Flight.second_crew_user_id == uid)
            )
            got = {f.id for f in apply_filters(mine, {"gps": "without"}, uid)}
        # Not the tracked flight, nor the simulator session.
        assert got == {ids[1], ids[3]}
        _login(client, uid)
        assert client.get("/pilot/logbook?gps=without").status_code == 200

    def test_as_pic_or_second_crew(self, client, app):
        uid, _ac_id, _ids = _world(app)
        _login(client, uid)
        page = client.get("/pilot/logbook?crew=second").data.decode()
        assert "EBCI" in page and "EBNM" not in page
        page = client.get("/pilot/logbook?crew=pic&gps=with").data.decode()
        assert "EBNM" in page and "EBCI" not in page
        page = client.get("/pilot/logbook?from=2026-01-01").data.decode()
        assert "No flight matches these filters." in page
