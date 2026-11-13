"""Symbolic links in the uploads folder never reach outside it (audit S07).

Everything runs in temporary folders: an "uploads" folder, and a sibling
"outside" folder holding a secret that links inside the uploads try to
reach. No real uploads are touched.
"""

import io
import os
import zipfile
from pathlib import Path

import pytest
from werkzeug.exceptions import HTTPException

from tests.test_aircraft_photos import (
    _add_aircraft,
    _add_photo,
    _login,
    _make_user_tenant,
)

OUTSIDE_BYTES = b"outside the uploads folder"


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    uploads = tmp_path / "uploads"
    outside = tmp_path / "outside"
    (uploads / "tenant" / "OO-ABC" / "documents").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_bytes(OUTSIDE_BYTES)
    (uploads / "tenant" / "OO-ABC" / "documents" / "kept.pdf").write_bytes(b"pdf")
    # A sibling whose name starts like the uploads folder's.
    (tmp_path / "uploads-other").mkdir()
    (tmp_path / "uploads-other" / "x.txt").write_bytes(OUTSIDE_BYTES)
    return uploads, outside


def _link(path: Path, target: Path) -> None:
    path.symlink_to(target, target_is_directory=target.is_dir())


class TestResolveInside:
    def test_ordinary_paths_resolve_inside(self, tree):
        from services.safe_paths import resolve_inside

        uploads, _ = tree
        assert resolve_inside(str(uploads), "tenant/OO-ABC/documents/kept.pdf") == str(
            (uploads / "tenant/OO-ABC/documents/kept.pdf").resolve()
        )
        assert resolve_inside(str(uploads), "tenant", "new", "file.txt") is not None
        assert resolve_inside(str(uploads)) == str(uploads.resolve())

    @pytest.mark.parametrize(
        "parts",
        [
            ("../outside/secret.txt",),
            ("tenant", "..", "..", "outside", "secret.txt"),
            ("../uploads-other/x.txt",),
        ],
    )
    def test_traversal_and_sibling_prefixes_are_refused(self, tree, parts):
        from services.safe_paths import resolve_inside

        uploads, _ = tree
        assert resolve_inside(str(uploads), *parts) is None

    def test_absolute_parts_are_refused(self, tree):
        from services.safe_paths import resolve_inside

        uploads, outside = tree
        assert resolve_inside(str(uploads), str(outside / "secret.txt")) is None

    def test_links_to_outside_are_refused(self, tree):
        from services.safe_paths import resolve_inside

        uploads, outside = tree
        _link(uploads / "tenant" / "escape", outside)
        _link(uploads / "tenant" / "secret.pdf", outside / "secret.txt")
        assert resolve_inside(str(uploads), "tenant/escape/secret.txt") is None
        assert resolve_inside(str(uploads), "tenant/escape/new.txt") is None
        assert resolve_inside(str(uploads), "tenant/secret.pdf") is None

    def test_links_staying_inside_resolve_to_their_target(self, tree):
        from services.safe_paths import resolve_inside

        uploads, _ = tree
        _link(uploads / "alias", uploads / "tenant")
        assert resolve_inside(str(uploads), "alias/OO-ABC/documents/kept.pdf") == str(
            (uploads / "tenant/OO-ABC/documents/kept.pdf").resolve()
        )


class TestRegularFiles:
    def test_links_are_never_listed_or_followed(self, tree):
        from services.safe_paths import regular_files

        uploads, outside = tree
        _link(uploads / "tenant" / "escape", outside)
        _link(uploads / "tenant" / "secret.pdf", outside / "secret.txt")
        _link(
            uploads / "tenant" / "alias.pdf",
            uploads / "tenant/OO-ABC/documents/kept.pdf",
        )
        (uploads / "tmp_import").mkdir()
        (uploads / "tmp_import" / "draft.xlsx").write_bytes(b"x")
        found = {
            os.path.relpath(p, uploads)
            for p in regular_files(
                str(uploads), skip_top_dirs=frozenset({"tmp_import"})
            )
        }
        assert found == {os.path.join("tenant", "OO-ABC", "documents", "kept.pdf")}


class TestSendInside:
    def test_serves_regular_files_and_refuses_the_rest(self, app, tree):
        from services.safe_paths import send_inside

        uploads, outside = tree
        _link(uploads / "tenant" / "secret.pdf", outside / "secret.txt")
        with app.test_request_context():
            response = send_inside(str(uploads), "tenant/OO-ABC/documents/kept.pdf")
            response.direct_passthrough = False
            assert response.get_data() == b"pdf"
            for relpath in (
                "tenant/secret.pdf",
                "tenant",
                "../outside/secret.txt",
                "nope",
            ):
                with pytest.raises(HTTPException) as refused:
                    send_inside(str(uploads), relpath)
                assert refused.value.code == 404


class TestApplicationBoundaries:
    def test_document_paths_through_an_outside_link_are_refused(self, app, tree):
        from documents.routes import _safe_join

        uploads, outside = tree
        _link(uploads / "tenant" / "OO-ABC" / "linked", outside)
        with app.test_request_context(), pytest.raises(HTTPException) as refused:
            _safe_join(str(uploads), "tenant", "OO-ABC", "linked", "upload.pdf")
        assert refused.value.code == 400

    def test_a_photo_that_became_an_outside_link_is_not_served(self, app, client):
        _uid, tid = _make_user_tenant(app)
        acid = _add_aircraft(app, tid)
        photo_id = _add_photo(app, acid)
        _login(app, client)
        uploads = Path(app.config["UPLOAD_FOLDER"])
        photo = uploads / "photo-hangar" / "OO-PH" / "photos" / "01-abc123.jpg"
        assert client.get(f"/aircraft/{acid}/photos/{photo_id}/img").status_code == 200
        outside = uploads.parent / (uploads.name + "-outside.jpg")
        outside.write_bytes(OUTSIDE_BYTES)
        photo.unlink()
        _link(photo, outside)
        try:
            for suffix in ("img", "thumb"):
                response = client.get(f"/aircraft/{acid}/photos/{photo_id}/{suffix}")
                assert response.status_code == 404
                assert OUTSIDE_BYTES not in response.data
        finally:
            outside.unlink()

    def test_backups_leave_links_out(self, tree):
        from config.routes import _add_uploads_to_zip

        uploads, outside = tree
        _link(uploads / "tenant" / "escape", outside)
        _link(uploads / "tenant" / "secret.pdf", outside / "secret.txt")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            _add_uploads_to_zip(zf, str(uploads))
        with zipfile.ZipFile(buffer) as zf:
            assert zf.namelist() == ["uploads/tenant/OO-ABC/documents/kept.pdf"]
            assert all(OUTSIDE_BYTES not in zf.read(name) for name in zf.namelist())

    def test_gps_archive_zip_skips_paths_leaving_the_folder(self, tree):
        from services.gps_archive import write_zip

        uploads, outside = tree
        _link(uploads / "tenant" / "log.gpx", outside / "secret.txt")
        buffer = io.BytesIO()
        written = write_zip(
            str(uploads),
            [
                "tenant/log.gpx",
                "../outside/secret.txt",
                "tenant/OO-ABC/documents/kept.pdf",
            ],
            buffer,
        )
        assert written == 1
        with zipfile.ZipFile(buffer) as zf:
            assert zf.namelist() == ["kept.pdf"]
