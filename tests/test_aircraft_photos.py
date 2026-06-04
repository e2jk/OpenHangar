"""Tests for aircraft photo upload, serve, delete, and reorder."""

from io import BytesIO
from pathlib import Path

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftPhoto,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _upload_dir(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def _make_user_tenant(app, email="photo@example.com", slug="photo-hangar"):
    with app.app_context():
        tenant = Tenant(name="Photo Hangar", slug=slug)
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def _add_aircraft(app, tenant_id, reg="OO-PH"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=reg, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _login(app, client, email="photo@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_photo(app, aircraft_id, slug="photo-hangar", reg="OO-PH", order=1):
    """Create a file in the app's upload dir + a DB row; return photo_id."""
    fname = f"{order:02d}-abc123.jpg"
    photo_dir = _upload_dir(app) / slug / reg / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    (photo_dir / fname).write_bytes(b"\xff\xd8\xff")
    relpath = f"{slug}/{reg}/photos/{fname}"
    with app.app_context():
        p = AircraftPhoto(
            aircraft_id=aircraft_id,
            filename=relpath,
            original_filename="photo.jpg",
            sort_order=order,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


# ── Model: cover_photo property ───────────────────────────────────────────────


class TestCoverPhotoProperty:
    def test_cover_photo_returns_first_photo(self, app):
        uid, tid = _make_user_tenant(app, "cp@x.com", "cp-hangar")
        ac_id = _add_aircraft(app, tid, "OO-CP")
        p1_id = _add_photo(app, ac_id, "cp-hangar", "OO-CP", order=1)
        _add_photo(app, ac_id, "cp-hangar", "OO-CP", order=2)

        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.cover_photo is not None
            assert ac.cover_photo.id == p1_id

    def test_cover_photo_none_when_no_photos(self, app):
        uid, tid = _make_user_tenant(app, "cpn@x.com", "cpn-hangar")
        ac_id = _add_aircraft(app, tid, "OO-CN")
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.cover_photo is None


# ── Upload ────────────────────────────────────────────────────────────────────


class TestUploadPhoto:
    def test_upload_single_photo(self, app, client):
        uid, tid = _make_user_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={"photos": (BytesIO(b"\xff\xd8\xff"), "cover.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            photos = AircraftPhoto.query.filter_by(aircraft_id=ac_id).all()
            assert len(photos) == 1
            assert photos[0].sort_order == 1
            assert photos[0].original_filename == "cover.jpg"

    def test_upload_multiple_photos_numbered_in_order(self, app, client):
        uid, tid = _make_user_tenant(app, "up2@x.com", "up2-hangar")
        ac_id = _add_aircraft(app, tid, "OO-U2")
        _login(app, client, "up2@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={
                "photos": [
                    (BytesIO(b"\xff\xd8\xff"), "a.jpg", "image/jpeg"),
                    (BytesIO(b"\xff\xd8\xff"), "b.png", "image/png"),
                ]
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            photos = (
                AircraftPhoto.query.filter_by(aircraft_id=ac_id)
                .order_by(AircraftPhoto.sort_order)
                .all()
            )
            assert len(photos) == 2
            assert photos[0].sort_order == 1
            assert photos[1].sort_order == 2

    def test_upload_appends_after_existing(self, app, client):
        uid, tid = _make_user_tenant(app, "app@x.com", "app-hangar")
        ac_id = _add_aircraft(app, tid, "OO-AP")
        _add_photo(app, ac_id, "app-hangar", "OO-AP", order=1)
        _login(app, client, "app@x.com")

        client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={"photos": (BytesIO(b"\xff\xd8\xff"), "new.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        with app.app_context():
            orders = [
                p.sort_order
                for p in AircraftPhoto.query.filter_by(aircraft_id=ac_id).all()
            ]
            assert sorted(orders) == [1, 2]

    def test_upload_no_files_rejected(self, app, client):
        uid, tid = _make_user_tenant(app, "nf@x.com", "nf-hangar")
        ac_id = _add_aircraft(app, tid, "OO-NF")
        _login(app, client, "nf@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            assert AircraftPhoto.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_upload_unsupported_format_rejected(self, app, client):
        uid, tid = _make_user_tenant(app, "fmt@x.com", "fmt-hangar")
        ac_id = _add_aircraft(app, tid, "OO-FT")
        _login(app, client, "fmt@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={"photos": (BytesIO(b"GIF89a"), "anim.gif", "image/gif")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        rv2 = client.get(rv.headers["Location"])
        assert (
            b"gif" in rv2.data.lower()
            or b"format" in rv2.data.lower()
            or b"unsupported" in rv2.data.lower()
        )
        with app.app_context():
            assert AircraftPhoto.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_file_with_no_filename_skipped(self, app, client):
        """Empty-named FileStorage entry is silently skipped; valid file is saved."""
        uid, tid = _make_user_tenant(app, "ef@x.com", "ef-hangar")
        ac_id = _add_aircraft(app, tid, "OO-EF")
        _login(app, client, "ef@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={
                "photos": [
                    (BytesIO(b""), "", "application/octet-stream"),
                    (BytesIO(b"\xff\xd8\xff"), "real.jpg", "image/jpeg"),
                ]
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            assert AircraftPhoto.query.filter_by(aircraft_id=ac_id).count() == 1


# ── Serve ─────────────────────────────────────────────────────────────────────


class TestServePhoto:
    def test_serve_returns_image_bytes(self, app, client):
        uid, tid = _make_user_tenant(app, "srv@x.com", "srv-hangar")
        ac_id = _add_aircraft(app, tid, "OO-SV")
        p_id = _add_photo(app, ac_id, "srv-hangar", "OO-SV")
        _login(app, client, "srv@x.com")

        rv = client.get(f"/aircraft/{ac_id}/photos/{p_id}/img")
        assert rv.status_code == 200
        assert rv.data[:3] == b"\xff\xd8\xff"

    def test_serve_wrong_aircraft_404(self, app, client):
        uid, tid = _make_user_tenant(app, "srv2@x.com", "srv2-hangar")
        ac1_id = _add_aircraft(app, tid, "OO-S1")
        ac2_id = _add_aircraft(app, tid, "OO-S2")
        p_id = _add_photo(app, ac1_id, "srv2-hangar", "OO-S1")
        _login(app, client, "srv2@x.com")

        rv = client.get(f"/aircraft/{ac2_id}/photos/{p_id}/img")
        assert rv.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


class TestDeletePhoto:
    def test_delete_removes_db_row_and_trashes_file(self, app, client):
        uid, tid = _make_user_tenant(app, "del@x.com", "del-hangar")
        ac_id = _add_aircraft(app, tid, "OO-DL")
        p_id = _add_photo(app, ac_id, "del-hangar", "OO-DL", order=1)
        _login(app, client, "del@x.com")

        rv = client.post(f"/aircraft/{ac_id}/photos/{p_id}/delete")
        assert rv.status_code == 302

        with app.app_context():
            assert db.session.get(AircraftPhoto, p_id) is None

        trash = _upload_dir(app) / "_trash"
        assert trash.exists() and any(trash.iterdir())

    def test_delete_renumbers_remaining(self, app, client):
        uid, tid = _make_user_tenant(app, "ren@x.com", "ren-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RN")
        p1_id = _add_photo(app, ac_id, "ren-hangar", "OO-RN", order=1)
        _add_photo(app, ac_id, "ren-hangar", "OO-RN", order=2)
        _add_photo(app, ac_id, "ren-hangar", "OO-RN", order=3)
        _login(app, client, "ren@x.com")

        client.post(f"/aircraft/{ac_id}/photos/{p1_id}/delete")

        with app.app_context():
            photos = (
                AircraftPhoto.query.filter_by(aircraft_id=ac_id)
                .order_by(AircraftPhoto.sort_order)
                .all()
            )
            assert [p.sort_order for p in photos] == [1, 2]

    def test_delete_wrong_aircraft_404(self, app, client):
        uid, tid = _make_user_tenant(app, "d404@x.com", "d404-hangar")
        ac1_id = _add_aircraft(app, tid, "OO-D1")
        ac2_id = _add_aircraft(app, tid, "OO-D2")
        p_id = _add_photo(app, ac1_id, "d404-hangar", "OO-D1")
        _login(app, client, "d404@x.com")

        rv = client.post(f"/aircraft/{ac2_id}/photos/{p_id}/delete")
        assert rv.status_code == 404

    def test_trash_collision_gets_unique_suffix(self, app, client):
        """If the trash destination already exists, a suffix is added."""
        uid, tid = _make_user_tenant(app, "trc@x.com", "trc-hangar")
        ac_id = _add_aircraft(app, tid, "OO-TC")
        p_id = _add_photo(app, ac_id, "trc-hangar", "OO-TC", order=1)
        _login(app, client, "trc@x.com")

        # Pre-populate trash with a file of the same base name
        trash = _upload_dir(app) / "_trash"
        trash.mkdir(exist_ok=True)
        (trash / "01-abc123.jpg").write_bytes(b"old")
        pre_count = len(list(trash.iterdir()))

        client.post(f"/aircraft/{ac_id}/photos/{p_id}/delete")

        assert len(list(trash.iterdir())) == pre_count + 1


# ── Reorder ───────────────────────────────────────────────────────────────────


class TestReorderPhotos:
    def test_reorder_updates_sort_order_and_renames_files(self, app, client):
        uid, tid = _make_user_tenant(app, "ro@x.com", "ro-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RO")
        p1_id = _add_photo(app, ac_id, "ro-hangar", "OO-RO", order=1)
        p2_id = _add_photo(app, ac_id, "ro-hangar", "OO-RO", order=2)
        _login(app, client, "ro@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": [str(p2_id), str(p1_id)]},
        )
        assert rv.status_code == 204

        with app.app_context():
            p1 = db.session.get(AircraftPhoto, p1_id)
            p2 = db.session.get(AircraftPhoto, p2_id)
            assert p2.sort_order == 1
            assert p1.sort_order == 2

    def test_reorder_invalid_ids_returns_400(self, app, client):
        uid, tid = _make_user_tenant(app, "ri@x.com", "ri-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RI")
        _add_photo(app, ac_id, "ri-hangar", "OO-RI", order=1)
        _login(app, client, "ri@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": ["99999"]},
        )
        assert rv.status_code == 400

    def test_reorder_non_integer_ids_returns_400(self, app, client):
        uid, tid = _make_user_tenant(app, "rv@x.com", "rv-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RV")
        _add_photo(app, ac_id, "rv-hangar", "OO-RV", order=1)
        _login(app, client, "rv@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": ["not-a-number"]},
        )
        assert rv.status_code == 400

    def test_reorder_rename_oserror_keeps_old_filename(self, app, client, monkeypatch):
        """If os.rename fails during renumber, old filename is preserved."""
        uid, tid = _make_user_tenant(app, "rr@x.com", "rr-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RR")
        p1_id = _add_photo(app, ac_id, "rr-hangar", "OO-RR", order=1)
        p2_id = _add_photo(app, ac_id, "rr-hangar", "OO-RR", order=2)
        _login(app, client, "rr@x.com")

        with app.app_context():
            old_p1_filename = db.session.get(AircraftPhoto, p1_id).filename

        monkeypatch.setattr(
            "os.rename", lambda *a: (_ for _ in ()).throw(OSError("busy"))
        )
        client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": [str(p2_id), str(p1_id)]},
        )

        with app.app_context():
            p1 = db.session.get(AircraftPhoto, p1_id)
            assert p1.sort_order == 2
            assert p1.filename == old_p1_filename


# ── _trash_photo_file edge cases ──────────────────────────────────────────────


class TestTrashPhotoFile:
    def test_trash_noop_when_file_missing(self, app):
        from aircraft.routes import _trash_photo_file  # pyright: ignore[reportMissingImports]

        with app.app_context():
            _trash_photo_file("does-not-exist/photo.jpg")
        # No _trash dir created means no file was moved
        assert not (_upload_dir(app) / "_trash" / "photo.jpg").exists()

    def test_trash_oserror_is_swallowed(self, app, monkeypatch):
        from aircraft.routes import _trash_photo_file  # pyright: ignore[reportMissingImports]

        photo_dir = _upload_dir(app) / "t-hangar" / "OO-TE" / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        (photo_dir / "01-abc.jpg").write_bytes(b"\xff\xd8\xff")

        monkeypatch.setattr(
            "os.rename", lambda *a: (_ for _ in ()).throw(OSError("locked"))
        )
        with app.app_context():
            _trash_photo_file("t-hangar/OO-TE/photos/01-abc.jpg")


# ── _photo_folder helper ──────────────────────────────────────────────────────


class TestPhotoFolderHelper:
    def test_returns_correct_path(self, app):
        from aircraft.routes import _photo_folder  # pyright: ignore[reportMissingImports]

        original = app.config.get("UPLOAD_FOLDER")
        try:
            app.config["UPLOAD_FOLDER"] = "/uploads"
            with app.test_request_context():
                result = _photo_folder(app, "my-hangar", "OO-AB")
            assert result == "/uploads/my-hangar/OO-AB/photos"
        finally:
            app.config["UPLOAD_FOLDER"] = original
