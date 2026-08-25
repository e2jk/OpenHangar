"""Tests for Phase 40: AMP spreadsheet import routes (upload/review/commit/history/rollback)."""

import io

import openpyxl
import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    ComponentType,
    MaintenanceImportBatch,
    MaintenanceTrigger,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


def _create_user_and_tenant(app, email="pilot@example.com", role=Role.ADMIN):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-PNH"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Robin", model="DR400"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(app, aircraft_id, comp_type=ComponentType.ENGINE):
    with app.app_context():
        c = Component(
            aircraft_id=aircraft_id, type=comp_type, make="Continental", model="CD-155"
        )
        db.session.add(c)
        db.session.commit()
        return c.id


def _make_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


_VALID_ROWS = [
    ["Category", "Task description", "Reference", "Action", "Interval", "Notes"],
    [
        "Maintenance due to repetitive ADs",
        "AD compliance check",
        "AD 2023-0048",
        "INSPECTION",
        "100FH / 12MO",
        "",
    ],
    ["", "Engine 100 hr inspection", "OM-02-02", "", "100FH", ""],
    ["", "Undecided item", "", "", "PENDING", "PENDING SHOP INPUT"],
]


class TestAuthGuard:
    def test_upload_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance/import")
        assert r.status_code == 302

    def test_upload_403_for_viewer(self, app, client):
        _uid, tid = _create_user_and_tenant(app, role=Role.VIEWER)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/import")
        assert r.status_code == 403


class TestUpload:
    def test_get_shows_upload_form(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/import")
        assert r.status_code == 200
        assert b"Import Maintenance Schedule" in r.data

    def test_post_no_file_is_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/maintenance/import", data={})
        assert r.status_code == 422
        assert b"select a file" in r.data

    def test_post_wrong_extension_is_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(b"not excel"), "sched.csv")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 422
        assert b"Unsupported format" in r.data

    def test_post_unparseable_workbook_is_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        data = _make_xlsx([["Foo", "Bar"], ["a", "b"]])
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 422
        assert b"No matching header row" in r.data

    def test_post_valid_file_shows_review(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        data = _make_xlsx(_VALID_ROWS)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        assert b"AD compliance check" in r.data
        assert b"Engine 100 hr inspection" in r.data
        assert b"Undecided item" in r.data
        assert b"Needs review" in r.data

    def test_post_valid_file_suggests_engine_component(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_component(app, acid, comp_type=ComponentType.ENGINE)
        _login(app, client)
        data = _make_xlsx(_VALID_ROWS)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        assert b"selected" in r.data

    def test_post_file_too_large_is_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        big = b"x" * (10 * 1024 * 1024 + 1)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(big), "sched.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 422
        assert b"too large" in r.data

    def test_post_no_data_rows_is_error(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        data = _make_xlsx([["Task description", "Interval"]])
        r = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 422
        assert b"No task rows found" in r.data

    def test_cleanup_swallows_oserror_on_remove(self, app, client, monkeypatch):
        """Covers the defensive except branch in _cleanup_amp_import_tmp —
        mirrors the same swallow-and-log pattern as the pilot logbook
        import's tmp-file cleanup."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        data = _make_xlsx(_VALID_ROWS)
        client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )

        import os as _os

        monkeypatch.setattr(
            _os,
            "remove",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")),
        )
        # A second upload triggers _cleanup_amp_import_tmp for the first one.
        client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )


class TestCommit:
    def _upload(self, client, acid, rows=None):
        data = _make_xlsx(rows or _VALID_ROWS)
        return client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "sched.xlsx")},
            content_type="multipart/form-data",
        )

    def test_commit_without_session_redirects(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import/commit",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/import" in r.headers["Location"]

    def test_commit_creates_triggers_and_batch(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        self._upload(client, acid)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import/commit",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            batch = MaintenanceImportBatch.query.filter_by(aircraft_id=acid).first()
            assert batch is not None
            assert batch.row_count == 3
            assert batch.needs_review_count == 1
            assert batch.source_filename == "sched.xlsx"
            triggers = MaintenanceTrigger.query.filter_by(
                import_batch_id=batch.id
            ).all()
            assert len(triggers) == 3
            by_name = {t.name: t for t in triggers}
            ad_row = by_name["AD compliance check"]
            assert ad_row.category == "Maintenance due to repetitive ADs"
            assert ad_row.reference == "AD 2023-0048"
            assert ad_row.action == "INSPECTION"
            assert float(ad_row.interval_hours) == 100.0
            assert ad_row.interval_days == 360
            assert ad_row.needs_review is False
            pending_row = by_name["Undecided item"]
            assert pending_row.needs_review is True
            assert pending_row.due_date is None
            assert pending_row.due_engine_hours is None

    def test_commit_applies_component_override(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, comp_type=ComponentType.PROPELLER)
        _login(app, client)
        self._upload(client, acid)
        client.post(
            f"/aircraft/{acid}/maintenance/import/commit",
            data={"component_id_0": str(cid)},
        )
        with app.app_context():
            batch = MaintenanceImportBatch.query.filter_by(aircraft_id=acid).first()
            t = MaintenanceTrigger.query.filter_by(
                import_batch_id=batch.id, name="AD compliance check"
            ).first()
            assert t.component_id == cid

    def test_commit_uses_suggested_component_when_no_override(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        cid = _add_component(app, acid, comp_type=ComponentType.ENGINE)
        _login(app, client)
        self._upload(client, acid)
        client.post(f"/aircraft/{acid}/maintenance/import/commit", data={})
        with app.app_context():
            batch = MaintenanceImportBatch.query.filter_by(aircraft_id=acid).first()
            t = MaintenanceTrigger.query.filter_by(
                import_batch_id=batch.id, name="Engine 100 hr inspection"
            ).first()
            assert t.component_id == cid
            assert t.hours_basis == "engine"

    def test_commit_missing_tmp_file_redirects(self, app, client):
        import os

        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        self._upload(client, acid)
        with client.session_transaction() as sess:
            tmp_path = sess["amp_import"]["tmp_path"]
        os.remove(tmp_path)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import/commit",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/maintenance/import")
        with client.session_transaction() as sess:
            assert "amp_import" not in sess

    def test_commit_wrong_session_aircraft_redirects_to_upload(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid1 = _add_aircraft(app, tid, registration="OO-AA1")
        acid2 = _add_aircraft(app, tid, registration="OO-AA2")
        _login(app, client)
        self._upload(client, acid1)
        r = client.post(
            f"/aircraft/{acid2}/maintenance/import/commit",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/maintenance/import")
        assert "OO-AA2" in r.headers["Location"]

    def test_commit_invalid_component_override_falls_back_to_unscoped(
        self, app, client
    ):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        self._upload(client, acid)
        client.post(
            f"/aircraft/{acid}/maintenance/import/commit",
            data={"component_id_0": "not-a-number"},
        )
        with app.app_context():
            batch = MaintenanceImportBatch.query.filter_by(aircraft_id=acid).first()
            t = MaintenanceTrigger.query.filter_by(
                import_batch_id=batch.id, name="AD compliance check"
            ).first()
            assert t.component_id is None

    def test_commit_clears_session(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        self._upload(client, acid)
        client.post(f"/aircraft/{acid}/maintenance/import/commit", data={})
        with client.session_transaction() as sess:
            assert "amp_import" not in sess


class TestHistoryAndRollback:
    def test_history_lists_batches(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                MaintenanceImportBatch(
                    aircraft_id=acid,
                    source_filename="old-import.xlsx",
                    row_count=5,
                    needs_review_count=1,
                )
            )
            db.session.commit()
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/import/history")
        assert r.status_code == 200
        assert b"old-import.xlsx" in r.data

    def test_history_empty_state(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/import/history")
        assert b"No imports yet" in r.data

    def test_rollback_removes_batch_and_triggers(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            batch = MaintenanceImportBatch(
                aircraft_id=acid,
                source_filename="x.xlsx",
                row_count=1,
                needs_review_count=0,
            )
            db.session.add(batch)
            db.session.flush()
            db.session.add(
                MaintenanceTrigger(
                    aircraft_id=acid,
                    name="Imported item",
                    trigger_type="calendar",
                    import_batch_id=batch.id,
                )
            )
            db.session.commit()
            batch_id = batch.id
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/maintenance/import/{batch_id}/rollback",
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(MaintenanceImportBatch, batch_id) is None
            assert (
                MaintenanceTrigger.query.filter_by(import_batch_id=batch_id).count()
                == 0
            )

    def test_rollback_404_for_batch_belonging_to_another_aircraft(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid_mine = _add_aircraft(app, tid, registration="OO-MINE")
        acid_other = _add_aircraft(app, tid, registration="OO-OTHER")
        with app.app_context():
            batch = MaintenanceImportBatch(
                aircraft_id=acid_other, source_filename="x.xlsx", row_count=0
            )
            db.session.add(batch)
            db.session.commit()
            batch_id = batch.id
        _login(app, client)
        r = client.post(f"/aircraft/{acid_mine}/maintenance/import/{batch_id}/rollback")
        assert r.status_code == 404

    def test_rollback_404_for_other_tenant_aircraft(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.post(f"/aircraft/{other_acid}/maintenance/import/1/rollback")
        assert r.status_code == 404
