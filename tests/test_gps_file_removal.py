"""Taking a GPS file off a flight, the trash, and undoing an import.

- services.gps_removal: removing one part (one file) of a track rebuilds
  the map and the GPS block times from the parts left; the last part takes
  the track with it; the file goes to the trash unless still used;
- restoring puts the file and its part back (or just the file, the flight
  being gone); the daily purge empties the trash after TRASH_DAYS;
- "Undo import" removes only the parts its batch added;
- the flight page's GPS file list, and the removed GPS files page.
"""

import os
from datetime import UTC, date, datetime, time, timedelta

import pytest
from models import (  # pyright: ignore[reportMissingImports]
    AircraftGpsImportBatch,
    Flight,
    GpsFileRemoval,
    GpsTrack,
    Role,
    TenantUser,
    User,
    db,
)
from services.gps_link import link_track  # pyright: ignore[reportMissingImports]
from services.gps_removal import (  # pyright: ignore[reportMissingImports]
    TRASH_DAYS,
    _abs,
    listed_parts,
    purge_date,
    purge_expired,
    remove_batch_parts,
    remove_part,
    remove_whole_track,
    restore,
)

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)


def _line(*pts):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(p) for p in pts]},
    }


def _t(h, m=0):
    return datetime(2024, 6, 1, h, m, tzinfo=UTC)


def _write(app, rel, content=None):
    path = os.path.join(app.config["UPLOAD_FOLDER"], *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content if content is not None else rel)
    return path


def _exists(app, rel):
    return os.path.isfile(os.path.join(app.config["UPLOAD_FOLDER"], *rel.split("/")))


A = "t/OO-GPS/gps_logs/a.csv"
B = "t/OO-GPS/gps_logs/b.csv"
TAXI = "t/OO-GPS/gps_logs/taxi.csv"


def _world(app, *, pilot=False):
    """A flight with a track of two flight parts (a.csv, b.csv) and a taxi
    part (taxi.csv), each file on disk."""
    uid, tid, ac_id = _make_user_and_aircraft(app)
    with app.app_context():
        f = Flight(
            aircraft_id=ac_id,
            date=date(2024, 6, 1),
            departure_icao="EBAW",
            arrival_icao="EBAW",
            departure_time=time(10, 0),
            arrival_time=time(13, 0),
            pic_user_id=uid if pilot else None,
        )
        db.session.add(f)
        db.session.flush()
        for rel, (start, end), pts, taxi in (
            (A, (_t(10), _t(11)), ((1, 1), (1, 2)), False),
            (B, (_t(11, 30), _t(12)), ((2, 2),), False),
            (TAXI, (_t(9, 45), _t(9, 55)), ((0, 0),), True),
        ):
            _write(app, rel)
            link_track(
                f,
                geojson=_line(*pts),
                files=[rel],
                block_off=start,
                block_on=end,
                source_filename="a.csv",
                departure_icao="EBAW",
                taxi=taxi,
                batch_id=None,
            )
        db.session.commit()
        return uid, tid, ac_id, f.id


def _args(app, tid, uid=None):
    return {
        "tenant_id": tid,
        "user_id": uid,
        "upload_folder": app.config["UPLOAD_FOLDER"],
    }


def _aware(*values):
    """Datetimes read back from SQLite come back naive (stored as UTC)."""
    return tuple(v.replace(tzinfo=UTC) if v and v.tzinfo is None else v for v in values)


def _coords(track):
    return [c[:2] for c in track.geojson["geometry"]["coordinates"]]


class TestRemovePart:
    def test_one_file_off_rebuilds_the_track(self, app):
        uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            parts = listed_parts(f.gps_track)
            assert [(p["files"], p["taxi"]) for p in parts] == [
                ([("taxi.csv", 0)], True),
                ([("a.csv", 1)], False),
                ([("b.csv", 2)], False),
            ]
            removal = remove_part(f, 2, **_args(app, tid, uid))
            db.session.commit()
            track = f.gps_track
            assert track.source_files == [TAXI, A]
            assert _coords(track) == [[0, 0], [1, 1], [1, 2]]
            # Block times from the flight parts left; logged times untouched.
            assert _aware(f.block_off_utc, f.block_on_utc) == (_t(10), _t(11))
            assert (f.departure_time, f.arrival_time) == (time(10, 0), time(13, 0))
            assert not _exists(app, B)
            [entry] = removal.files
            assert entry["path"] == B and _exists(app, entry["trash"])
            assert entry["trash"].startswith("_trash/gps/")
            assert removal.flight_label == "2024-06-01 EBAW → EBAW"
            assert removal.part["coords"] == [[2, 2]]
            assert removal.part["track"]["source_filename"] == "a.csv"

    def test_only_taxi_left_clears_the_gps_times(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            remove_part(f, 2, **_args(app, tid))
            remove_part(f, 1, **_args(app, tid))
            assert f.gps_track_id is not None
            assert f.block_off_utc is None and f.block_on_utc is None

    def test_last_part_takes_the_track(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            track_id = f.gps_track_id
            removals = remove_whole_track(f, **_args(app, tid))
            db.session.commit()
            assert len(removals) == 3
            assert f.gps_track_id is None and f.block_off_utc is None
            assert db.session.get(GpsTrack, track_id) is None
            assert not any(_exists(app, rel) for rel in (A, B, TAXI))

    def test_file_still_used_stays(self, app):
        _uid, tid, _ac_id, fid = _world(app)
        with app.app_context():
            # Another flight's track lists b.csv too (one log, two flights).
            other = GpsTrack(source_files=[B], geojson=_line((5, 5)))
            db.session.add(other)
            db.session.flush()
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid))
            assert removal.files == [{"path": B, "trash": None}]
            assert _exists(app, B)

            # A part made of two files, one also in another part: only the
            # other one goes.
            _write(app, "t/OO-GPS/gps_logs/c.csv")
            link_track(
                f,
                geojson=_line((3, 3)),
                files=["t/OO-GPS/gps_logs/c.csv", A],
                block_off=_t(12, 30),
                block_on=_t(12, 45),
            )
            idx = next(
                p["index"] for p in listed_parts(f.gps_track) if len(p["files"]) == 2
            )
            removal = remove_part(f, idx, **_args(app, tid))
            assert [e["trash"] is None for e in removal.files] == [False, True]
            assert _exists(app, A)

    def test_missing_file_and_no_track(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], *B.split("/")))
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid))
            assert removal.files == [{"path": B, "trash": None}]
            remove_whole_track(f, **_args(app, tid))
            with pytest.raises(ValueError, match="no GPS track"):
                remove_part(f, 0, **_args(app, tid))

    def test_track_from_before_parts(self, app):
        _uid, tid, ac_id = _make_user_and_aircraft(app)
        with app.app_context():
            track = GpsTrack(
                source_files=None,
                geojson=_line((1, 1)),
                block_off_utc=_t(10),
                block_on_utc=_t(11),
            )
            db.session.add(track)
            db.session.flush()
            f = Flight(aircraft_id=ac_id, date=date(2024, 6, 1), gps_track_id=track.id)
            db.session.add(f)
            db.session.flush()
            [part] = listed_parts(track)
            assert part["files"] == [] and part["start"] == _t(10)
            assert listed_parts(None) == []
            removal = remove_part(f, 0, **_args(app, tid))
            assert removal.files == [] and f.gps_track_id is None

    def test_paths_stay_in_the_uploads_folder(self, app):
        with pytest.raises(ValueError, match="outside"):
            _abs(app.config["UPLOAD_FOLDER"], "../escape.csv")


class TestRestoreAndPurge:
    def test_restore_puts_file_and_part_back(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid))
            trash_dir = os.path.dirname(
                _abs(app.config["UPLOAD_FOLDER"], removal.files[0]["trash"])
            )
            assert restore(removal, app.config["UPLOAD_FOLDER"]) is f
            assert removal.restored_at is not None
            assert _exists(app, B) and not os.path.isdir(trash_dir)
            assert f.gps_track.source_files == [TAXI, A, B]
            assert _aware(f.block_off_utc, f.block_on_utc) == (_t(10), _t(12))

            # The whole track gone, then one part restored: a new track
            # with its details.
            removals = remove_whole_track(f, **_args(app, tid))
            restore(removals[-1], app.config["UPLOAD_FOLDER"])
            assert f.gps_track.source_filename == "a.csv"

    def test_restore_when_the_name_was_taken(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            same = remove_part(f, 2, **_args(app, tid))
            other = remove_part(f, 1, **_args(app, tid))
            _write(app, B)  # the same content uploaded again
            _write(app, A, "something else")
            restore(same, app.config["UPLOAD_FOLDER"])
            assert not _exists(app, same.files[0]["trash"])
            restore(other, app.config["UPLOAD_FOLDER"])
            names = sorted(
                os.listdir(os.path.dirname(_abs(app.config["UPLOAD_FOLDER"], A)))
            )
            suffixed = [n for n in names if n.startswith("a-")]
            assert len(suffixed) == 1
            assert f"t/OO-GPS/gps_logs/{suffixed[0]}" in f.gps_track.source_files

    def test_restore_without_a_flight_or_a_file(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid))
            os.remove(_abs(app.config["UPLOAD_FOLDER"], removal.files[0]["trash"]))
            removal.flight_id = None
            assert restore(removal, app.config["UPLOAD_FOLDER"]) is None
            # Never moved (still used): nothing to move back.
            kept = GpsFileRemoval(
                tenant_id=tid, files=[{"path": A, "trash": None}], part={}
            )
            assert restore(kept, app.config["UPLOAD_FOLDER"]) is None

    def test_purge_after_the_trash_days(self, app):
        _uid, tid, _ac, fid = _world(app)
        with app.app_context():
            f = db.session.get(Flight, fid)
            old = remove_part(f, 2, **_args(app, tid))
            recent = remove_part(f, 1, **_args(app, tid))
            done = remove_part(f, 0, **_args(app, tid))
            restore(done, app.config["UPLOAD_FOLDER"])
            old.removed_at = datetime.now(UTC) - timedelta(days=TRASH_DAYS, hours=1)
            # A trash entry whose file is already gone is purged all the same.
            gone = GpsFileRemoval(
                tenant_id=tid,
                removed_at=datetime.now(UTC) - timedelta(days=30),
                files=[
                    {"path": "x.csv", "trash": "_trash/gps/zz/x.csv"},
                    {"path": "y"},
                ],
            )
            db.session.add(gone)
            db.session.commit()
            trash = old.files[0]["trash"]
            assert purge_expired(app.config["UPLOAD_FOLDER"]) == 2
            assert not _exists(app, trash) and old.purged_at is not None
            assert gone.purged_at is not None
            assert recent.purged_at is None and _exists(app, recent.files[0]["trash"])
            # Naive timestamps (SQLite) still give the purge date.
            recent.removed_at = datetime(2024, 6, 1)
            assert purge_date(recent) == datetime(2024, 6, 1 + TRASH_DAYS, tzinfo=UTC)


class TestUndoImport:
    def test_only_the_batch_parts_come_off(self, app):
        _uid, tid, ac_id, fid = _world(app)
        with app.app_context():
            batch = AircraftGpsImportBatch(
                aircraft_id=ac_id, format_detected="gpx", linked_flight_entry_ids=[fid]
            )
            db.session.add(batch)
            db.session.flush()
            f = db.session.get(Flight, fid)
            # Untagged track: no part says its batch, so all of it goes.
            assert len(remove_batch_parts(f, batch.id, **_args(app, tid))) == 3
            assert remove_batch_parts(f, batch.id, **_args(app, tid)) == []

            for rel, start, batch_id in ((A, _t(10), None), (B, _t(11, 30), batch.id)):
                link_track(
                    f,
                    geojson=_line((1, 1)),
                    files=[rel],
                    block_off=start,
                    block_on=start + timedelta(minutes=20),
                    batch_id=batch_id,
                )
            removed = remove_batch_parts(f, batch.id, **_args(app, tid))
            assert [r.files[0]["path"] for r in removed] == [B]
            assert f.gps_track.source_files == [A]

    def test_rollback_route(self, client, app):
        uid, _tid, ac_id, fid = _world(app)
        _login(client, uid)
        _write(app, "t/OO-GPS/gps_logs/new.csv")
        with app.app_context():
            batch = AircraftGpsImportBatch(
                aircraft_id=ac_id, format_detected="gpx", linked_flight_entry_ids=[fid]
            )
            db.session.add(batch)
            db.session.flush()
            f = db.session.get(Flight, fid)
            link_track(
                f,
                geojson=_line((9, 9)),
                files=[TAXI.replace("taxi", "late")],
                block_off=_t(12, 30),
                block_on=_t(12, 50),
                batch_id=batch.id,
            )
            created = Flight(
                aircraft_id=ac_id,
                date=date(2024, 6, 2),
                gps_import_batch_id=batch.id,
                source="gps_import",
            )
            db.session.add(created)
            db.session.flush()
            link_track(
                created,
                geojson=_line((8, 8)),
                files=["t/OO-GPS/gps_logs/new.csv"],
                block_off=_t(14),
                block_on=_t(15),
            )
            db.session.commit()
            batch_id, created_id = batch.id, created.id
        resp = client.post(
            f"/aircraft/{ac_id}/gps-import/{batch_id}/rollback", follow_redirects=True
        )
        assert b"Import undone" in resp.data
        with app.app_context():
            f = db.session.get(Flight, fid)
            # The earlier parts stay: only this batch's came off.
            assert f.gps_track.source_files == [TAXI, A, B]
            assert db.session.get(Flight, created_id) is None
            assert not _exists(app, "t/OO-GPS/gps_logs/new.csv")
            reasons = {r.reason for r in GpsFileRemoval.query.all()}
            assert reasons == {"import_undone"}
            orphan = GpsFileRemoval.query.filter(
                GpsFileRemoval.files.isnot(None), GpsFileRemoval.flight_id.is_(None)
            ).count()
            assert orphan == 1


def _admin(app, tid, email="admin-gps@example.com"):
    with app.app_context():
        user = User(email=email, password_hash="x", is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tid, role=Role.ADMIN))
        db.session.commit()
        return user.id


class TestFlightPageAndRoutes:
    def test_list_remove_and_restore(self, client, app):
        uid, _tid, ac_id, fid = _world(app)
        _login(client, uid)
        page = client.get(f"/aircraft/{ac_id}/flights/{fid}").data.decode()
        assert "taxi.csv" in page and "b.csv" in page
        assert f"/flights/{fid}/gps-part/2/remove" in page
        assert "/flights/removed-gps-files" in page
        with app.app_context():
            key = listed_parts(db.session.get(Flight, fid).gps_track)[2]["key"]

        # A stale page: nothing removed.
        resp = client.post(
            f"/flights/{fid}/gps-part/2/remove",
            data={"part_key": "other"},
            follow_redirects=True,
        )
        assert b"changed meanwhile" in resp.data
        resp = client.post(
            f"/flights/{fid}/gps-part/2/remove",
            data={"part_key": key, "next": f"/aircraft/{ac_id}/flights/{fid}"},
        )
        assert resp.headers["Location"].endswith(f"/aircraft/{ac_id}/flights/{fid}")
        with app.app_context():
            assert db.session.get(Flight, fid).gps_track.source_files == [TAXI, A]
            removal_id = GpsFileRemoval.query.one().id

        listing = client.get("/flights/removed-gps-files").data.decode()
        assert "b.csv" in listing and "Restore" in listing
        resp = client.post(
            f"/flights/removed-gps-files/{removal_id}/restore", follow_redirects=True
        )
        assert b"its track is back on the flight" in resp.data
        assert "Restored on" in client.get("/flights/removed-gps-files").data.decode()
        resp = client.post(
            f"/flights/removed-gps-files/{removal_id}/restore", follow_redirects=True
        )
        assert b"can no longer be restored" in resp.data

    def test_restore_after_the_flight_was_deleted(self, client, app):
        uid, tid, _ac, fid = _world(app)
        _login(client, uid)
        with app.app_context():
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid, uid))
            removal.flight_id = None
            db.session.commit()
            removal_id = removal.id
        resp = client.post(
            f"/flights/removed-gps-files/{removal_id}/restore", follow_redirects=True
        )
        assert b"no longer exists" in resp.data

    def test_purged_and_undone_rows(self, client, app):
        uid, tid, _ac, _fid = _world(app)
        _login(client, uid)
        with app.app_context():
            db.session.add(
                GpsFileRemoval(
                    tenant_id=tid,
                    removed_by_user_id=uid,
                    reason="import_undone",
                    files=[],
                    purged_at=datetime(2024, 6, 9, tzinfo=UTC),
                )
            )
            db.session.commit()
        page = client.get("/flights/removed-gps-files").data.decode()
        assert "Deleted on 2024-06-09" in page and "Import undone" in page
        assert "no original file kept" in page

    def test_who_sees_and_restores_what(self, client, app):
        uid, tid, _ac, fid = _world(app)
        admin_id = _admin(app, tid)
        with app.app_context():
            f = db.session.get(Flight, fid)
            removal = remove_part(f, 2, **_args(app, tid, admin_id))
            db.session.commit()
            removal_id = removal.id
        # The owner didn't remove it: not listed, can't restore.
        _login(client, uid)
        assert "b.csv" not in client.get("/flights/removed-gps-files").data.decode()
        assert (
            "No GPS file has been removed"
            in client.get("/flights/removed-gps-files").data.decode()
        )
        assert (
            client.post(f"/flights/removed-gps-files/{removal_id}/restore").status_code
            == 404
        )
        assert (
            client.post("/flights/removed-gps-files/999999/restore").status_code == 404
        )
        # An admin sees every removal.
        _login(client, admin_id)
        assert "b.csv" in client.get("/flights/removed-gps-files").data.decode()

    def test_pilot_page_and_unsafe_next(self, client, app):
        uid, _tid, _ac_id, fid = _world(app, pilot=True)
        _login(client, uid)
        page = client.get(f"/pilot/logbook/{fid}/view").data.decode()
        assert f"/flights/{fid}/gps-part/0/remove" in page
        with app.app_context():
            f = db.session.get(Flight, fid)
            key = listed_parts(f.gps_track)[0]["key"]
            f.aircraft_id = None  # flown on an aircraft not managed here
            db.session.commit()
        resp = client.post(
            f"/flights/{fid}/gps-part/0/remove",
            data={"part_key": key, "next": "//evil.example/"},
        )
        assert resp.headers["Location"].endswith(f"/pilot/logbook/{fid}/view")

    def test_not_allowed_to_edit(self, client, app):
        _uid, tid, _ac_id, fid = _world(app)
        with app.app_context():
            viewer = User(
                email="viewer-gps@example.com", password_hash="x", is_active=True
            )
            db.session.add(viewer)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=viewer.id, tenant_id=tid, role=Role.VIEWER)
            )
            db.session.commit()
            viewer_id = viewer.id
        with app.test_request_context():
            from flask import session
            from flights.routes import gps_editable

            session["user_id"] = viewer_id
            with app.app_context():
                assert gps_editable(db.session.get(Flight, fid)) is False
                viewer = db.session.get(User, viewer_id)
                viewer.is_pilot = True
                db.session.commit()
                assert gps_editable(db.session.get(Flight, fid)) is True

    def test_flight_form_can_edit_check_applies(self, client, app, monkeypatch):
        uid, _tid, _ac, fid = _world(app)
        _login(client, uid)
        import flights.routes as flight_routes

        monkeypatch.setattr(flight_routes, "can_edit_shared", lambda *_a: False)
        assert client.post(f"/flights/{fid}/gps-part/0/remove").status_code == 403
