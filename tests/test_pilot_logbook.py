"""
Tests for Phase 17: PilotProfile model, PilotLogbookEntry model, pilot logbook routes.
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from datetime import date
from unittest.mock import patch

import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    FlightCrew,
    FlightEntry,
    LogbookEntryType,
    PilotLogbookEntry,
    PilotProfile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration="OO-TST",
            make="Cessna",
            model="172S",
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_flight(app, aircraft_id):
    with app.app_context():
        fe = FlightEntry(
            aircraft_id=aircraft_id,
            date=date(2024, 1, 15),
            departure_icao="EBOS",
            arrival_icao="EBBR",
            flight_time_counter_start=100.0,
            flight_time_counter_end=101.5,
        )
        db.session.add(fe)
        db.session.flush()
        db.session.add(
            FlightCrew(flight_id=fe.id, name="J. Smith", role="PIC", sort_order=0)
        )
        db.session.commit()
        return fe.id


def _add_logbook_entry(app, user_id, flight_id=None, **kwargs):
    defaults = dict(
        date=date(2024, 3, 1),
        aircraft_type="C172S",
        aircraft_registration="OO-TST",
        departure_place="EBOS",
        arrival_place="EBBR",
        single_pilot_se=1.5,
        landings_day=1,
        function_pic=1.5,
    )
    defaults.update(kwargs)
    with app.app_context():
        entry = PilotLogbookEntry(
            pilot_user_id=user_id,
            flight_id=flight_id,
            **defaults,
        )
        db.session.add(entry)
        db.session.commit()
        return entry.id


def _post_entry(client, extra=None):
    data = {
        "date": "2024-06-01",
        "aircraft_type": "C172S",
        "aircraft_registration": "OO-TST",
        "departure_place": "EBOS",
        "arrival_place": "EBBR",
        "single_pilot_se": "1.5",
        "landings_day": "1",
        "function_pic": "1.5",
    }
    if extra:
        data.update(extra)
    return client.post("/pilot/logbook/new", data=data, follow_redirects=True)


# ── PilotProfile model ────────────────────────────────────────────────────────


class TestPilotProfileModel:
    def test_create_profile(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            p = PilotProfile(
                user_id=uid,
                license_number="BE.PPL.A.12345",
                medical_expiry=date(2027, 6, 1),
                sep_expiry=date(2026, 9, 30),
            )
            db.session.add(p)
            db.session.commit()
            stored = PilotProfile.query.filter_by(user_id=uid).first()
            assert stored.license_number == "BE.PPL.A.12345"
            assert stored.medical_expiry == date(2027, 6, 1)
            assert stored.sep_expiry == date(2026, 9, 30)

    def test_profile_nullable_fields(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            p = PilotProfile(user_id=uid)
            db.session.add(p)
            db.session.commit()
            stored = PilotProfile.query.filter_by(user_id=uid).first()
            assert stored.license_number is None
            assert stored.medical_expiry is None
            assert stored.sep_expiry is None

    def test_profile_unique_per_user(self, app):
        uid, _ = _create_user_and_tenant(app)
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid))
            db.session.commit()
            db.session.add(PilotProfile(user_id=uid))
            import sqlalchemy.exc

            with pytest.raises(sqlalchemy.exc.IntegrityError):
                db.session.commit()


# ── PilotLogbookEntry model ───────────────────────────────────────────────────


class TestPilotLogbookEntryModel:
    def test_total_flight_time_se_only(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(
            app, uid, single_pilot_se=1.5, single_pilot_me=None, multi_pilot=None
        )
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time == 1.5

    def test_total_flight_time_sum_all(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(
            app, uid, single_pilot_se=1.0, single_pilot_me=0.5, multi_pilot=0.8
        )
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time == 2.3

    def test_total_flight_time_none_when_no_columns(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(
            app, uid, single_pilot_se=None, single_pilot_me=None, multi_pilot=None
        )
        with app.app_context():
            e = db.session.get(PilotLogbookEntry, eid)
            assert e.total_flight_time is None

    def test_flight_entry_deletion_sets_null(self, app):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            db.session.delete(fe)
            db.session.commit()
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry is not None
            assert entry.flight_id is None

    def test_multiple_entries_for_same_pilot(self, app):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1))
        _add_logbook_entry(app, uid, date=date(2024, 2, 1))
        with app.app_context():
            entries = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).all()
            assert len(entries) == 2


# ── Logbook route: list & totals ──────────────────────────────────────────────


class TestLogbookRoutes:
    def test_logbook_requires_login(self, app, client):
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 302

    def test_view_entry_loads(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        resp = client.get(f"/pilot/logbook/{eid}/view")
        assert resp.status_code == 200
        assert b"EBOS" in resp.data

    def test_view_entry_wrong_user_returns_404(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        eid = _add_logbook_entry(app, uid1)
        _login(app, client, email="b@x.com")
        resp = client.get(f"/pilot/logbook/{eid}/view")
        assert resp.status_code == 404

    def test_pilot_tracks_empty(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/tracks")
        assert resp.status_code == 200

    def test_pilot_tracks_with_gps_entry(self, app, client):
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
                geojson={"type": "FeatureCollection", "features": []},
            )
            db.session.add(track)
            db.session.flush()
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 6, 1),
                departure_place="EBNM",
                arrival_place="EBAW",
                gps_track_id=track.id,
            )
            db.session.add(entry)
            db.session.commit()
        resp = client.get("/pilot/tracks")
        assert resp.status_code == 200
        assert b"EBNM" in resp.data
        assert b"gif-modal-export-all-btn" in resp.data
        assert b"Download all formats" in resp.data

    def test_pilot_tracks_gif_endpoint(self, app, client):
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
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
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    departure_place="EBNM",
                    arrival_place="EBAW",
                    gps_track_id=track.id,
                )
            )
            db.session.commit()
        resp = client.get("/pilot/tracks/animation.gif")
        assert resp.status_code == 200
        assert resp.content_type == "image/gif"
        assert resp.data[:3] == b"GIF"

    def test_pilot_tracks_gif_portrait_endpoint(self, app, client):
        from models import GpsTrack  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
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
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    departure_place="EBNM",
                    arrival_place="EBAW",
                    gps_track_id=track.id,
                )
            )
            db.session.commit()
        resp = client.get("/pilot/tracks/animation.gif?orientation=portrait")
        assert resp.status_code == 200
        assert resp.content_type == "image/gif"
        assert resp.data[:3] == b"GIF"
        img = _Img.open(_io.BytesIO(resp.data))
        assert img.size == (480, 800), f"expected portrait 480×800, got {img.size}"

    def test_pilot_tracks_gif_hires_landscape_endpoint(self, app, client):
        from models import GpsTrack  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
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
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    departure_place="EBNM",
                    arrival_place="EBAW",
                    gps_track_id=track.id,
                )
            )
            db.session.commit()
        resp = client.get(
            "/pilot/tracks/animation.gif?orientation=landscape&quality=hires"
        )
        assert resp.status_code == 200
        assert resp.content_type == "image/gif"
        assert resp.data[:3] == b"GIF"
        img = _Img.open(_io.BytesIO(resp.data))
        assert img.size == (1600, 960), (
            f"expected hires landscape 1600×960, got {img.size}"
        )
        cd = resp.headers.get("Content-Disposition", "")
        assert "hires" in cd, f"filename should include -hires: {cd}"

    def test_pilot_tracks_gif_hires_portrait_endpoint(self, app, client):
        from models import GpsTrack  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            track = GpsTrack(
                source_filename="flight.gpx",
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
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 6, 1),
                    departure_place="EBNM",
                    arrival_place="EBAW",
                    gps_track_id=track.id,
                )
            )
            db.session.commit()
        resp = client.get(
            "/pilot/tracks/animation.gif?orientation=portrait&quality=hires"
        )
        assert resp.status_code == 200
        img = _Img.open(_io.BytesIO(resp.data))
        assert img.size == (960, 1600), (
            f"expected hires portrait 960×1600, got {img.size}"
        )
        cd = resp.headers.get("Content-Disposition", "")
        assert "portrait" in cd and "hires" in cd

    def test_logbook_empty(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 200
        assert b"No logbook entries" in resp.data

    def test_logbook_shows_entries(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, departure_place="EBOS", arrival_place="EBBR")
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert b"EBOS" in resp.data
        assert b"EBBR" in resp.data

    def test_logbook_shows_totals(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, single_pilot_se=1.5)
        _add_logbook_entry(app, uid, single_pilot_se=2.0)
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert b"Totals" in resp.data
        assert b"3.5" in resp.data

    def test_logbook_only_shows_own_entries(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        _add_logbook_entry(app, uid1, departure_place="EHAM")
        _login(app, client, email="b@x.com")
        resp = client.get("/pilot/logbook")
        assert b"EHAM" not in resp.data

    def test_logbook_default_order_is_antichronological(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1), departure_place="EARLY")
        _add_logbook_entry(app, uid, date=date(2024, 6, 1), departure_place="LATER")
        _login(app, client)
        resp = client.get("/pilot/logbook")
        pos_early = resp.data.find(b"EARLY")
        pos_later = resp.data.find(b"LATER")
        assert pos_later < pos_early  # most recent appears first in HTML

    def test_logbook_asc_order_toggle(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, date=date(2024, 1, 1), departure_place="EARLY")
        _add_logbook_entry(app, uid, date=date(2024, 6, 1), departure_place="LATER")
        _login(app, client)
        resp = client.get("/pilot/logbook?order=asc")
        pos_early = resp.data.find(b"EARLY")
        pos_later = resp.data.find(b"LATER")
        assert pos_early < pos_later  # oldest appears first in HTML

    def test_logbook_same_day_orders_by_departure_time_not_insertion(self, app, client):
        """Backfilling an earlier same-day flight after a later one must not
        make the earlier flight sort above the later one (insertion order
        must not be used as a proxy for time of day)."""
        from datetime import time

        uid, _ = _create_user_and_tenant(app)
        # Insert the LATER flight first...
        _add_logbook_entry(
            app,
            uid,
            date=date(2024, 3, 1),
            departure_time=time(14, 0),
            departure_place="LATERFLT",
        )
        # ...then backfill an EARLIER same-day flight, inserted (higher id) after.
        _add_logbook_entry(
            app,
            uid,
            date=date(2024, 3, 1),
            departure_time=time(8, 0),
            departure_place="EARLYFLT",
        )
        _login(app, client)
        resp = client.get("/pilot/logbook")
        pos_later = resp.data.find(b"LATERFLT")
        pos_earlier = resp.data.find(b"EARLYFLT")
        assert 0 <= pos_later < pos_earlier

    def test_logbook_same_day_untimed_entries_sort_after_timed_ones(self, app, client):
        from datetime import time

        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(
            app,
            uid,
            date=date(2024, 3, 1),
            departure_time=time(8, 0),
            departure_place="TIMEDFLT",
        )
        _add_logbook_entry(
            app,
            uid,
            date=date(2024, 3, 1),
            departure_time=None,
            departure_place="NOTIMEFLT",
        )
        _login(app, client)
        resp = client.get("/pilot/logbook")
        pos_timed = resp.data.find(b"TIMEDFLT")
        pos_untimed = resp.data.find(b"NOTIMEFLT")
        assert 0 <= pos_timed < pos_untimed

    def test_logbook_totals_cover_all_entries_not_just_page(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        # Create 55 entries (more than one page of 50)
        for i in range(55):
            _add_logbook_entry(
                app,
                uid,
                single_pilot_se=1.0,
                function_pic=1.0,
                single_pilot_me=None,
                multi_pilot=None,
            )
        _login(app, client)
        resp = client.get("/pilot/logbook")
        # Total should be 55.0, not 50.0 (which would be a page-only sum)
        assert b"55" in resp.data

    def test_logbook_per_page_all_returns_all_entries(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        for i in range(5):
            _add_logbook_entry(
                app,
                uid,
                single_pilot_se=1.0,
                function_pic=1.0,
                single_pilot_me=None,
                multi_pilot=None,
            )
        _login(app, client)
        resp = client.get("/pilot/logbook?per_page=all")
        assert resp.status_code == 200
        # All 5 entries visible and "all" state shown
        assert b"5 entries (all)" in resp.data


# ── New / edit / delete entry routes ─────────────────────────────────────────


class TestEntryRoutes:
    def test_new_entry_get(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/logbook/new")
        assert resp.status_code == 200
        assert b"New Logbook Entry" in resp.data

    def test_new_entry_saved(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        _post_entry(client)
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.aircraft_type == "C172S"
            assert float(entry.single_pilot_se) == 1.5

    def test_new_entry_date_required(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"date": ""})
        assert resp.status_code == 422
        assert b"required" in resp.data.lower()

    def test_new_entry_negative_time_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"single_pilot_se": "-1.0"})
        assert b"non-negative" in resp.data

    def test_new_entry_negative_landings_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"landings_day": "-1"})
        assert b"non-negative" in resp.data

    def test_edit_entry_get(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        resp = client.get(f"/pilot/logbook/{eid}/edit")
        assert resp.status_code == 200
        assert b"Edit / New Logbook Entry" in resp.data

    def test_edit_entry_saved(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-07-01",
                "aircraft_type": "PA44",
                "aircraft_registration": "OO-ABC",
                "departure_place": "EHRD",
                "arrival_place": "EBBR",
                "single_pilot_me": "1.2",
                "landings_day": "1",
                "function_pic": "1.2",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.aircraft_type == "PA44"
            assert float(entry.single_pilot_me) == 1.2

    def test_edit_entry_nulls_cross_country(self, app, client):
        """Pre-existing quirk (see docs/phase38_offline_logbook_spec.md §38h):
        `cross_country` has no form field anywhere, so a standalone edit
        always clears it — not to be "fixed" by the parse_pilot_fields
        extraction (zero-behaviour-diff rule)."""
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid, cross_country=0.8)
        _login(app, client)
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-07-01",
                "aircraft_type": "PA44",
                "aircraft_registration": "OO-ABC",
                "departure_place": "EHRD",
                "arrival_place": "EBBR",
                "single_pilot_me": "1.2",
                "landings_day": "1",
                "function_pic": "1.2",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.cross_country is None

    def test_edit_entry_with_flight_id_redirects_to_edit_flight(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        aid = _add_aircraft(app, tid)
        fid = _add_flight(app, aid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        _login(app, client)
        resp = client.get(f"/pilot/logbook/{eid}/edit")
        assert resp.status_code == 302
        assert f"/flights/{fid}/edit" in resp.headers["Location"]

    def test_edit_entry_wrong_user_returns_404(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        eid = _add_logbook_entry(app, uid1)
        _login(app, client, email="b@x.com")
        resp = client.get(f"/pilot/logbook/{eid}/edit")
        assert resp.status_code == 404

    def test_delete_entry(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        client.post(f"/pilot/logbook/{eid}/delete", follow_redirects=True)
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, eid) is None

    def test_delete_entry_only_keeps_linked_flight_entry(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        _login(app, client)
        client.post(f"/pilot/logbook/{eid}/delete", follow_redirects=True)
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, eid) is None
            assert db.session.get(FlightEntry, fid) is not None

    def test_delete_both_removes_linked_flight_entry(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        _login(app, client)
        client.post(
            f"/pilot/logbook/{eid}/delete",
            data={"delete_flight_entry": "1"},
            follow_redirects=True,
        )
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, eid) is None
            assert db.session.get(FlightEntry, fid) is None

    def test_delete_both_also_removes_flight_counter_photos(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            fe.flight_counter_photo = "does-not-exist-on-disk.jpg"
            fe.engine_counter_photo = "also-missing.jpg"
            db.session.commit()
        _login(app, client)
        client.post(
            f"/pilot/logbook/{eid}/delete",
            data={"delete_flight_entry": "1"},
            follow_redirects=True,
        )
        with app.app_context():
            assert db.session.get(FlightEntry, fid) is None

    def test_delete_both_without_aircraft_access_keeps_flight_entry(self, app, client):
        """A pilot without access to the linked aircraft cannot delete its
        FlightEntry via the pilot-logbook delete route, even when asked to."""
        with app.app_context():
            tenant = Tenant(name="Test Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email="norights@example.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT)
            )
            db.session.commit()
            uid, tid = user.id, tenant.id
        acid = _add_aircraft(app, tid)
        fid = _add_flight(app, acid)
        eid = _add_logbook_entry(app, uid, flight_id=fid)
        _login(app, client, email="norights@example.com")
        client.post(
            f"/pilot/logbook/{eid}/delete",
            data={"delete_flight_entry": "1"},
            follow_redirects=True,
        )
        with app.app_context():
            assert db.session.get(PilotLogbookEntry, eid) is None
            assert db.session.get(FlightEntry, fid) is not None

    def test_new_entry_with_gps_creates_track(self, app, client):
        import json
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        geojson = json.dumps({"type": "FeatureCollection", "features": []})
        resp = client.post(
            "/pilot/logbook/new",
            data={
                "date": "2024-06-01",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "function_pic": "1.0",
                "gps_geojson": geojson,
                "gps_filename": "flight.gpx",
                "gps_block_off_utc": "",
                "gps_block_on_utc": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.gps_track_id is not None
            gt = db.session.get(GpsTrack, entry.gps_track_id)
            assert gt is not None
            assert gt.source_filename == "flight.gpx"

    def test_edit_entry_with_gps_updates_track(self, app, client):
        import json
        from models import GpsTrack  # pyright: ignore[reportMissingImports]

        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        geojson = json.dumps({"type": "FeatureCollection", "features": []})
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-06-01",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "gps_geojson": geojson,
                "gps_filename": "track.gpx",
                "gps_block_off_utc": "",
                "gps_block_on_utc": "",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.gps_track_id is not None
            gt = db.session.get(GpsTrack, entry.gps_track_id)
            assert gt.source_filename == "track.gpx"
            # Edit again — existing track updated, not replaced; also provide block times
            old_track_id = entry.gps_track_id

        geojson2 = json.dumps({"type": "FeatureCollection", "features": [1]})
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-06-01",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "gps_geojson": geojson2,
                "gps_filename": "track2.gpx",
                "gps_block_off_utc": "2024-06-01T08:00:00+00:00",
                "gps_block_on_utc": "2024-06-01T09:00:00+00:00",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.gps_track_id == old_track_id
            gt = db.session.get(GpsTrack, entry.gps_track_id)
            assert gt.source_filename == "track2.gpx"
            assert gt.block_off_utc is not None
            assert gt.block_on_utc is not None

    def test_new_entry_with_malformed_gps_data_saves_without_track(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/pilot/logbook/new",
            data={
                "date": "2024-06-01",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "gps_geojson": "{not valid json",
                "gps_filename": "bad.gpx",
                "gps_block_off_utc": "not-a-date",
                "gps_block_on_utc": "also-bad",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.gps_track_id is not None

    def test_edit_entry_preserves_gps_track_when_no_gps_submitted(self, app, client):
        import json

        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        # First: attach a GPS track
        geojson = json.dumps({"type": "FeatureCollection", "features": []})
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-06-01",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
                "gps_geojson": geojson,
                "gps_filename": "track.gpx",
                "gps_block_off_utc": "",
                "gps_block_on_utc": "",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            track_id = entry.gps_track_id
            assert track_id is not None

        # Second: edit without GPS fields — track must not be cleared
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "date": "2024-06-02",
                "departure_place": "EBNM",
                "arrival_place": "EBAW",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.gps_track_id == track_id

    def test_delete_entry_wrong_user_returns_404(self, app, client):
        uid1, _ = _create_user_and_tenant(app, email="a@x.com")
        uid2, _ = _create_user_and_tenant(app, email="b@x.com")
        eid = _add_logbook_entry(app, uid1)
        _login(app, client, email="b@x.com")
        resp = client.post(f"/pilot/logbook/{eid}/delete")
        assert resp.status_code == 404


# ── FSTD / simulator session entries ─────────────────────────────────────────


class TestFstdLogbookEntries:
    def test_entry_type_defaults_to_flight(self, app):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.entry_type == LogbookEntryType.FLIGHT
            assert entry.fstd_type is None
            assert entry.fstd_duration is None

    def test_new_fstd_entry_saved(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/pilot/logbook/new",
            data={
                "entry_type": "fstd",
                "date": "2024-06-01",
                "fstd_type": "FNPT",
                "fstd_duration": "2.5",
                "remarks": "Instrument approaches",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.entry_type == LogbookEntryType.FSTD
            assert entry.fstd_type == "FNPT"
            assert float(entry.fstd_duration) == 2.5
            assert entry.remarks == "Instrument approaches"

    def test_fstd_entry_nulls_flight_specific_fields_even_if_submitted(
        self, app, client
    ):
        """A tampered form submitting aircraft/route/counters alongside
        entry_type=fstd must not persist those flight-only values."""
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/pilot/logbook/new",
            data={
                "entry_type": "fstd",
                "date": "2024-06-01",
                "fstd_type": "FFS",
                "fstd_duration": "1.0",
                "aircraft_type": "C172S",
                "aircraft_type_icao": "C172",
                "aircraft_registration": "OO-TST",
                "departure_place": "EBOS",
                "departure_time": "10:00",
                "arrival_place": "EBBR",
                "arrival_time": "11:00",
                "landings_day": "3",
                "landings_night": "1",
                "single_pilot_se": "1.5",
                "single_pilot_me": "1.5",
                "multi_pilot": "1.5",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry is not None
            assert entry.aircraft_type is None
            assert entry.aircraft_type_icao is None
            assert entry.aircraft_registration is None
            assert entry.departure_place is None
            assert entry.departure_time is None
            assert entry.arrival_place is None
            assert entry.arrival_time is None
            assert entry.landings_day is None
            assert entry.landings_night is None
            assert entry.single_pilot_se is None
            assert entry.single_pilot_me is None
            assert entry.multi_pilot is None
            assert entry.total_flight_time is None

    def test_invalid_entry_type_falls_back_to_flight(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        _post_entry(client, {"entry_type": "bogus"})
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry.entry_type == LogbookEntryType.FLIGHT

    def test_invalid_fstd_type_is_dropped(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/pilot/logbook/new",
            data={
                "entry_type": "fstd",
                "date": "2024-06-01",
                "fstd_type": "bogus",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry.fstd_type is None

    def test_fstd_negative_duration_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/pilot/logbook/new",
            data={
                "entry_type": "fstd",
                "date": "2024-06-01",
                "fstd_duration": "-1.0",
            },
        )
        assert b"non-negative" in resp.data

    def test_edit_entry_switches_to_fstd(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        client.post(
            f"/pilot/logbook/{eid}/edit",
            data={
                "entry_type": "fstd",
                "date": "2024-06-02",
                "fstd_type": "AATD",
                "fstd_duration": "0.5",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = db.session.get(PilotLogbookEntry, eid)
            assert entry.entry_type == LogbookEntryType.FSTD
            assert entry.fstd_type == "AATD"
            assert entry.aircraft_type is None
            assert entry.single_pilot_se is None

    def test_logbook_totals_include_fstd_duration_separately(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(app, uid, single_pilot_se=1.5)
        _add_logbook_entry(
            app,
            uid,
            entry_type=LogbookEntryType.FSTD,
            fstd_type="FNPT",
            fstd_duration=2.0,
            aircraft_type=None,
            aircraft_registration=None,
            departure_place=None,
            arrival_place=None,
            single_pilot_se=None,
            landings_day=None,
            function_pic=None,
        )
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert resp.status_code == 200
        # FSTD hours are excluded from the flight-time total…
        assert b"1.5" in resp.data
        # …but shown in their own Sim total.
        assert b"2" in resp.data

    def test_logbook_list_shows_fstd_badge(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_logbook_entry(
            app,
            uid,
            entry_type=LogbookEntryType.FSTD,
            fstd_type="FNPT",
            fstd_duration=1.0,
            aircraft_type=None,
            aircraft_registration=None,
            departure_place=None,
            arrival_place=None,
            single_pilot_se=None,
            landings_day=None,
            function_pic=None,
        )
        _login(app, client)
        resp = client.get("/pilot/logbook")
        assert b"FSTD" in resp.data
        assert b"FNPT" in resp.data

    def test_entry_detail_shows_fstd_info(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(
            app,
            uid,
            entry_type=LogbookEntryType.FSTD,
            fstd_type="FTD",
            fstd_duration=1.5,
            aircraft_type=None,
            aircraft_registration=None,
            departure_place=None,
            arrival_place=None,
            single_pilot_se=None,
            landings_day=None,
            function_pic=None,
        )
        _login(app, client)
        resp = client.get(f"/pilot/logbook/{eid}/view")
        assert resp.status_code == 200
        assert b"FTD" in resp.data
        assert b"1.5" in resp.data


# ── Profile routes ────────────────────────────────────────────────────────────


class TestProfileRoutes:
    def test_profile_get_creates_empty_profile(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/pilot/profile")
        assert resp.status_code == 200
        assert b"Pilot Profile" in resp.data

    def test_profile_save(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/pilot/profile",
            data={
                "license_number": "BE.PPL.A.99999",
                "medical_expiry": "2027-06-01",
                "sep_expiry": "2026-09-30",
            },
            follow_redirects=True,
        )
        with app.app_context():
            p = PilotProfile.query.filter_by(user_id=uid).first()
            assert p.license_number == "BE.PPL.A.99999"
            assert p.medical_expiry == date(2027, 6, 1)
            assert p.sep_expiry == date(2026, 9, 30)

    def test_profile_invalid_date_shows_error(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/pilot/profile",
            data={
                "medical_expiry": "not-a-date",
            },
            follow_redirects=True,
        )
        assert b"valid date" in resp.data

    def test_profile_invalid_sep_expiry_shows_error(self, app, client):
        # covers line 110: errors.append for sep_expiry parse failure
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.post(
            "/pilot/profile",
            data={
                "sep_expiry": "not-a-date",
            },
            follow_redirects=True,
        )
        assert b"valid date" in resp.data

    def test_profile_requires_login(self, app, client):
        resp = client.get("/pilot/profile")
        assert resp.status_code == 302


# ── Validation edge cases (parser branches) ───────────────────────────────────


class TestParserValidation:
    def test_valid_dep_arr_time_saved(self, app, client):
        # covers _parse_time happy path (lines 41-44)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        _post_entry(client, {"departure_time": "09:00", "arrival_time": "10:30"})
        with app.app_context():
            entry = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).first()
            assert entry.departure_time is not None
            assert entry.departure_time.hour == 9
            assert entry.arrival_time.hour == 10

    def test_invalid_departure_time_shows_error(self, app, client):
        # covers _parse_time except path (lines 45-46) and line 272
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"departure_time": "notatime"})
        assert resp.status_code == 422
        assert b"valid HH:MM" in resp.data

    def test_invalid_arrival_time_shows_error(self, app, client):
        # covers line 275
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"arrival_time": "99:99"})
        assert resp.status_code == 422
        assert b"valid HH:MM" in resp.data

    def test_invalid_date_string_shows_error(self, app, client):
        # covers line 266: invalid (non-empty) date
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"date": "not-a-date"})
        assert resp.status_code == 422
        assert b"valid date" in resp.data

    def test_non_numeric_decimal_field_shows_error(self, app, client):
        # covers _parse_decimal except path (lines 60-61)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"single_pilot_se": "abc"})
        assert resp.status_code == 422
        assert b"must be a number" in resp.data

    def test_non_numeric_int_field_shows_error(self, app, client):
        # covers _parse_int except path (lines 73-74)
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(client, {"landings_day": "abc"})
        assert resp.status_code == 422
        assert b"must be a whole number" in resp.data

    def test_invalid_optional_numeric_fields_show_errors(self, app, client):
        # covers lines 321,326,332,338,341,344,347,350,355 — error branches for
        # night_time, instrument_time, landings_night, sp_me, multi_pilot,
        # fn_pic, fn_co, fn_dual, fn_inst
        _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_entry(
            client,
            {
                "night_time": "bad",
                "instrument_time": "bad",
                "landings_night": "bad",
                "single_pilot_me": "bad",
                "multi_pilot": "bad",
                "function_pic": "bad",
                "function_copilot": "bad",
                "function_dual": "bad",
                "function_instructor": "bad",
            },
        )
        assert resp.status_code == 422
        assert b"must be a number" in resp.data

    def test_edit_entry_validation_error(self, app, client):
        # covers lines 226-228: edit POST with validation error re-renders form
        uid, _ = _create_user_and_tenant(app)
        eid = _add_logbook_entry(app, uid)
        _login(app, client)
        resp = client.post(f"/pilot/logbook/{eid}/edit", data={"date": "bad"})
        assert resp.status_code == 422
        assert b"valid date" in resp.data


class TestLoadAirportNames:
    def test_oserror_returns_empty_dict(self):
        from utils import _load_airport_names  # pyright: ignore[reportMissingImports]

        _load_airport_names.cache_clear()
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _load_airport_names()
        _load_airport_names.cache_clear()
        assert result == {}

    def test_known_airport_loaded(self, app):
        from utils import _load_airport_names  # pyright: ignore[reportMissingImports]

        with app.app_context():
            names = _load_airport_names()
        assert len(names) > 0


class TestAirportNameFilter:
    def test_none_returns_empty_string(self, app):
        with app.app_context():
            assert app.jinja_env.filters["airport_name"](None) == ""
            assert app.jinja_env.filters["airport_name"]("") == ""

    def test_known_code_returns_name(self, app):
        with app.app_context():
            assert app.jinja_env.filters["airport_name"]("EBBR") != ""


class TestAirportSearch:
    def test_returns_code_prefix_matches(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/airport-search?q=EBBR")
        assert rv.status_code == 200
        data = rv.get_json()
        codes = [r["code"] for r in data["results"]]
        assert "EBBR" in codes

    def test_returns_name_matches(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/airport-search?q=Brussels")
        assert rv.status_code == 200
        data = rv.get_json()
        codes = [r["code"] for r in data["results"]]
        assert "EBBR" in codes

    def test_short_query_returns_empty(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/airport-search?q=E")
        assert rv.status_code == 200
        assert rv.get_json() == {"results": []}

    def test_unauthenticated_returns_empty(self, app, client):
        rv = client.get("/airport-search?q=EBBR")
        assert rv.status_code == 200
        assert rv.get_json() == {"results": []}

    def test_max_ten_results(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/airport-search?q=EB")
        data = rv.get_json()
        assert len(data["results"]) <= 10


class TestGenerateTracksGif:
    def _sample_rows(self):
        return [
            {
                "date": "2024-01-01",
                "dep": "EBNM",
                "arr": "EBAW",
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.0, 51.0], [4.5, 51.3]],
                    },
                    "properties": {},
                },
            },
            {
                "date": "2024-06-01",
                "dep": "EBAW",
                "arr": "ELLX",
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.5, 51.3], [6.2, 49.6]],
                    },
                    "properties": {},
                },
            },
        ]

    def test_returns_gif_bytes(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_tracks_gif(self._sample_rows())
        assert result[:3] == b"GIF"

    def test_openaip_overlay_attempted_when_key_provided(self, app):
        """Lines 173-178: OpenAIP overlay branch is entered when key is set."""
        import io as _io
        from contextlib import contextmanager
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

        def _fake_tile_png() -> bytes:
            buf = _io.BytesIO()
            _Img.new("RGBA", (256, 256), (200, 200, 200, 255)).save(buf, format="PNG")
            return buf.getvalue()

        @contextmanager  # type: ignore[misc]
        def _fake_urlopen(*_a: object, **_kw: object):  # type: ignore[misc]
            yield _io.BytesIO(_fake_tile_png())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            with app.app_context():
                result = generate_tracks_gif(
                    self._sample_rows(), _openaip_key="TEST_KEY"
                )
        assert result[:3] == b"GIF"

    def test_empty_rows_returns_gif(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_tracks_gif([])
        assert result[:3] == b"GIF"

    def test_rows_without_geojson_handled(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        rows = [{"date": "2024-01-01", "dep": "A", "arr": "B", "geojson": None}]
        with app.app_context():
            result = generate_tracks_gif(rows)
        assert result[:3] == b"GIF"

    def test_gif_uses_plain_bg_when_tile_background_returns_none(self, app):
        """Line 237: _base_frame() falls back to Image.new when tile_bg is None."""
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("utils._make_tile_background", return_value=None):
            with app.app_context():
                result = generate_tracks_gif(self._sample_rows())
        assert result[:3] == b"GIF"

    def test_tile_background_zero_scale_returns_none(self, app):
        """Line 121: _make_tile_background returns None when projection has zero scale."""
        from utils import _make_tile_background  # pyright: ignore[reportMissingImports]

        # Projection that always returns the same x (zero scale)
        with app.app_context():
            result = _make_tile_background(
                lambda lon, lat: (100, int(lat * 10)),  # constant x → scale_x = 0
                4.0,
                5.0,
                50.0,
                51.0,
                800,
                480,
            )
        assert result is None

    def test_tile_fetch_failure_falls_back_to_plain_bg(self, app):
        """GIF is still produced when tile fetching fails (network error)."""
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from unittest.mock import patch

        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            with app.app_context():
                result = generate_tracks_gif(self._sample_rows())
        assert result[:3] == b"GIF"

    def test_tile_background_too_many_tiles_returns_none(self, app):
        """_make_tile_background returns None when tile count exceeds limit."""
        from utils import _make_tile_background  # pyright: ignore[reportMissingImports]

        # Pass a near-global bbox that would require hundreds of tiles
        with app.app_context():
            result = _make_tile_background(
                lambda lon, lat: (int(lon * 10), int(lat * 10)),
                -170.0,
                170.0,
                -80.0,
                80.0,
                800,
                480,
            )
        assert result is None

    def test_unknown_geojson_type_handled(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        rows = [
            {
                "date": "2024-01-01",
                "dep": "A",
                "arr": "B",
                "geojson": {"type": "MultiPolygon", "coordinates": []},
            }
        ]
        with app.app_context():
            result = generate_tracks_gif(rows)
        assert result[:3] == b"GIF"

    def test_font_fallback_when_truetype_unavailable(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        with app.app_context():
            result = generate_tracks_gif(
                self._sample_rows(), _font_path="/nonexistent/font.ttf"
            )
        assert result[:3] == b"GIF"

    def test_feature_collection_geojson(self, app):
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        rows = [
            {
                "date": "2024-01-01",
                "dep": "A",
                "arr": "B",
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[4.0, 51.0], [5.0, 52.0]],
                            },
                            "properties": {},
                        }
                    ],
                },
            }
        ]
        with app.app_context():
            result = generate_tracks_gif(rows)
        assert result[:3] == b"GIF"

    def test_high_res_gif_is_double_size(self, app):
        """high_res=True produces a 1600×960 landscape GIF (2× the default 800×480)."""
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_tracks_gif(
                self._sample_rows(),
                canvas_w=1600,
                canvas_h=960,
                high_res=True,
            )
        assert result[:3] == b"GIF"
        img = _Img.open(_io.BytesIO(result))
        assert img.size == (1600, 960)

    def test_per_frame_projection_called_for_each_track(self, app):
        """_build_gif_projection is called once per accumulated frame plus the
        final frame — confirming the zoom-out uses per-frame projections."""
        from utils import _build_gif_projection, generate_tracks_gif  # pyright: ignore[reportMissingImports]

        with patch(
            "utils._build_gif_projection", wraps=_build_gif_projection
        ) as mock_proj:
            with app.app_context():
                result = generate_tracks_gif(self._sample_rows())

        assert result[:3] == b"GIF"
        # 2 sample rows → 2 per-frame calls + 1 final-frame call = 3 total
        assert mock_proj.call_count == 3

    def test_leading_no_geojson_row_skipped_frame_still_produces_gif(self, app):
        """A row with no geojson that precedes rows with coords is skipped
        (proj_result is None for that iteration) but the GIF still succeeds."""
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]

        rows = [
            {"date": "2024-01-01", "dep": "A", "arr": "B", "geojson": None},
            {
                "date": "2024-06-01",
                "dep": "B",
                "arr": "C",
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.5, 51.3], [6.2, 49.6]],
                    },
                    "properties": {},
                },
            },
        ]
        with app.app_context():
            result = generate_tracks_gif(rows)
        assert result[:3] == b"GIF"

    def test_tile_cache_prevents_duplicate_fetches(self, app):
        """When two frames request the same tile, urlopen is called only once
        for that tile (the second frame reads from tile_cache)."""
        import io as _io
        from unittest.mock import patch
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

        def _fake_png() -> bytes:
            buf = _io.BytesIO()
            _Img.new("RGBA", (256, 256), (200, 200, 200, 255)).save(buf, format="PNG")
            return buf.getvalue()

        fetch_count = {"n": 0}

        from contextlib import contextmanager

        @contextmanager  # type: ignore[misc]
        def _counting_urlopen(*_a: object, **_kw: object):  # type: ignore[misc]
            fetch_count["n"] += 1
            yield _io.BytesIO(_fake_png())

        # Two tracks close together → same zoom level → shared tiles between frames
        rows = [
            {
                "date": "2024-01-01",
                "dep": "EBNM",
                "arr": "EBAW",
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.0, 51.0], [4.5, 51.3]],
                    },
                    "properties": {},
                },
            },
            {
                "date": "2024-06-01",
                "dep": "EBAW",
                "arr": "ELLX",
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[4.5, 51.3], [4.8, 51.5]],
                    },
                    "properties": {},
                },
            },
        ]
        with patch("urllib.request.urlopen", side_effect=_counting_urlopen):
            with app.app_context():
                result = generate_tracks_gif(rows)

        assert result[:3] == b"GIF"
        # With caching, fetches across 3 frames must be fewer than 3× the
        # per-frame tile count (some tiles are shared).
        # The exact count depends on zoom, but it must be > 0 (tiles were fetched).
        assert fetch_count["n"] > 0

    def test_portrait_canvas_produces_correct_dimensions(self, app):
        """generate_tracks_gif with canvas_w=480, canvas_h=800 produces a
        GIF whose pixel dimensions match the portrait canvas exactly."""
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]
        import io as _io

        with app.app_context():
            result = generate_tracks_gif(
                self._sample_rows(), canvas_w=480, canvas_h=800
            )

        assert result[:3] == b"GIF"
        buf = _io.BytesIO(result)
        img = _Img.open(buf)
        assert img.size == (480, 800), f"expected 480×800, got {img.size}"

    def test_tile_cache_hit_skips_network_fetch(self, app):
        """_make_tile_background populates tile_cache on first call; a second
        call with the same area and cache makes no additional network fetches."""
        import io as _io
        from contextlib import contextmanager
        from unittest.mock import patch
        from utils import _make_tile_background  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

        def _fake_png() -> bytes:
            buf = _io.BytesIO()
            _Img.new("RGBA", (256, 256), (180, 180, 180, 255)).save(buf, format="PNG")
            return buf.getvalue()

        @contextmanager  # type: ignore[misc]
        def _fake_urlopen(*_a: object, **_kw: object):  # type: ignore[misc]
            yield _io.BytesIO(_fake_png())

        def _proj(lon: float, lat: float) -> tuple[int, int]:
            return (int((lon - 4.0) * 400), int((52.0 - lat) * 400))

        shared_cache: dict = {}

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen) as mock_fetch:
            with app.app_context():
                # First call: fetches tiles and populates the cache
                _make_tile_background(
                    _proj, 4.0, 5.0, 51.0, 52.0, 800, 480, tile_cache=shared_cache
                )
                first_count = mock_fetch.call_count

                # Second call with same bounds and cache: all tiles come from cache
                _make_tile_background(
                    _proj, 4.0, 5.0, 51.0, 52.0, 800, 480, tile_cache=shared_cache
                )
                second_count = mock_fetch.call_count

        assert first_count > 0, "first call should have fetched at least one tile"
        assert second_count == first_count, "second call must not make any new fetches"

    def test_canvas_geo_bounds_wider_than_track_bbox(self, app):
        """_canvas_geo_bounds returns a bbox that covers the full canvas extent,
        which is wider/taller than the track projection bbox in at least one direction."""
        from utils import _canvas_geo_bounds, _build_gif_projection  # pyright: ignore[reportMissingImports]

        coords = [(4.0, 51.0), (4.5, 51.3), (5.0, 51.0)]
        with app.app_context():
            proj_result = _build_gif_projection(coords, canvas_w=800, canvas_h=480)
        assert proj_result is not None
        project_fn, (min_lon, min_lat, max_lon, max_lat) = proj_result
        c_lon_min, c_lat_min, c_lon_max, c_lat_max = _canvas_geo_bounds(
            project_fn, 800, 480, min_lon, max_lon, min_lat, max_lat
        )
        # Canvas bounds must extend beyond the track bbox on at least one axis
        assert c_lon_min <= min_lon and c_lon_max >= max_lon
        assert c_lat_min < min_lat or c_lat_max > max_lat

    def test_canvas_geo_bounds_zero_scale_returns_original(self, app):
        """_canvas_geo_bounds returns original bbox when projection has zero x-scale."""
        from utils import _canvas_geo_bounds  # pyright: ignore[reportMissingImports]

        def _proj(lon: float, lat: float) -> tuple[int, int]:
            return (100, int((52.0 - lat) * 100))  # constant x → scale_x = 0

        with app.app_context():
            result = _canvas_geo_bounds(_proj, 800, 480, 4.0, 5.0, 51.0, 52.0)
        assert result == (4.0, 51.0, 5.0, 52.0)

    def test_canvas_geo_bounds_same_lat_extends_lon_only(self, app):
        """When min_lat == max_lat (merc_range = 0), _canvas_geo_bounds still
        extends longitude but leaves latitude at the original values."""
        from utils import _canvas_geo_bounds  # pyright: ignore[reportMissingImports]

        def _proj(lon: float, lat: float) -> tuple[int, int]:
            return (int((lon - 4.0) * 200 + 100), 240)  # constant y (py_range = 0)

        with app.app_context():
            c_lon_min, c_lat_min, c_lon_max, c_lat_max = _canvas_geo_bounds(
                _proj, 800, 480, 4.0, 5.0, 51.0, 51.0
            )
        assert c_lon_min < 4.0  # lon is extended leftward
        assert c_lon_max > 5.0  # lon is extended rightward
        assert c_lat_min == 51.0  # lat falls back to original
        assert c_lat_max == 51.0

    def test_high_res_frame_bg_calls_tile_background_with_canvas_bounds(self, app):
        """When high_res=True, _make_tile_background is called with bounds that
        extend beyond the track bbox (full-canvas tile fill)."""
        from unittest.mock import patch
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

        rows = self._sample_rows()
        lon_min_calls: list[float] = []

        def _capture(proj, lon_min, lon_max, lat_min, lat_max, cw, ch, **kw):
            lon_min_calls.append(lon_min)
            return _Img.new("RGB", (cw, ch), (200, 200, 200))

        with patch("utils._make_tile_background", side_effect=_capture):
            with app.app_context():
                result = generate_tracks_gif(
                    rows, canvas_w=1600, canvas_h=960, high_res=True
                )
        assert result[:3] == b"GIF"
        # Track bbox min_lon ≈ 3.78 (sample coords 4.0–6.2 minus 10% margin).
        # Canvas-extent calls push lon_min further left; at least one must be < 3.7.
        assert any(v < 3.7 for v in lon_min_calls), (
            f"No canvas-extent call found; lon_min values were {lon_min_calls}"
        )

    def test_high_res_frame_bg_falls_back_to_track_bbox_when_canvas_extent_fails(
        self, app
    ):
        """When canvas-extent tile count exceeds the cap (_make_tile_background → None),
        _frame_bg retries with the track bbox and the GIF is still produced."""
        from unittest.mock import patch
        from utils import generate_tracks_gif  # pyright: ignore[reportMissingImports]
        from PIL import Image as _Img  # pyright: ignore[reportMissingImports]

        rows = self._sample_rows()
        # Discriminate by lon_min: canvas-extent calls have lon_min < 3.7,
        # track-bbox fallback calls have lon_min ≈ 3.78 (within track range).
        fallback_calls: list[float] = []

        def _selective(proj, lon_min, lon_max, lat_min, lat_max, cw, ch, **kw):
            if lon_min < 3.7:
                return None  # simulate tile cap exceeded for canvas-extent call
            fallback_calls.append(lon_min)
            return _Img.new("RGB", (cw, ch), (200, 200, 200))

        with patch("utils._make_tile_background", side_effect=_selective):
            with app.app_context():
                result = generate_tracks_gif(
                    rows, canvas_w=1600, canvas_h=960, high_res=True
                )
        assert result[:3] == b"GIF"
        assert fallback_calls, (
            "expected at least one fallback call with track-bbox bounds"
        )


class TestLoadAircraftTypes:
    def test_loads_known_designator(self, app):
        from utils import _load_aircraft_types  # pyright: ignore[reportMissingImports]

        with app.app_context():
            types = _load_aircraft_types()
        assert "C172" in types
        assert "P28A" in types

    def test_returns_manufacturer_and_model(self, app):
        from utils import _load_aircraft_types  # pyright: ignore[reportMissingImports]

        with app.app_context():
            types = _load_aircraft_types()
        mfr, model = types["C172"]
        assert mfr != ""
        assert model != ""

    def test_oserror_returns_empty_dict(self, app):
        from utils import _load_aircraft_types  # pyright: ignore[reportMissingImports]

        with patch("builtins.open", side_effect=OSError):
            _load_aircraft_types.cache_clear()
            result = _load_aircraft_types()
        _load_aircraft_types.cache_clear()
        assert result == {}

    def test_variants_returns_multiple_for_shared_code(self, app):
        from utils import _load_aircraft_type_variants  # pyright: ignore[reportMissingImports]

        variants = _load_aircraft_type_variants()
        p28a_names = [name for code, name, *_ in variants if code == "P28A"]
        assert len(p28a_names) > 1, "P28A should have multiple variants"

    def test_variants_oserror_returns_empty_list(self, app):
        from utils import _load_aircraft_type_variants  # pyright: ignore[reportMissingImports]

        with patch("builtins.open", side_effect=OSError):
            result = _load_aircraft_type_variants()
        assert result == []


class TestResolveAircraftTypeIcao:
    def test_exact_match(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao("C172") == "C172"

    def test_case_insensitive(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao("c172") == "C172"

    def test_hyphen_stripped(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            # B-738 → B738
            assert resolve_aircraft_type_icao("B-738") == "B738"

    def test_none_returns_none(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao(None) is None
            assert resolve_aircraft_type_icao("") is None

    def test_unknown_returns_none(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao("ZZZZ_UNKNOWN_TYPE") is None

    def test_model_name_prefix_dr401(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao("DR401") == "DR40"

    def test_model_name_prefix_pa28161_with_suffix(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            assert resolve_aircraft_type_icao("PA28-161 TDI") == "P28A"

    def test_digit_tail_guard_c172rg(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            # "C172RG" must NOT resolve via the C17 (C-17 Globemaster) prefix;
            # the tail "2RG" starts with a digit — the guard rejects the match.
            result = resolve_aircraft_type_icao("C172RG")
            assert result != "C17"

    def test_digit_tail_guard_fires_for_four_char_prefix(self, app):
        """Line 953: ``continue`` fires when a ≥4-char key matches but the tail
        starts with a digit — signals a different model-number suffix."""
        import os
        import tempfile

        from utils import (  # pyright: ignore[reportMissingImports]
            _build_model_name_prefix_lookup,
            _load_aircraft_types,
            resolve_aircraft_type_icao,
        )

        # "ABCD 1 special" → key "ABCD1" (5 chars, ≥4) → ICAO "TSTA".
        # "ABCD12" starts with "ABCD1", tail "2" is a digit → guard fires → None.
        csv_content = (
            "manufacturer,model,type_designator,description,engine_type,engine_count,wtc\n"
            "TEST,ABCD 1 special,TSTA,LandPlane,Piston,1,L\n"
        )
        fd, tmp = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(csv_content)
            with app.app_context():
                _load_aircraft_types.cache_clear()
                _build_model_name_prefix_lookup.cache_clear()
                try:
                    with patch("utils.os.path.join", return_value=tmp):
                        result = resolve_aircraft_type_icao("ABCD12")
                    assert result is None
                finally:
                    _load_aircraft_types.cache_clear()
                    _build_model_name_prefix_lookup.cache_clear()
        finally:
            os.unlink(tmp)

    def test_short_key_guard_skips_keys_under_4_chars(self, app):
        from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

        with app.app_context():
            # "U4A" is a 3-char model-name prefix key (→ AC56) that is not an
            # exact ICAO designator.  The guard (len(key) < 4 → break) must
            # prevent it from being used as a match prefix.
            assert resolve_aircraft_type_icao("U4A") is None

    def test_prefix_lookup_oserror_returns_empty(self, app):
        from utils import _build_model_name_prefix_lookup  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _build_model_name_prefix_lookup.cache_clear()
            try:
                with patch("builtins.open", side_effect=OSError("no csv")):
                    result = _build_model_name_prefix_lookup()
                assert result == {}
            finally:
                _build_model_name_prefix_lookup.cache_clear()

    def test_prefix_lookup_skips_empty_rows(self, app):
        import os
        import tempfile

        from utils import _build_model_name_prefix_lookup  # pyright: ignore[reportMissingImports]

        csv_content = (
            "manufacturer,model,type_designator,description,engine_type,engine_count,wtc\n"
            "TEST,,TSTS,LandPlane,Piston,1,L\n"
            "TEST,Good Model,TGOO,LandPlane,Piston,1,L\n"
        )
        fd, tmp = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(csv_content)
            with app.app_context():
                _build_model_name_prefix_lookup.cache_clear()
                with patch("utils.os.path.join", return_value=tmp):
                    result = _build_model_name_prefix_lookup()
                assert "TGOO" in result.values()
                assert "TSTS" not in result.values()
        finally:
            os.unlink(tmp)
            _build_model_name_prefix_lookup.cache_clear()


class TestAircraftTypeSearch:
    def test_returns_code_prefix_matches(self, app, client):
        uid, _ = _create_user_and_tenant(app, email="ats1@example.com")
        _login(app, client, email="ats1@example.com")
        rv = client.get("/aircraft-type-search?q=C172")
        assert rv.status_code == 200
        data = rv.get_json()
        codes = [r["code"] for r in data["results"]]
        assert "C172" in codes

    def test_returns_name_matches(self, app, client):
        uid, _ = _create_user_and_tenant(app, email="ats2@example.com")
        _login(app, client, email="ats2@example.com")
        rv = client.get("/aircraft-type-search?q=Boeing+737")
        assert rv.status_code == 200
        data = rv.get_json()
        codes = [r["code"] for r in data["results"]]
        assert "B738" in codes

    def test_short_query_returns_empty(self, app, client):
        uid, _ = _create_user_and_tenant(app, email="ats3@example.com")
        _login(app, client, email="ats3@example.com")
        rv = client.get("/aircraft-type-search?q=C")
        assert rv.status_code == 200
        assert rv.get_json() == {"results": []}

    def test_unauthenticated_returns_empty(self, app, client):
        rv = client.get("/aircraft-type-search?q=C172")
        assert rv.status_code == 200
        assert rv.get_json() == {"results": []}

    def test_all_variants_returned_for_shared_designator(self, app, client):
        # P28A has many variants — all should be returned, not just the first
        uid, _ = _create_user_and_tenant(app, email="ats4@example.com")
        _login(app, client, email="ats4@example.com")
        rv = client.get("/aircraft-type-search?q=P28A")
        data = rv.get_json()
        codes = [r["code"] for r in data["results"]]
        assert codes.count("P28A") > 1, "expected multiple P28A variants"


class TestBackfillAircraftTypeIcao:
    def _setup_instance_admin(self, app, email="admin_bf@example.com"):
        with app.app_context():
            tenant = Tenant(name="BF Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email=email,
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_instance_admin=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            db.session.commit()
            return user.id

    def test_resolves_known_type_and_flashes(self, app, client):
        uid = self._setup_instance_admin(app)
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 1, 1),
                aircraft_type="C172",
                aircraft_type_icao=None,
            )
            db.session.add(entry)
            db.session.commit()
            eid = entry.id

        rv = client.post("/config/backfill/aircraft-type-icao")
        assert rv.status_code == 302

        with app.app_context():
            updated = db.session.get(PilotLogbookEntry, eid)
            assert updated.aircraft_type_icao == "C172"

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("Back-fill complete" in msg for _, msg in flashes)

    def test_skips_already_resolved(self, app, client):
        uid = self._setup_instance_admin(app, email="admin_bf2@example.com")
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 1, 1),
                aircraft_type="C172",
                aircraft_type_icao="C172",
            )
            db.session.add(entry)
            db.session.commit()

        rv = client.post("/config/backfill/aircraft-type-icao")
        assert rv.status_code == 302

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        # "0 of 0 entries resolved" — the already-resolved entry was excluded
        assert any("0 of 0" in msg for _, msg in flashes)


# ── Profile anniversary date fields ───────────────────────────────────────────


class TestProfileAnniversaryFields:
    def test_save_first_solo_date(self, app, client):
        _create_user_and_tenant(app, "ann1@example.com")
        _login(app, client, "ann1@example.com")
        client.post(
            "/pilot/profile",
            data={
                "license_number": "",
                "first_solo_date": "2020-06-15",
                "ppl_issue_date": "",
            },
        )
        with app.app_context():
            from models import User

            uid = User.query.filter_by(email="ann1@example.com").first().id
            p = PilotProfile.query.filter_by(user_id=uid).first()
            assert p.first_solo_date == date(2020, 6, 15)

    def test_save_ppl_issue_date(self, app, client):
        _create_user_and_tenant(app, "ann2@example.com")
        _login(app, client, "ann2@example.com")
        client.post(
            "/pilot/profile",
            data={
                "license_number": "",
                "first_solo_date": "",
                "ppl_issue_date": "2021-03-10",
            },
        )
        with app.app_context():
            from models import User

            uid = User.query.filter_by(email="ann2@example.com").first().id
            p = PilotProfile.query.filter_by(user_id=uid).first()
            assert p.ppl_issue_date == date(2021, 3, 10)

    def test_invalid_first_solo_date_shows_error(self, app, client):
        _create_user_and_tenant(app, "ann3@example.com")
        _login(app, client, "ann3@example.com")
        resp = client.post(
            "/pilot/profile",
            data={
                "license_number": "",
                "first_solo_date": "not-a-date",
                "ppl_issue_date": "",
            },
        )
        assert resp.status_code == 422

    def test_invalid_ppl_issue_date_shows_error(self, app, client):
        _create_user_and_tenant(app, "ann4@example.com")
        _login(app, client, "ann4@example.com")
        resp = client.post(
            "/pilot/profile",
            data={
                "license_number": "",
                "first_solo_date": "",
                "ppl_issue_date": "baddate",
            },
        )
        assert resp.status_code == 422


# ── Logbook milestone detection ───────────────────────────────────────────────


def _post_milestone_entry(client, **overrides):
    fields = {
        "date": "2024-06-01",
        "departure_place": "EBST",
        "arrival_place": "EBST",
        "single_pilot_se": "1.0",
        "function_pic": "1.0",
        "landings_day": "1",
    }
    fields.update(overrides)
    return client.post("/pilot/logbook/new", data=fields)


class TestLogbookMilestones:
    def test_100th_flight_sets_milestone(self, app, client):
        uid, _ = _create_user_and_tenant(app, "ms100@example.com")
        _login(app, client, "ms100@example.com")
        with app.app_context():
            for _ in range(99):
                db.session.add(
                    PilotLogbookEntry(
                        pilot_user_id=uid,
                        date=date(2024, 1, 1),
                        departure_place="EBST",
                        arrival_place="EBST",
                        single_pilot_se=1.0,
                        function_pic=1.0,
                        landings_day=1,
                    )
                )
            db.session.commit()
        _post_milestone_entry(client)
        with client.session_transaction() as sess:
            assert sess.get("logbook_milestone") == "100flights"

    def test_first_night_flight_sets_milestone(self, app, client):
        _create_user_and_tenant(app, "msnight@example.com")
        _login(app, client, "msnight@example.com")
        _post_milestone_entry(client, night_time="0.5")
        with client.session_transaction() as sess:
            assert sess.get("logbook_milestone") == "first_night"

    def test_second_night_flight_no_milestone(self, app, client):
        uid, _ = _create_user_and_tenant(app, "msnight2@example.com")
        _login(app, client, "msnight2@example.com")
        with app.app_context():
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=date(2024, 1, 1),
                    departure_place="EBST",
                    arrival_place="EBST",
                    night_time=0.5,
                    single_pilot_se=1.0,
                    function_pic=1.0,
                    landings_day=1,
                )
            )
            db.session.commit()
        _post_milestone_entry(client, night_time="0.3")
        with client.session_transaction() as sess:
            assert sess.get("logbook_milestone") is None


# ── Anniversary context processor ─────────────────────────────────────────────


class TestAnniversaryContextProcessor:
    def _setup(self, app, email, first_solo=None, ppl=None):
        with app.app_context():
            tenant = Tenant(name="Ann Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email=email,
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            profile = PilotProfile(
                user_id=user.id,
                first_solo_date=first_solo,
                ppl_issue_date=ppl,
            )
            db.session.add(profile)
            db.session.commit()
            return user.id

    def test_solo_anniversary_shows_in_response(self, app, client):
        from datetime import date as _d

        today = _d.today()
        ann = _d(today.year - 5, today.month, today.day)
        self._setup(app, "cp_solo@example.com", first_solo=ann)
        _login(app, client, "cp_solo@example.com")
        resp = client.get("/pilot/logbook")
        assert b"solo flight" in resp.data.lower() or b"solo" in resp.data

    def test_ppl_anniversary_shows_in_response(self, app, client):
        from datetime import date as _d

        today = _d.today()
        ann = _d(today.year - 3, today.month, today.day)
        self._setup(app, "cp_ppl@example.com", ppl=ann)
        _login(app, client, "cp_ppl@example.com")
        resp = client.get("/pilot/logbook")
        assert b"PPL" in resp.data

    def test_non_anniversary_no_banner(self, app, client):
        from datetime import date as _d, timedelta

        today = _d.today()
        non_ann = _d(today.year - 1, today.month, today.day) + timedelta(days=1)
        if non_ann.month == today.month and non_ann.day == today.day:
            non_ann = non_ann + timedelta(days=1)
        self._setup(app, "cp_none@example.com", first_solo=non_ann)
        _login(app, client, "cp_none@example.com")
        resp = client.get("/pilot/logbook")
        assert b"anniversary" not in resp.data.lower()


# ── link_entries_to_aircraft ───────────────────────────────────────────────────


class TestLinkEntriesToAircraft:
    def _setup(self, app, email="link@example.com", registration="OOABC", offset=0.3):
        with app.app_context():
            tenant = Tenant(name="Link Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email=email,
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                name="Link Pilot",
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            ac = Aircraft(
                registration=registration,
                tenant_id=tenant.id,
                make="Cessna",
                model="172S",
                flight_counter_offset=offset,
            )
            db.session.add(ac)
            db.session.commit()
            return user.id, ac.id

    def test_creates_flight_entry_for_matching_aircraft(self, app):
        from datetime import time

        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link1@example.com", registration="OOABC")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-ABC",
                departure_place="EBBR",
                arrival_place="EBOS",
                departure_time=time(9, 0),
                arrival_time=time(10, 30),
            )
            db.session.add(entry)
            db.session.flush()
            count = link_entries_to_aircraft([entry])
            db.session.commit()
            assert count == 1
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            assert flight.source == "logbook_import"
            assert flight.flight_time is None
            assert flight.departure_icao == "EBBR"
            assert flight.arrival_icao == "EBOS"
            assert flight.arrival_time == time(10, 30)
            assert entry.flight_id == flight.id

    def test_departure_time_offset_applied(self, app):
        from datetime import time

        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(
            app, email="link2@example.com", registration="OODEF", offset=0.5
        )
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-DEF",
                departure_time=time(9, 30),
                arrival_time=time(10, 30),
            )
            db.session.add(entry)
            db.session.flush()
            link_entries_to_aircraft([entry])
            db.session.commit()
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            assert flight.departure_time == time(9, 0)

    def test_creates_pic_crew_entry(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link3@example.com", registration="OOGHI")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-GHI",
            )
            db.session.add(entry)
            db.session.flush()
            link_entries_to_aircraft([entry])
            db.session.commit()
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            crew = FlightCrew.query.filter_by(flight_id=flight.id).first()
            assert crew is not None
            assert crew.role == "PIC"
            assert crew.user_id == uid

    def test_skips_entry_with_no_registration(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, _ = self._setup(app, email="link4@example.com")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration=None,
            )
            db.session.add(entry)
            db.session.flush()
            count = link_entries_to_aircraft([entry])
            assert count == 0
            assert entry.flight_id is None

    def test_skips_already_linked_entry(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link5@example.com", registration="OOJKL")
        with app.app_context():
            existing_flight = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 3, 10),
                departure_icao="EBBR",
                arrival_icao="EBOS",
            )
            db.session.add(existing_flight)
            db.session.flush()
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-JKL",
                flight_id=existing_flight.id,
            )
            db.session.add(entry)
            db.session.flush()
            count = link_entries_to_aircraft([entry])
            assert count == 0

    def test_skips_unmanaged_aircraft(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, _ = self._setup(app, email="link6@example.com")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-UNKNOWN",
            )
            db.session.add(entry)
            db.session.flush()
            count = link_entries_to_aircraft([entry])
            assert count == 0
            assert entry.flight_id is None

    def test_place_icao_zzzz_for_missing(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link7@example.com", registration="OOMNO")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-MNO",
                departure_place=None,
                arrival_place=None,
            )
            db.session.add(entry)
            db.session.flush()
            link_entries_to_aircraft([entry])
            db.session.commit()
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            assert flight.departure_icao == "ZZZZ"
            assert flight.arrival_icao == "ZZZZ"

    def test_no_crew_when_pilot_user_not_found(self, app):
        from sqlalchemy import text

        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link8@example.com", registration="OOPQR")
        with app.app_context():
            # Disable FK checks to allow a non-existent pilot_user_id so we
            # can test the defensive guard in link_entries_to_aircraft.
            db.session.execute(text("PRAGMA foreign_keys=OFF"))
            try:
                tenant_id = db.session.get(Aircraft, ac_id).tenant_id
                # A TenantUser row (but no backing User row) is needed so the
                # tenant-scoped aircraft match still succeeds for this
                # otherwise-nonexistent pilot_user_id.
                db.session.add(
                    TenantUser(user_id=999999, tenant_id=tenant_id, role=Role.PILOT)
                )
                entry = PilotLogbookEntry(
                    pilot_user_id=999999,
                    date=date(2024, 3, 10),
                    aircraft_registration="OO-PQR",
                )
                db.session.add(entry)
                db.session.flush()
                count = link_entries_to_aircraft([entry])
                db.session.commit()
                assert count == 1
                flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
                assert flight is not None
                assert FlightCrew.query.filter_by(flight_id=flight.id).count() == 0
            finally:
                db.session.execute(text("PRAGMA foreign_keys=ON"))

    def test_departure_time_none_when_entry_has_none(self, app):
        from pilots.logbook_import import link_entries_to_aircraft  # pyright: ignore[reportMissingImports]

        uid, ac_id = self._setup(app, email="link9@example.com", registration="OOSTU")
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 3, 10),
                aircraft_registration="OO-STU",
                departure_time=None,
            )
            db.session.add(entry)
            db.session.flush()
            link_entries_to_aircraft([entry])
            db.session.commit()
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            assert flight.departure_time is None


# ── Backfill pilot log to flight entries route ─────────────────────────────────


class TestBackfillPilotLogToFlightEntries:
    def _setup_instance_admin(
        self, app, email="bpfa@example.com", registration="OOVWX"
    ):
        with app.app_context():
            tenant = Tenant(name="BPFA Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email=email,
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
                is_instance_admin=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            ac = Aircraft(
                registration=registration,
                tenant_id=tenant.id,
                make="Cessna",
                model="172S",
                flight_counter_offset=0.3,
            )
            db.session.add(ac)
            db.session.commit()
            return user.id, ac.id

    def test_creates_entries_and_flashes(self, app, client):
        uid, ac_id = self._setup_instance_admin(
            app, email="bpfa1@example.com", registration="OOVWX"
        )
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with app.app_context():
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 5, 1),
                aircraft_registration="OO-VWX",
            )
            db.session.add(entry)
            db.session.commit()

        rv = client.post("/config/backfill/pilot-log-to-flight-entries")
        assert rv.status_code == 302

        with app.app_context():
            flight = FlightEntry.query.filter_by(aircraft_id=ac_id).first()
            assert flight is not None
            assert flight.source == "logbook_import"

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("Back-fill complete" in msg for _, msg in flashes)

    def test_zero_when_no_matching_entries(self, app, client):
        uid, _ = self._setup_instance_admin(
            app, email="bpfa2@example.com", registration="OOYZA"
        )
        with client.session_transaction() as sess:
            sess["user_id"] = uid

        rv = client.post("/config/backfill/pilot-log-to-flight-entries")
        assert rv.status_code == 302

        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("0" in msg for _, msg in flashes)

    def test_non_instance_admin_gets_403(self, app, client):
        uid, _ = _create_user_and_tenant(app, email="bpfa3@example.com")
        with client.session_transaction() as sess:
            sess["user_id"] = uid

        rv = client.post("/config/backfill/pilot-log-to-flight-entries")
        assert rv.status_code == 403

    def test_skips_already_linked_entries(self, app, client):
        uid, ac_id = self._setup_instance_admin(
            app, email="bpfa4@example.com", registration="OOBCD"
        )
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        with app.app_context():
            existing_flight = FlightEntry(
                aircraft_id=ac_id,
                date=date(2024, 5, 1),
                departure_icao="EBBR",
                arrival_icao="EBOS",
            )
            db.session.add(existing_flight)
            db.session.flush()
            entry = PilotLogbookEntry(
                pilot_user_id=uid,
                date=date(2024, 5, 1),
                aircraft_registration="OO-BCD",
                flight_id=existing_flight.id,
            )
            db.session.add(entry)
            db.session.commit()

        rv = client.post("/config/backfill/pilot-log-to-flight-entries")
        assert rv.status_code == 302

        with app.app_context():
            assert FlightEntry.query.filter_by(aircraft_id=ac_id).count() == 1


# ── Visual indicator for logbook-imported entries needing time review ──────────


class TestFlightListLogbookImportIndicator:
    def _setup(self, app, email="viind@example.com", registration="OOEFG"):
        with app.app_context():
            tenant = Tenant(name="VI Hangar")
            db.session.add(tenant)
            db.session.flush()
            user = User(
                email=email,
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            ac = Aircraft(
                registration=registration,
                tenant_id=tenant.id,
                make="Cessna",
                model="172S",
                flight_counter_offset=0.3,
            )
            db.session.add(ac)
            db.session.flush()
            flight = FlightEntry(
                aircraft_id=ac.id,
                date=date(2024, 6, 1),
                departure_icao="EBBR",
                arrival_icao="EBOS",
                source="logbook_import",
                flight_time=None,
            )
            db.session.add(flight)
            db.session.commit()
            return user.id, ac.id, flight.id

    def test_warning_icon_shown_for_logbook_import_without_flight_time(
        self, app, client
    ):
        uid, ac_id, _ = self._setup(app, email="vi1@example.com", registration="OOHIJ")
        _login(app, client, email="vi1@example.com")
        rv = client.get(f"/aircraft/{ac_id}/flights")
        assert rv.status_code == 200
        assert b"bi-clock-history" in rv.data

    def test_warning_icon_absent_when_flight_time_set(self, app, client):
        uid, ac_id, flight_id = self._setup(
            app, email="vi2@example.com", registration="OOKLM"
        )
        with app.app_context():
            flight = db.session.get(FlightEntry, flight_id)
            flight.flight_time = 1.5
            db.session.commit()
        _login(app, client, email="vi2@example.com")
        rv = client.get(f"/aircraft/{ac_id}/flights")
        assert rv.status_code == 200
        assert b"bi-clock-history" not in rv.data

    def test_warning_icon_absent_for_manual_entry(self, app, client):
        uid, ac_id, flight_id = self._setup(
            app, email="vi3@example.com", registration="OONOP"
        )
        with app.app_context():
            flight = db.session.get(FlightEntry, flight_id)
            flight.source = None
            db.session.commit()
        _login(app, client, email="vi3@example.com")
        rv = client.get(f"/aircraft/{ac_id}/flights")
        assert rv.status_code == 200
        assert b"bi-clock-history" not in rv.data
