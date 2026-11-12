"""Tests for aircraft photo upload, serve, delete, and reorder."""

from io import BytesIO
from pathlib import Path

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pytest
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
            password_hash=_pw_hash.hash("pw"),
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


def _add_photo(
    app, aircraft_id, slug="photo-hangar", reg="OO-PH", order=1, suffix="abc123"
):
    """Create a file in the app's upload dir + a DB row; return photo_id.

    suffix defaults to a fixed value like a real upload's random hex suffix
    would never collide with -- fine when a test only cares about sort_order,
    but pass distinct suffixes for photos in the same reorder test, or their
    "NN-" prefix swap collides on Windows (os.rename refuses to overwrite an
    existing destination there) and one photo silently keeps its old name.
    """
    fname = f"{order:02d}-{suffix}.jpg"
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
        _uid, tid = _make_user_tenant(app, "cp@x.com", "cp-hangar")
        ac_id = _add_aircraft(app, tid, "OO-CP")
        p1_id = _add_photo(app, ac_id, "cp-hangar", "OO-CP", order=1)
        _add_photo(app, ac_id, "cp-hangar", "OO-CP", order=2)

        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.cover_photo is not None
            assert ac.cover_photo.id == p1_id

    def test_cover_photo_none_when_no_photos(self, app):
        _uid, tid = _make_user_tenant(app, "cpn@x.com", "cpn-hangar")
        ac_id = _add_aircraft(app, tid, "OO-CN")
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.cover_photo is None


# ── Upload ────────────────────────────────────────────────────────────────────


class TestUploadPhoto:
    def test_upload_single_photo(self, app, client):
        _uid, tid = _make_user_tenant(app)
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
        _uid, tid = _make_user_tenant(app, "up2@x.com", "up2-hangar")
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
        _uid, tid = _make_user_tenant(app, "app@x.com", "app-hangar")
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
        _uid, tid = _make_user_tenant(app, "nf@x.com", "nf-hangar")
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
        _uid, tid = _make_user_tenant(app, "fmt@x.com", "fmt-hangar")
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
        _uid, tid = _make_user_tenant(app, "ef@x.com", "ef-hangar")
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

    def test_upload_generates_real_thumbnail(self, app, client):
        """A genuinely decodable image produces an actual resized-JPEG
        thumbnail file on disk, not just the best-effort failure path."""
        import PIL.Image

        buf = BytesIO()
        PIL.Image.new("RGB", (1600, 1200), color=(10, 20, 30)).save(buf, "JPEG")
        buf.seek(0)

        _uid, tid = _make_user_tenant(app, "rtn@x.com", "rtn-hangar")
        ac_id = _add_aircraft(app, tid, "OO-TN")
        _login(app, client, "rtn@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={"photos": (buf, "real.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            from aircraft.routes import (
                thumb_path_for,  # pyright: ignore[reportMissingImports]
            )

            photo = AircraftPhoto.query.filter_by(aircraft_id=ac_id).one()
            thumb_rel = thumb_path_for(photo.filename)
        thumb_path = _upload_dir(app) / thumb_rel
        assert thumb_path.exists()
        with PIL.Image.open(thumb_path) as thumb_img:
            assert thumb_img.format == "JPEG"
            assert max(thumb_img.size) <= 800

    def test_thumbnail_generation_failure_is_swallowed(self, app, client, monkeypatch):
        """A thumbnail-generation error (corrupt upload, decode failure, disk
        error, ...) must not block the upload itself -- only the thumbnail
        file is missing afterwards, and serve_photo_thumb falls back."""
        import PIL.Image

        def boom(*a, **kw):
            raise OSError("cannot identify image file")

        monkeypatch.setattr(PIL.Image, "open", boom)

        _uid, tid = _make_user_tenant(app, "tgf@x.com", "tgf-hangar")
        ac_id = _add_aircraft(app, tid, "OO-TG")
        _login(app, client, "tgf@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/upload",
            data={"photos": (BytesIO(b"\xff\xd8\xff"), "cover.jpg", "image/jpeg")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            from aircraft.routes import (
                thumb_path_for,  # pyright: ignore[reportMissingImports]
            )

            photos = AircraftPhoto.query.filter_by(aircraft_id=ac_id).all()
            assert len(photos) == 1
            thumb_rel = thumb_path_for(photos[0].filename)
        assert not (_upload_dir(app) / thumb_rel).exists()


# ── Serve ─────────────────────────────────────────────────────────────────────


class TestServePhoto:
    def test_serve_returns_image_bytes(self, app, client):
        _uid, tid = _make_user_tenant(app, "srv@x.com", "srv-hangar")
        ac_id = _add_aircraft(app, tid, "OO-SV")
        p_id = _add_photo(app, ac_id, "srv-hangar", "OO-SV")
        _login(app, client, "srv@x.com")

        rv = client.get(f"/aircraft/{ac_id}/photos/{p_id}/img")
        assert rv.status_code == 200
        assert rv.data[:3] == b"\xff\xd8\xff"

    def test_serve_wrong_aircraft_404(self, app, client):
        _uid, tid = _make_user_tenant(app, "srv2@x.com", "srv2-hangar")
        ac1_id = _add_aircraft(app, tid, "OO-S1")
        ac2_id = _add_aircraft(app, tid, "OO-S2")
        p_id = _add_photo(app, ac1_id, "srv2-hangar", "OO-S1")
        _login(app, client, "srv2@x.com")

        rv = client.get(f"/aircraft/{ac2_id}/photos/{p_id}/img")
        assert rv.status_code == 404


class TestServePhotoThumb:
    def test_serve_thumb_returns_thumbnail_when_present(self, app, client):
        from aircraft.routes import (
            thumb_path_for,  # pyright: ignore[reportMissingImports]
        )

        _uid, tid = _make_user_tenant(app, "sth@x.com", "sth-hangar")
        ac_id = _add_aircraft(app, tid, "OO-TH")
        p_id = _add_photo(app, ac_id, "sth-hangar", "OO-TH")
        _login(app, client, "sth@x.com")

        with app.app_context():
            photo = db.session.get(AircraftPhoto, p_id)
            thumb_rel = thumb_path_for(photo.filename)
        thumb_path = _upload_dir(app) / thumb_rel
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        # Distinguishable from the original's b"\xff\xd8\xff" so the assertion
        # below can only pass if the thumbnail (not a fallback) was served.
        thumb_path.write_bytes(b"\xff\xd8\xff\xaa\xaa")

        rv = client.get(f"/aircraft/{ac_id}/photos/{p_id}/thumb")
        assert rv.status_code == 200
        assert rv.data == b"\xff\xd8\xff\xaa\xaa"

    def test_serve_thumb_falls_back_to_original_when_missing(self, app, client):
        """A photo uploaded before thumbnailing existed (or whose generation
        failed) has no thumbnail file -- the route must still serve something."""
        _uid, tid = _make_user_tenant(app, "stf@x.com", "stf-hangar")
        ac_id = _add_aircraft(app, tid, "OO-TF")
        p_id = _add_photo(app, ac_id, "stf-hangar", "OO-TF")
        _login(app, client, "stf@x.com")

        rv = client.get(f"/aircraft/{ac_id}/photos/{p_id}/thumb")
        assert rv.status_code == 200
        assert rv.data[:3] == b"\xff\xd8\xff"

    def test_serve_thumb_wrong_aircraft_404(self, app, client):
        _uid, tid = _make_user_tenant(app, "st4@x.com", "st4-hangar")
        ac1_id = _add_aircraft(app, tid, "OO-T1")
        ac2_id = _add_aircraft(app, tid, "OO-T2")
        p_id = _add_photo(app, ac1_id, "st4-hangar", "OO-T1")
        _login(app, client, "st4@x.com")

        rv = client.get(f"/aircraft/{ac2_id}/photos/{p_id}/thumb")
        assert rv.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


class TestDeletePhoto:
    def test_delete_removes_db_row_and_trashes_file(self, app, client):
        _uid, tid = _make_user_tenant(app, "del@x.com", "del-hangar")
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
        _uid, tid = _make_user_tenant(app, "ren@x.com", "ren-hangar")
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
        _uid, tid = _make_user_tenant(app, "d404@x.com", "d404-hangar")
        ac1_id = _add_aircraft(app, tid, "OO-D1")
        ac2_id = _add_aircraft(app, tid, "OO-D2")
        p_id = _add_photo(app, ac1_id, "d404-hangar", "OO-D1")
        _login(app, client, "d404@x.com")

        rv = client.post(f"/aircraft/{ac2_id}/photos/{p_id}/delete")
        assert rv.status_code == 404

    def test_trash_collision_gets_unique_suffix(self, app, client):
        """If the trash destination already exists, a suffix is added."""
        _uid, tid = _make_user_tenant(app, "trc@x.com", "trc-hangar")
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
        _uid, tid = _make_user_tenant(app, "ro@x.com", "ro-hangar")
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
        _uid, tid = _make_user_tenant(app, "ri@x.com", "ri-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RI")
        _add_photo(app, ac_id, "ri-hangar", "OO-RI", order=1)
        _login(app, client, "ri@x.com")

        rv = client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": ["99999"]},
        )
        assert rv.status_code == 400

    def test_reorder_non_integer_ids_returns_400(self, app, client):
        _uid, tid = _make_user_tenant(app, "rv@x.com", "rv-hangar")
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
        _uid, tid = _make_user_tenant(app, "rr@x.com", "rr-hangar")
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

    def test_reorder_renames_thumbnail_alongside_original(self, app, client):
        """A photo's thumbnail file must keep the same numeric prefix as its
        (renumbered) original, or thumb_path_for(new filename) stops finding
        it and the photo silently falls back to full-size until re-uploaded."""
        from aircraft.routes import (
            thumb_path_for,  # pyright: ignore[reportMissingImports]
        )

        _uid, tid = _make_user_tenant(app, "rth@x.com", "rth-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RH")
        p1_id = _add_photo(app, ac_id, "rth-hangar", "OO-RH", order=1, suffix="p1abc")
        p2_id = _add_photo(app, ac_id, "rth-hangar", "OO-RH", order=2, suffix="p2xyz")
        _login(app, client, "rth@x.com")

        with app.app_context():
            p1_filename = db.session.get(AircraftPhoto, p1_id).filename
        old_thumb = _upload_dir(app) / thumb_path_for(p1_filename)
        old_thumb.parent.mkdir(parents=True, exist_ok=True)
        old_thumb.write_bytes(b"\xff\xd8\xff")

        client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": [str(p2_id), str(p1_id)]},
        )

        with app.app_context():
            p1 = db.session.get(AircraftPhoto, p1_id)
            assert p1.sort_order == 2
        new_thumb = _upload_dir(app) / thumb_path_for(p1.filename)
        assert new_thumb.exists()
        assert not old_thumb.exists()

    def test_reorder_thumbnail_rename_oserror_is_swallowed(
        self, app, client, monkeypatch
    ):
        """If renaming the thumbnail fails during renumber (disk error,
        permissions, ...), that failure must not surface as a 500 -- the
        original file's own rename still succeeds independently."""
        from aircraft.routes import (
            thumb_path_for,  # pyright: ignore[reportMissingImports]
        )

        _uid, tid = _make_user_tenant(app, "rte@x.com", "rte-hangar")
        ac_id = _add_aircraft(app, tid, "OO-RE")
        p1_id = _add_photo(app, ac_id, "rte-hangar", "OO-RE", order=1, suffix="p1abc")
        p2_id = _add_photo(app, ac_id, "rte-hangar", "OO-RE", order=2, suffix="p2xyz")
        _login(app, client, "rte@x.com")

        with app.app_context():
            p1_filename = db.session.get(AircraftPhoto, p1_id).filename
        thumb = _upload_dir(app) / thumb_path_for(p1_filename)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\xff\xd8\xff")

        import os as _os

        real_rename = _os.rename

        def flaky_rename(src, dst):
            if "thumbs" in str(src).replace("\\", "/"):
                raise OSError("busy")
            return real_rename(src, dst)

        monkeypatch.setattr("os.rename", flaky_rename)

        rv = client.post(
            f"/aircraft/{ac_id}/photos/reorder",
            data={"photo_order[]": [str(p2_id), str(p1_id)]},
        )
        assert rv.status_code == 204

        with app.app_context():
            p1 = db.session.get(AircraftPhoto, p1_id)
            assert p1.sort_order == 2
            assert p1.filename.split("/")[-1].startswith("02-")


# ── _trash_photo_file edge cases ──────────────────────────────────────────────


class TestTrashPhotoFile:
    def test_trash_noop_when_file_missing(self, app):
        from aircraft.routes import (
            _trash_photo_file,  # pyright: ignore[reportMissingImports]
        )

        with app.app_context():
            _trash_photo_file("does-not-exist/photo.jpg")
        # No _trash dir created means no file was moved
        assert not (_upload_dir(app) / "_trash" / "photo.jpg").exists()

    def test_trash_oserror_is_swallowed(self, app, monkeypatch):
        from aircraft.routes import (
            _trash_photo_file,  # pyright: ignore[reportMissingImports]
        )

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
        from aircraft.routes import (
            _photo_folder,  # pyright: ignore[reportMissingImports]
        )

        original = app.config.get("UPLOAD_FOLDER")
        try:
            app.config["UPLOAD_FOLDER"] = "/uploads"
            with app.test_request_context():
                result = _photo_folder(app, "my-hangar", "OO-AB")
            assert result == "/uploads/my-hangar/OO-AB/photos"
        finally:
            app.config["UPLOAD_FOLDER"] = original


@pytest.mark.parametrize("variant", ["img", "thumb"])
def test_private_photo_rechecks_authentication_and_grants(app, client, variant):
    from models import UserAircraftAccess

    uid, tid = _make_user_tenant(app, "private@x.com", "private-hangar")
    acid = _add_aircraft(app, tid, "OO-PR")
    photo_id = _add_photo(app, acid, "private-hangar", "OO-PR")
    with app.app_context():
        membership = TenantUser.query.filter_by(user_id=uid).one()
        membership.role = Role.PILOT
        db.session.add(UserAircraftAccess(user_id=uid, aircraft_id=acid))
        db.session.commit()
    _login(app, client, "private@x.com")
    url = f"/aircraft/{acid}/photos/{photo_id}/{variant}"
    first = client.get(url)
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store, private"
    assert "Expires" not in first.headers
    etag = first.headers["ETag"]
    valid = client.get(url, headers={"If-None-Match": etag})
    assert valid.status_code == 304
    assert valid.headers["Cache-Control"] == "no-store, private"
    with app.app_context():
        UserAircraftAccess.query.filter_by(user_id=uid, aircraft_id=acid).delete()
        db.session.commit()
    denied = client.get(url, headers={"If-None-Match": etag})
    assert denied.status_code == 404
    assert "ETag" not in denied.headers
    client.get("/logout")
    anonymous = client.get(url, headers={"If-None-Match": etag})
    assert anonymous.status_code == 302
    assert "/login" in anonymous.location
