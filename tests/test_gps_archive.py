"""
Tests for keeping the original uploaded GPS log files on disk (they're only
stored simplified in the database):
  - archive_gps_file: dedup of identical re-uploads, no overwrite on a name
    clash
  - the aircraft and pilot GPS uploads keep every accepted file, whatever
    the parser makes of it
  - the document scan never offers app-managed folders for reconciliation
"""

import io
import os

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    PendingReconcile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from services.gps_archive import (  # pyright: ignore[reportMissingImports]
    archive_gps_file,
)


def _world(app, email):
    with app.app_context():
        tenant = Tenant(name="Archive Hangar", slug=f"archive-{email.split('@')[0]}")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        ac = Aircraft(
            tenant_id=tenant.id, registration="OO-ARC", make="Robin", model="DR401"
        )
        db.session.add(ac)
        db.session.commit()
        return user.id, tenant.slug, ac.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid
        sess["pilot_access"] = True


def _files(*names_and_bytes):
    return [(io.BytesIO(data), name) for name, data in names_and_bytes]


class TestArchiveGpsFile:
    def test_stores_under_original_name(self, tmp_path):
        path = archive_gps_file(b"abc", "log_260621_104720_EBAW.csv", str(tmp_path))
        assert path == str(tmp_path / "log_260621_104720_EBAW.csv")
        with open(path, "rb") as fh:
            assert fh.read() == b"abc"
        assert not [n for n in os.listdir(tmp_path) if n.endswith(".part")]

    def test_identical_reupload_stores_nothing_new(self, tmp_path):
        first = archive_gps_file(b"abc", "flight.gpx", str(tmp_path))
        second = archive_gps_file(b"abc", "flight.gpx", str(tmp_path))
        assert first == second
        assert os.listdir(tmp_path) == ["flight.gpx"]

    def test_name_clash_keeps_both_files(self, tmp_path):
        archive_gps_file(b"day one", "flight.gpx", str(tmp_path))
        other = archive_gps_file(b"day two", "flight.gpx", str(tmp_path))
        again = archive_gps_file(b"day two", "flight.gpx", str(tmp_path))
        assert other == again
        assert os.path.basename(other).startswith("flight-")
        assert sorted(os.listdir(tmp_path)) == sorted(
            ["flight.gpx", os.path.basename(other)]
        )
        with open(tmp_path / "flight.gpx", "rb") as fh:
            assert fh.read() == b"day one"

    def test_unusable_name_gets_a_default(self, tmp_path):
        path = archive_gps_file(b"abc", "../", str(tmp_path))
        assert os.path.basename(path) == "gps_log"


class TestUploadsKeepOriginals:
    def test_aircraft_upload_keeps_every_accepted_file(
        self, app, client, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
        uid, slug, ac_id = _world(app, "arc1@example.com")
        _login(client, uid)
        client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={
                "gps_files": _files(
                    ("log_250304_164150_EBST.csv", b"not a garmin log"),
                    ("track.gpx", b"<gpx/>"),
                    ("notes.txt", b"rejected type"),
                )
            },
            content_type="multipart/form-data",
        )
        archive = tmp_path / slug / "OO-ARC" / "gps_logs"
        assert sorted(os.listdir(archive)) == [
            "log_250304_164150_EBST.csv",
            "track.gpx",
        ]

    def test_aircraft_upload_for_other_aircraft_goes_to_pilot_archive(
        self, app, client, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
        uid, _slug, ac_id = _world(app, "arc2@example.com")
        _login(client, uid)
        client.post(
            f"/aircraft/{ac_id}/gps-import",
            data={
                "gps_files": _files(("rental.gpx", b"<gpx/>")),
                "other_aircraft": "1",
            },
            content_type="multipart/form-data",
        )
        assert os.listdir(tmp_path / "_pilots" / str(uid) / "gps_logs") == [
            "rental.gpx"
        ]

    def test_pilot_upload_keeps_files_in_pilot_archive(
        self, app, client, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
        uid, _slug, _ac_id = _world(app, "arc3@example.com")
        _login(client, uid)
        client.post(
            "/pilot/gps-import",
            data={"gps_files": _files(("skydemon.gpx", b"<gpx/>"))},
            content_type="multipart/form-data",
        )
        assert os.listdir(tmp_path / "_pilots" / str(uid) / "gps_logs") == [
            "skydemon.gpx"
        ]


class TestDocumentScanSkipsAppManagedFolders:
    def test_photos_and_gps_logs_never_offered_as_documents(
        self, app, client, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
        uid, slug, _ac_id = _world(app, "arc4@example.com")
        _login(client, uid)
        base = tmp_path / slug / "OO-ARC"
        for sub, name in (
            ("photos", "01-abc.jpg"),
            ("gps_logs", "log_250304_164150_EBST.csv"),
            ("maintenance", "2024-01-01 - Annual.pdf"),
        ):
            (base / sub).mkdir(parents=True)
            (base / sub / name).write_bytes(b"x")

        client.post("/documents/reconcile/scan")

        with app.app_context():
            tid = Tenant.query.filter_by(slug=slug).one().id
            paths = [
                pr.filepath for pr in PendingReconcile.query.filter_by(tenant_id=tid)
            ]
        assert paths == [f"{slug}/OO-ARC/maintenance/2024-01-01 - Annual.pdf"]
