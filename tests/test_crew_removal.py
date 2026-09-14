"""
Tests for deleting flights that are in more than one pilot's logbook: every
delete path may only remove the acting user — never another pilot's hours
(flights/crew_removal.py).
"""

from datetime import UTC, datetime
from decimal import Decimal

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftGpsImportBatch,
    AirframeImportBatch,
    CrewRole,
    Flight,
    LogbookImportBatch,
    Role,
    db,
)

from tests.test_crew_invites import (  # pyright: ignore[reportMissingImports]
    _add_flight,
    _login,
    _make_user,
    _world,
)


def _world_with_aircraft(app):
    tid, kris, jan, els = _world(app)
    owner = _make_user(app, tid, "olga@example.com", "Olga Owner", role=Role.OWNER)
    with app.app_context():
        ac = Aircraft(tenant_id=tid, registration="OO-ABC", make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return tid, kris, jan, els, owner, ac.id


def _flight(app, fid):
    with app.app_context():
        return db.session.get(Flight, fid)


def _logbook_ids(app, uid):
    with app.app_context():
        return {
            f.id
            for f in Flight.query.filter(
                (Flight.pic_user_id == uid) | (Flight.second_crew_user_id == uid)
            ).all()
        }


def _shared_flight(app, kris, jan, **fields):
    """A flight in two logbooks: *kris* as PIC, *jan* (confirmed) as
    instructor in the second slot."""
    return _add_flight(
        app,
        pic_user_id=kris,
        pic_name="Kris Logger",
        function_pic=Decimal("1.5"),
        second_crew_user_id=jan,
        second_crew_name="Jan Peeters",
        second_crew_role=CrewRole.IP,
        function_instructor=Decimal("1.5"),
        **fields,
    )


# ── Pilot logbook side ────────────────────────────────────────────────────────


class TestPilotLogbookDelete:
    def test_delete_only_removes_own_link_when_other_pilot_linked(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared_flight(app, kris, jan)
        _login(client, jan)
        html = client.get("/pilot/logbook").data.decode()
        assert "Remove this flight from your logbook?" in html

        resp = client.post(f"/pilot/logbook/{fid}/delete", follow_redirects=True)
        assert b"It stays in the other pilot" in resp.data
        fe = _flight(app, fid)
        assert fe is not None
        assert fe.second_crew_user_id is None
        assert fe.function_instructor is None
        assert fe.second_crew_name == "Jan Peeters"
        # Kris's side is untouched.
        assert fe.pic_user_id == kris
        assert fe.function_pic == Decimal("1.5")
        assert _logbook_ids(app, kris) == {fid}
        assert _logbook_ids(app, jan) == set()

    def test_last_linked_pilot_deletes_the_flight(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared_flight(app, kris, jan)
        _login(client, jan)
        client.post(f"/pilot/logbook/{fid}/delete")
        _login(client, kris)
        html = client.get("/pilot/logbook").data.decode()
        assert "Delete this logbook entry?" in html
        client.post(f"/pilot/logbook/{fid}/delete")
        assert _flight(app, fid) is None

    def test_import_rollback_keeps_flights_other_pilots_have(self, app, client):
        _tid, kris, jan, _els = _world(app)
        with app.app_context():
            batch = LogbookImportBatch(
                pilot_user_id=kris,
                source_filename="kris.csv",
                imported_at=datetime.now(UTC),
                row_count=2,
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id
        shared = _shared_flight(app, kris, jan, import_batch_id=batch_id)
        solo = _add_flight(app, pic_user_id=kris, import_batch_id=batch_id)
        _login(client, kris)
        resp = client.post(
            f"/pilot/logbook/import/{batch_id}/rollback", follow_redirects=True
        )
        assert b"Import deleted: one entry removed." in resp.data
        assert b"One flight stays in another pilot" in resp.data
        assert _flight(app, solo) is None
        fe = _flight(app, shared)
        assert fe.import_batch_id is None
        assert fe.pic_user_id is None
        assert fe.second_crew_user_id == jan
        with app.app_context():
            assert db.session.get(LogbookImportBatch, batch_id) is None


# ── Aircraft side ─────────────────────────────────────────────────────────────


class TestAircraftSideDelete:
    def test_aircraft_log_delete_detaches_flight_linked_to_pilots(self, app, client):
        _tid, kris, jan, _els, owner, acid = _world_with_aircraft(app)
        fid = _shared_flight(app, kris, jan, aircraft_id=acid)
        _login(client, owner)
        html = client.get(f"/aircraft/{acid}/flights").data.decode()
        assert "Remove this flight from the aircraft log?" in html

        resp = client.post(
            f"/aircraft/{acid}/flights/{fid}/delete", follow_redirects=True
        )
        assert b"removed from the aircraft log" in resp.data
        fe = _flight(app, fid)
        assert fe.aircraft_id is None
        assert fe.other_aircraft_registration == "OO-ABC"
        assert fe.other_aircraft_type == "Cessna 172S"
        assert fe.pic_user_id == kris
        assert fe.second_crew_user_id == jan
        assert _logbook_ids(app, jan) == {fid}

    def test_aircraft_log_delete_by_linked_pilot_unlinks_only_them(self, app, client):
        _tid, _kris, jan, _els, owner, acid = _world_with_aircraft(app)
        fid = _shared_flight(app, owner, jan, aircraft_id=acid)
        _login(client, owner)
        client.post(f"/aircraft/{acid}/flights/{fid}/delete")
        fe = _flight(app, fid)
        assert fe.aircraft_id is None
        assert fe.pic_user_id is None
        assert fe.function_pic is None
        assert fe.second_crew_user_id == jan

    def test_aircraft_log_delete_of_own_solo_flight_still_deletes(self, app, client):
        _tid, _kris, _jan, _els, owner, acid = _world_with_aircraft(app)
        fid = _add_flight(app, pic_user_id=owner, aircraft_id=acid)
        _login(client, owner)
        html = client.get(f"/aircraft/{acid}/flights").data.decode()
        assert "Delete this flight entry?" in html
        client.post(f"/aircraft/{acid}/flights/{fid}/delete")
        assert _flight(app, fid) is None

    def test_airframe_import_rollback_detaches_linked_flights(self, app, client):
        _tid, kris, jan, _els, owner, acid = _world_with_aircraft(app)
        with app.app_context():
            batch = AirframeImportBatch(
                aircraft_id=acid,
                source_filename="airframe.csv",
                imported_at=datetime.now(UTC),
                row_count=2,
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id
        claimed = _shared_flight(
            app, kris, jan, aircraft_id=acid, airframe_import_batch_id=batch_id
        )
        unclaimed = _add_flight(
            app,
            pic_name="Paper Pilot",
            aircraft_id=acid,
            airframe_import_batch_id=batch_id,
        )
        _login(client, owner)
        resp = client.post(
            f"/aircraft/{acid}/flights/import/{batch_id}/rollback",
            follow_redirects=True,
        )
        assert b"Import deleted: 1 flight entries removed." in resp.data
        assert b"One flight stays in a pilot" in resp.data
        assert _flight(app, unclaimed) is None
        fe = _flight(app, claimed)
        assert fe.aircraft_id is None
        assert fe.airframe_import_batch_id is None
        assert _logbook_ids(app, kris) == {claimed}

    def test_gps_import_rollback_detaches_linked_flights(self, app, client):
        _tid, kris, jan, _els, owner, acid = _world_with_aircraft(app)
        with app.app_context():
            batch = AircraftGpsImportBatch(aircraft_id=acid, format_detected="gpx")
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id
        claimed = _shared_flight(
            app, kris, jan, aircraft_id=acid, gps_import_batch_id=batch_id
        )
        unclaimed = _add_flight(app, aircraft_id=acid, gps_import_batch_id=batch_id)
        _login(client, owner)
        resp = client.post(
            f"/aircraft/{acid}/gps-import/{batch_id}/rollback", follow_redirects=True
        )
        assert b"One flight stays in a pilot" in resp.data
        assert _flight(app, unclaimed) is None
        fe = _flight(app, claimed)
        assert fe.aircraft_id is None
        assert fe.gps_import_batch_id is None
        assert fe.second_crew_user_id == jan

    def test_deleting_aircraft_keeps_every_pilot_linked_flight(self, app, client):
        _tid, kris, jan, _els, owner, acid = _world_with_aircraft(app)
        shared = _shared_flight(app, kris, jan, aircraft_id=acid)
        owners_own = _add_flight(app, pic_user_id=owner, aircraft_id=acid)
        unlinked = _add_flight(app, pic_name="Paper Pilot", aircraft_id=acid)
        _login(client, owner)
        resp = client.post(f"/aircraft/{acid}/delete", follow_redirects=True)
        assert b"2 flights stay in pilots" in resp.data
        with app.app_context():
            assert db.session.get(Aircraft, acid) is None
        assert _flight(app, unlinked) is None
        for fid, expected_reg in ((shared, "OO-ABC"), (owners_own, "OO-ABC")):
            fe = _flight(app, fid)
            assert fe is not None
            assert fe.aircraft_id is None
            assert fe.other_aircraft_registration == expected_reg
        assert _logbook_ids(app, jan) == {shared}
        assert _logbook_ids(app, owner) == {owners_own}


# ── Helpers in isolation ──────────────────────────────────────────────────────


class TestRemovalHelpers:
    def test_unlink_none_and_detach_standalone_are_no_ops(self, app):
        _tid, kris, jan, _els = _world(app)
        fid = _shared_flight(app, kris, jan)
        with app.app_context():
            from flights.crew_removal import detach_from_aircraft, unlink_user

            fe = db.session.get(Flight, fid)
            unlink_user(fe, None)
            detach_from_aircraft(fe)
            assert (fe.pic_user_id, fe.second_crew_user_id) == (kris, jan)
            assert fe.other_aircraft_registration == "OO-TST"

    def test_deleting_aircraft_with_loaded_flights_collection(self, app):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        shared = _shared_flight(app, kris, jan, aircraft_id=acid)
        with app.app_context():
            from flights.crew_removal import detach_from_aircraft

            ac = db.session.get(Aircraft, acid)
            assert len(ac.flights) == 1  # collection loaded before detaching
            detach_from_aircraft(ac.flights[0])
            db.session.flush()
            db.session.expire(ac, ["flights"])
            db.session.delete(ac)
            db.session.commit()
        assert _flight(app, shared) is not None
