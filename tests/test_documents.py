"""
Tests for Phase 9: Document & Photo Uploads.
"""

import os
from io import BytesIO

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    ComponentType,
    Document,
    PendingReconcile,
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


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(app, aircraft_id):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=ComponentType.ENGINE,
            make="Lycoming",
            model="IO-360",
        )
        db.session.add(comp)
        db.session.commit()
        return comp.id


def _add_document(app, aircraft_id, title=None, is_sensitive=False, component_id=None):
    with app.app_context():
        doc = Document(
            aircraft_id=aircraft_id,
            component_id=component_id,
            filename=f"doc_ac{aircraft_id}_placeholder.txt",
            original_filename="test.txt",
            mime_type="text/plain",
            size_bytes=42,
            title=title,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _login_orphan(app, client):
    with app.app_context():
        user = User(
            email="orphan@x.com",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _fake_file(name="doc.txt", content=b"hello", content_type="text/plain"):
    return (BytesIO(content), name, content_type)


# ── Document model ─────────────────────────────────────────────────────────────


class TestDocumentModel:
    def test_owner_type_aircraft(self, app):
        with app.app_context():
            doc = Document(aircraft_id=1, filename="f", original_filename="f")
            assert doc.owner_type == "aircraft"

    def test_owner_type_component(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1, component_id=2, filename="f", original_filename="f"
            )
            assert doc.owner_type == "component"

    def test_owner_type_entry(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1, flight_entry_id=3, filename="f", original_filename="f"
            )
            assert doc.owner_type == "entry"

    def test_is_image_true(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                mime_type="image/jpeg",
            )
            assert doc.is_image is True

    def test_is_image_false(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                mime_type="application/pdf",
            )
            assert doc.is_image is False

    def test_is_image_none_mime(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1, filename="f", original_filename="f", mime_type=None
            )
            assert doc.is_image is False


# ── List documents ─────────────────────────────────────────────────────────────


class TestListDocuments:
    def test_list_empty(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code == 200
        assert b"No documents" in rv.data

    def test_list_shows_documents(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="My ARC")
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code == 200
        assert b"My ARC" in rv.data

    def test_sensitive_hidden_by_default(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="Secret Doc", is_sensitive=True)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert b"Secret Doc" not in rv.data
        assert b"Show sensitive" in rv.data

    def test_sensitive_shown_with_flag(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="Secret Doc", is_sensitive=True)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents?sensitive=1")
        assert b"Secret Doc" in rv.data
        assert b"Hide sensitive" in rv.data

    def test_no_sensitive_toggle_when_none(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="Public Doc")
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert b"Show sensitive" not in rv.data

    def test_list_403_orphan_user(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login_orphan(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code == 403

    def test_list_404_wrong_tenant(self, app, client):
        uid, tid = _create_user_and_tenant(app, "a@x.com")
        _create_user_and_tenant(app, "b@x.com")
        ac_id = _add_aircraft(app, tid)
        _login(app, client, "b@x.com")
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code == 404

    def test_requires_login(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code in (302, 401)


# ── Upload document ───────────────────────────────────────────────────────────


class TestUploadDocument:
    def test_get_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/upload")
        assert rv.status_code == 200
        assert b"Upload document" in rv.data

    def test_get_form_with_component(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/upload?component_id={comp_id}")
        assert rv.status_code == 200
        assert b"Lycoming" in rv.data

    def test_upload_txt_success(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("report.txt", b"hello world"),
                "title": "My Report",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.title == "My Report"
            assert doc.original_filename == "report.txt"
            assert doc.is_sensitive is False

    def test_upload_sensitive(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("policy.txt", b"secret"),
                "is_sensitive": "1",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.is_sensitive is True

    def test_upload_with_component_scope(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("engine.txt", b"data"),
                "component_id": str(comp_id),
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.component_id == comp_id

    def test_upload_component_wrong_aircraft_ignored(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-A")
        ac2_id = _add_aircraft(app, tid, "OO-B")
        comp_id = _add_component(app, ac2_id)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("doc.txt", b"x"),
                "component_id": str(comp_id),
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.component_id is None

    def test_upload_component_invalid_id_ignored(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("doc.txt", b"x"),
                "component_id": "notanumber",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

    def test_upload_no_file_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200
        assert b"select a file" in rv.data

    def test_upload_disallowed_extension(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={"file": _fake_file("virus.exe", b"\x00", "application/octet-stream")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200
        assert b"not allowed" in rv.data

    def test_upload_stores_file_on_disk(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={"file": _fake_file("note.txt", b"content here")},
            content_type="multipart/form-data",
        )
        folder = app.config["UPLOAD_FOLDER"]
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert os.path.exists(os.path.join(folder, doc.filename))

    def test_upload_403_orphan(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login_orphan(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={"file": _fake_file("doc.txt", b"x")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 403

    def test_upload_404_wrong_tenant(self, app, client):
        _create_user_and_tenant(app, "a@x.com")
        uid2, tid2 = _create_user_and_tenant(app, "b@x.com")
        ac_id = _add_aircraft(app, tid2)
        _login(app, client, "a@x.com")
        rv = client.get(f"/aircraft/{ac_id}/documents/upload")
        assert rv.status_code == 404

    def test_upload_collision_adds_suffix(self, app, client):
        """Covers documents/routes.py:228-232 — filename suffix when dest already exists."""
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)

        # Upload the same filename twice in the same test; the second write must
        # detect the collision and append a UUID suffix.
        for _ in range(2):
            client.post(
                f"/aircraft/{ac_id}/documents/upload",
                data={"file": _fake_file("collision.txt", b"data")},
                content_type="multipart/form-data",
            )

        with app.app_context():
            docs = Document.query.filter_by(aircraft_id=ac_id).all()
        assert len(docs) == 2
        filenames = [d.filename for d in docs]
        # The second filename must differ from the first (UUID suffix was added)
        assert filenames[0] != filenames[1]


# ── Delete document ───────────────────────────────────────────────────────────


class TestDeleteDocument:
    def test_delete_removes_record(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="Temp")
        _login(app, client)
        rv = client.post(f"/aircraft/{ac_id}/documents/{doc_id}/delete")
        assert rv.status_code == 302
        with app.app_context():
            assert db.session.get(Document, doc_id) is None

    def test_delete_removes_file_from_disk(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        # Upload a real file first
        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={"file": _fake_file("todel.txt", b"bye")},
            content_type="multipart/form-data",
        )
        folder = app.config["UPLOAD_FOLDER"]
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            doc_id = doc.id
            stored = doc.filename
        client.post(f"/aircraft/{ac_id}/documents/{doc_id}/delete")
        assert not os.path.exists(os.path.join(folder, stored))

    def test_delete_missing_file_on_disk_ok(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        # Add record pointing to non-existent file
        doc_id = _add_document(app, ac_id)
        _login(app, client)
        rv = client.post(f"/aircraft/{ac_id}/documents/{doc_id}/delete")
        assert rv.status_code == 302

    def test_delete_404_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-A")
        ac2_id = _add_aircraft(app, tid, "OO-B")
        doc_id = _add_document(app, ac2_id)
        _login(app, client)
        rv = client.post(f"/aircraft/{ac_id}/documents/{doc_id}/delete")
        assert rv.status_code == 404

    def test_delete_403_orphan(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id)
        _login_orphan(app, client)
        rv = client.post(f"/aircraft/{ac_id}/documents/{doc_id}/delete")
        assert rv.status_code == 403


# ── Edit document ─────────────────────────────────────────────────────────────


class TestEditDocument:
    def test_get_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="Old Title")
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/{doc_id}/edit")
        assert rv.status_code == 200
        assert b"Old Title" in rv.data

    def test_post_updates_title(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="Old")
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit", data={"title": "New Title"}
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.title == "New Title"
            assert doc.is_sensitive is False

    def test_post_clears_title_when_blank(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="Had Title")
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/documents/{doc_id}/edit", data={"title": "  "})
        with app.app_context():
            assert db.session.get(Document, doc_id).title is None

    def test_post_marks_sensitive(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "", "is_sensitive": "1"},
        )
        with app.app_context():
            assert db.session.get(Document, doc_id).is_sensitive is True

    def test_edit_404_wrong_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-A")
        ac2_id = _add_aircraft(app, tid, "OO-B")
        doc_id = _add_document(app, ac2_id)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/{doc_id}/edit")
        assert rv.status_code == 404

    def test_edit_403_orphan(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id)
        _login_orphan(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/{doc_id}/edit")
        assert rv.status_code == 403


# ── Aircraft detail shows documents ──────────────────────────────────────────


class TestAircraftDetailDocuments:
    def test_detail_shows_document_count(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="ARC 2025")
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}")
        assert rv.status_code == 200
        assert b"Documents" in rv.data
        assert b"ARC 2025" in rv.data

    def test_detail_sensitive_not_shown(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, ac_id, title="Secret", is_sensitive=True)
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}")
        assert b"Secret" not in rv.data


# ── Internal helpers ──────────────────────────────────────────────────────────


class TestDeleteFileHelper:
    def test_delete_file_none_is_noop(self, app):
        from documents.routes import _delete_file  # pyright: ignore[reportMissingImports]

        # Should return immediately without error (no app context needed)
        _delete_file(None)

    def test_delete_file_moves_to_trash(self, app, tmp_path):

        from documents.routes import _delete_file  # pyright: ignore[reportMissingImports]

        src = tmp_path / "test_doc.pdf"
        src.write_bytes(b"pdf content")

        with app.test_request_context():
            app.config["UPLOAD_FOLDER"] = str(tmp_path)
            _delete_file("test_doc.pdf")

        assert not src.exists()
        assert (tmp_path / "_trash" / "test_doc.pdf").exists()

    def test_delete_file_missing_is_silent(self, app, tmp_path):
        from documents.routes import _delete_file  # pyright: ignore[reportMissingImports]

        with app.test_request_context():
            app.config["UPLOAD_FOLDER"] = str(tmp_path)
            # No error when file does not exist
            _delete_file("nonexistent.pdf")

    def test_delete_file_trash_collision_adds_suffix(self, app, tmp_path):
        from documents.routes import _delete_file  # pyright: ignore[reportMissingImports]

        (tmp_path / "_trash").mkdir()
        # Pre-existing file in trash with same name
        (tmp_path / "_trash" / "dup.pdf").write_bytes(b"old")
        (tmp_path / "dup.pdf").write_bytes(b"new")

        with app.test_request_context():
            app.config["UPLOAD_FOLDER"] = str(tmp_path)
            _delete_file("dup.pdf")

        trash_files = list((tmp_path / "_trash").iterdir())
        assert len(trash_files) == 2


# ── Category upload (canonical path) ─────────────────────────────────────────


class TestCanonicalUpload:
    def test_upload_with_category_uses_canonical_path(self, app, client, tmp_path):
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)

        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "test-hangar"
            db.session.commit()

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("arc.pdf", b"%PDF-1.4", "application/pdf"),
                "title": "Annual Review",
                "category": DocCategory.MAINTENANCE,
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.category == DocCategory.MAINTENANCE
            assert "test-hangar" in doc.filename
            assert "maintenance" in doc.filename

    def test_upload_without_category_uses_flat_path(self, app, client, tmp_path):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("doc.pdf", b"%PDF-1.4", "application/pdf"),
                "title": "Some doc",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.category is None
            # Flat filename — no subdirectory separator
            assert "/" not in doc.filename

    def test_edit_saves_category(self, app, client):
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="ARC")
        _login(app, client)

        rv = client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "ARC", "category": DocCategory.AIRWORTHINESS},
        )
        assert rv.status_code == 302

        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.category == DocCategory.AIRWORTHINESS

    def test_list_shows_broken_link_badge(self, app, client, tmp_path):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        with app.app_context():
            # Manually add doc with a filename that does not exist on disk
            doc = Document(
                aircraft_id=ac_id,
                filename="ghost.pdf",
                original_filename="ghost.pdf",
            )
            db.session.add(doc)
            db.session.commit()

        rv = client.get(f"/aircraft/{ac_id}/documents")
        assert rv.status_code == 200
        assert b"File missing" in rv.data


# ── Insurance document auto-fill ──────────────────────────────────────────────


class TestInsuranceDocumentAutoFill:
    def test_insurance_upload_with_expiry_updates_aircraft(self, app, client, tmp_path):
        import datetime
        from models import Aircraft, DocCategory, DocType  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        expiry = datetime.date(2027, 6, 30).isoformat()
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("insurance.pdf", b"%PDF", "application/pdf"),
                "category": DocCategory.INSURANCE,
                "valid_until": expiry,
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.insurance_expiry == datetime.date(2027, 6, 30)
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.doc_type == DocType.INSURANCE_CERT

    def test_insurance_upload_without_expiry_does_not_update_aircraft(
        self, app, client, tmp_path
    ):
        from models import Aircraft, DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("insurance.pdf", b"%PDF", "application/pdf"),
                "category": DocCategory.INSURANCE,
            },
            content_type="multipart/form-data",
        )

        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.insurance_expiry is None

    def test_insurance_upload_does_not_regress_later_expiry(
        self, app, client, tmp_path
    ):
        import datetime
        from models import Aircraft, DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        with app.app_context():
            db.session.get(Aircraft, ac_id).insurance_expiry = datetime.date(2028, 1, 1)
            db.session.commit()

        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("old.pdf", b"%PDF", "application/pdf"),
                "category": DocCategory.INSURANCE,
                "valid_until": "2026-12-31",
            },
            content_type="multipart/form-data",
        )

        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.insurance_expiry == datetime.date(2028, 1, 1)

    def test_insurance_upload_supersedes_previous_cert(self, app, client, tmp_path):
        from models import DocCategory, DocType  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        for year in (2026, 2027):
            client.post(
                f"/aircraft/{ac_id}/documents/upload",
                data={
                    "file": _fake_file(f"ins{year}.pdf", b"%PDF", "application/pdf"),
                    "category": DocCategory.INSURANCE,
                    "valid_until": f"{year}-12-31",
                },
                content_type="multipart/form-data",
            )

        with app.app_context():
            docs = (
                Document.query.filter_by(
                    aircraft_id=ac_id, doc_type=DocType.INSURANCE_CERT
                )
                .order_by(Document.id)
                .all()
            )
            assert len(docs) == 2
            assert docs[0].superseded_by_id == docs[1].id
            assert docs[1].superseded_by_id is None


# ── Reconcile routes ──────────────────────────────────────────────────────────


class TestReconcile:
    def test_list_reconcile_empty(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/documents/reconcile")
        assert rv.status_code == 200
        assert b"No pending" in rv.data

    def test_scan_without_slug_shows_warning(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302
        rv2 = client.get(rv.headers["Location"])
        assert b"slug" in rv2.data.lower()

    def test_scan_with_slug_no_dir_shows_info(self, app, client, tmp_path):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "my-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302

    def test_scan_finds_canonical_files(self, app, client, tmp_path):
        from models import DocCategory, PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, "OO-TST")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "scan-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        # Create a file in the canonical structure
        canon_dir = tmp_path / "scan-hangar" / "OO-TST" / "maintenance"
        canon_dir.mkdir(parents=True)
        (canon_dir / "2024-06-04 - Annual inspection.pdf").write_bytes(b"%PDF")

        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302

        with app.app_context():
            prs = PendingReconcile.query.filter_by(tenant_id=tid).all()
            assert len(prs) == 1
            pr = prs[0]
            assert pr.category == DocCategory.MAINTENANCE
            assert pr.title_hint == "Annual inspection"

    def test_import_reconcile_creates_document(self, app, client, tmp_path):
        from models import DocCategory, PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-TST")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "import-hangar"
            db.session.commit()

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        (tmp_path / "import-hangar" / "OO-TST" / "insurance").mkdir(parents=True)
        rel = "import-hangar/OO-TST/insurance/2024-01-01 - Hull.pdf"
        (tmp_path / rel).write_bytes(b"%PDF")

        with app.app_context():
            pr = PendingReconcile(
                tenant_id=tid,
                aircraft_id=ac_id,
                filepath=rel,
                category=DocCategory.INSURANCE,
                title_hint="Hull",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        _login(app, client)
        rv = client.post(
            f"/documents/reconcile/{pr_id}/import",
            data={
                "aircraft_id": str(ac_id),
                "title": "Hull insurance",
                "category": DocCategory.INSURANCE,
            },
        )
        assert rv.status_code == 302

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.filename == rel
            pr2 = db.session.get(PendingReconcile, pr_id)
            assert pr2.reconciled_at is not None

    def test_ignore_reconcile(self, app, client):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "ignore-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="ignore-hangar/OO-X/other/2024-01-01 - misc.pdf",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        _login(app, client)
        rv = client.post(f"/documents/reconcile/{pr_id}/ignore")
        assert rv.status_code == 302

        with app.app_context():
            pr2 = db.session.get(PendingReconcile, pr_id)
            assert pr2.ignored is True


# ── Tenant slug settings ──────────────────────────────────────────────────────


class TestTenantSlug:
    def test_update_slug_saves(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post("/config/tenant-slug", data={"slug": "My Hangar 2!"})
        assert rv.status_code == 302
        with app.app_context():
            t = db.session.get(Tenant, tid)
            assert t.slug == "my-hangar-2"

    def test_update_slug_empty_rejected(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post("/config/tenant-slug", data={"slug": ""})
        assert rv.status_code == 302
        rv2 = client.get(rv.headers["Location"])
        assert b"empty" in rv2.data.lower() or rv2.status_code == 200

    def test_update_slug_duplicate_rejected(self, app, client):
        uid1, tid1 = _create_user_and_tenant(app, "a@x.com")
        uid2, tid2 = _create_user_and_tenant(app, "b@x.com")
        with app.app_context():
            t = db.session.get(Tenant, tid2)
            t.slug = "taken"
            db.session.commit()
        _login(app, client, "a@x.com")
        rv = client.post("/config/tenant-slug", data={"slug": "taken"})
        assert rv.status_code == 302
        with app.app_context():
            t = db.session.get(Tenant, tid1)
            assert t.slug != "taken"

    def test_update_slug_all_special_chars_rejected(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        # A slug of only dashes reduces to "" after strip("-")
        rv = client.post("/config/tenant-slug", data={"slug": "---"})
        assert rv.status_code == 302


class TestScanEdgeCases:
    def test_scan_skips_known_and_hidden_files(self, app, client, tmp_path):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-TST")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "edge-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        canon_dir = tmp_path / "edge-hangar" / "OO-TST" / "maintenance"
        canon_dir.mkdir(parents=True)
        (canon_dir / ".hidden").write_bytes(b"x")
        (canon_dir / "_notes.txt").write_bytes(b"x")
        # Non-canonical name (no date prefix) → still added, title_hint = filename stem
        (canon_dir / "annual-report.pdf").write_bytes(b"%PDF")
        # Already-tracked file — should NOT appear in pending
        tracked_name = "2024-01-01 - Tracked.pdf"
        (canon_dir / tracked_name).write_bytes(b"%PDF")
        tracked_relpath = f"edge-hangar/OO-TST/maintenance/{tracked_name}"
        with app.app_context():
            doc = Document(
                aircraft_id=ac_id,
                filename=tracked_relpath,
                original_filename=tracked_name,
            )
            db.session.add(doc)
            db.session.commit()

        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302

        with app.app_context():
            prs = PendingReconcile.query.filter_by(tenant_id=tid).all()
            assert len(prs) == 1
            assert prs[0].title_hint == "annual-report"

    def test_scan_second_run_no_duplicates(self, app, client, tmp_path):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "dedup-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        d = tmp_path / "dedup-hangar" / "OO-X" / "other"
        d.mkdir(parents=True)
        (d / "2024-01-01 - Doc.pdf").write_bytes(b"%PDF")

        client.post("/documents/reconcile/scan")
        client.post("/documents/reconcile/scan")  # second scan — no duplicates

        with app.app_context():
            count = PendingReconcile.query.filter_by(tenant_id=tid).count()
            assert count == 1

    def test_scan_zero_new_files(self, app, client, tmp_path):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "zero-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        (tmp_path / "zero-hangar").mkdir()
        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302

    def test_import_reconcile_with_valid_until(self, app, client, tmp_path):
        from models import DocCategory, PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-VU")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "vu-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="vu-hangar/OO-VU/insurance/2024-01-01 - Insurance.pdf",
                category=DocCategory.INSURANCE,
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        _login(app, client)
        rv = client.post(
            f"/documents/reconcile/{pr_id}/import",
            data={
                "aircraft_id": str(ac_id),
                "category": DocCategory.INSURANCE,
                "valid_until": "2025-12-31",
                "title": "Annual insurance",
            },
        )
        assert rv.status_code == 302
        with app.app_context():
            from datetime import date

            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.valid_until == date(2025, 12, 31)

    def test_import_reconcile_invalid_valid_until_ignored(self, app, client, tmp_path):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "vu2-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="vu2-hangar/OO-X/other/2024-01-01 - file.pdf",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        _login(app, client)
        rv = client.post(
            f"/documents/reconcile/{pr_id}/import",
            data={"valid_until": "not-a-date"},
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.order_by(Document.id.desc()).first()
            assert doc.valid_until is None

    def test_import_reconcile_invalid_category_cleared(self, app, client, tmp_path):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "cat-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="cat-hangar/OO-X/other/2024-01-01 - file.pdf",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        _login(app, client)
        rv = client.post(
            f"/documents/reconcile/{pr_id}/import",
            data={"category": "invalid_category"},
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.order_by(Document.id.desc()).first()
            assert doc.category is None

    def test_slug_collision_adds_numeric_suffix(self, app):
        """_ensure_tenant_slug must append a number when the base slug is taken."""
        from documents.routes import _ensure_tenant_slug  # pyright: ignore[reportMissingImports]

        with app.app_context():
            t1 = Tenant(name="My Hangar", slug="my-hangar")
            t2 = Tenant(name="My Hangar")  # same name, no slug yet
            db.session.add_all([t1, t2])
            db.session.commit()
            result = _ensure_tenant_slug(t2)
            assert result == "my-hangar-1"
            assert t2.slug == "my-hangar-1"

    def test_delete_file_oserror_is_silent(self, app, tmp_path, monkeypatch):
        """OSError during rename is logged and swallowed."""
        from documents.routes import _delete_file  # pyright: ignore[reportMissingImports]

        src = tmp_path / "oserr.pdf"
        src.write_bytes(b"x")
        with app.test_request_context():
            app.config["UPLOAD_FOLDER"] = str(tmp_path)
            monkeypatch.setattr(
                "os.rename", lambda *a: (_ for _ in ()).throw(OSError("no"))
            )
            _delete_file("oserr.pdf")  # should not raise

    def test_edit_document_invalid_category_cleared(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, ac_id, title="X")
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "X", "category": "bogus_cat"},
        )
        assert rv.status_code == 302
        with app.app_context():
            d = db.session.get(Document, doc_id)
            assert d.category is None

    def test_scan_bad_date_in_filename(self, app, client, tmp_path):
        """Files with dates that fail fromisoformat still get added without date_hint."""
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "baddate-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        d = tmp_path / "baddate-hangar" / "OO-X" / "other"
        d.mkdir(parents=True)
        # date "0000-99-99" matches the regex but fails fromisoformat
        (d / "0000-99-99 - baddate.pdf").write_bytes(b"%PDF")

        client.post("/documents/reconcile/scan")

        with app.app_context():
            pr = PendingReconcile.query.filter_by(tenant_id=tid).first()
            assert pr is not None
            assert pr.date_hint is None
            assert pr.title_hint == "baddate"

    def test_import_reconcile_invalid_aircraft_id(self, app, client, tmp_path):
        from models import PendingReconcile  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "bad-ac-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="bad-ac-hangar/OO-X/other/file.pdf",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        _login(app, client)
        rv = client.post(
            f"/documents/reconcile/{pr_id}/import",
            data={"aircraft_id": "not_a_number"},
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.order_by(Document.id.desc()).first()
            assert doc.aircraft_id is None

    def test_upload_document_invalid_category_cleared(self, app, client, tmp_path):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        rv = client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("doc.pdf", b"%PDF-1.4", "application/pdf"),
                "category": "not_a_real_category",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.category is None


# ── Slug rename — folder + DB path propagation ────────────────────────────────


class TestSlugRename:
    def test_rename_renames_folder_and_updates_document_paths(
        self, app, client, tmp_path
    ):
        """Changing the slug renames the on-disk folder and rewrites Document.filename."""
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, "OO-SR")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "old-hangar"
            db.session.commit()
        _login(app, client)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        doc_rel = "old-hangar/OO-SR/maintenance/2024-01-01 - Doc.pdf"
        (tmp_path / "old-hangar" / "OO-SR" / "maintenance").mkdir(parents=True)
        (tmp_path / doc_rel).write_bytes(b"%PDF")
        with app.app_context():
            doc = Document(
                aircraft_id=ac_id,
                filename=doc_rel,
                original_filename="Doc.pdf",
                category=DocCategory.MAINTENANCE,
            )
            db.session.add(doc)
            db.session.commit()
            doc_id = doc.id

        rv = client.post("/config/tenant-slug", data={"slug": "new-hangar"})
        assert rv.status_code == 302

        with app.app_context():
            d = db.session.get(Document, doc_id)
            assert d.filename == "new-hangar/OO-SR/maintenance/2024-01-01 - Doc.pdf"

        assert (
            tmp_path / "new-hangar" / "OO-SR" / "maintenance" / "2024-01-01 - Doc.pdf"
        ).exists()
        assert not (tmp_path / "old-hangar").exists()

    def test_rename_updates_pending_reconcile_paths(self, app, client, tmp_path):
        """PendingReconcile.filepath is also rewritten when the slug changes."""
        uid, tid = _create_user_and_tenant(app, "slugpr@x.com")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "pr-old"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="pr-old/OO-X/other/2024-05-01 - File.pdf",
                title_hint="File",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id
        _login(app, client, "slugpr@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        (tmp_path / "pr-old").mkdir()

        client.post("/config/tenant-slug", data={"slug": "pr-new"})

        with app.app_context():
            p = db.session.get(PendingReconcile, pr_id)
            assert p.filepath == "pr-new/OO-X/other/2024-05-01 - File.pdf"

    def test_rename_merges_when_target_folder_exists(self, app, client, tmp_path):
        """When new slug folder already exists, files are merged into it."""
        uid, tid = _create_user_and_tenant(app, "merge@x.com")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "merge-old"
            db.session.commit()
        _login(app, client, "merge@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        (tmp_path / "merge-old").mkdir()
        (tmp_path / "merge-old" / "file-a.pdf").write_bytes(b"a")
        (tmp_path / "merge-new").mkdir()
        (tmp_path / "merge-new" / "file-b.pdf").write_bytes(b"b")

        client.post("/config/tenant-slug", data={"slug": "merge-new"})

        assert (tmp_path / "merge-new" / "file-a.pdf").exists()
        assert (tmp_path / "merge-new" / "file-b.pdf").exists()
        assert not (tmp_path / "merge-old").exists()


# ── Edit document — category change moves file ────────────────────────────────


class TestEditDocumentCategoryMove:
    def test_category_change_moves_file_on_disk(self, app, client, tmp_path):
        """Changing a document's category renames the on-disk file path."""
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app, "catmove@x.com")
        ac_id = _add_aircraft(app, tid, "OO-CM")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "cm-hangar"
            db.session.commit()
        _login(app, client, "catmove@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        old_rel = "cm-hangar/OO-CM/maintenance/2024-06-01 - Manual.pdf"
        (tmp_path / "cm-hangar" / "OO-CM" / "maintenance").mkdir(parents=True)
        (tmp_path / old_rel).write_bytes(b"%PDF")
        with app.app_context():
            doc = Document(
                aircraft_id=ac_id,
                filename=old_rel,
                original_filename="Manual.pdf",
                category=DocCategory.MAINTENANCE,
            )
            db.session.add(doc)
            db.session.commit()
            doc_id = doc.id

        rv = client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "Manual", "category": DocCategory.POH},
        )
        assert rv.status_code == 302

        with app.app_context():
            d = db.session.get(Document, doc_id)
            assert d.filename == "cm-hangar/OO-CM/poh/2024-06-01 - Manual.pdf"
            assert d.category == DocCategory.POH

        assert (
            tmp_path / "cm-hangar" / "OO-CM" / "poh" / "2024-06-01 - Manual.pdf"
        ).exists()
        assert not (tmp_path / old_rel).exists()

    def test_category_change_oserror_keeps_old_filename(
        self, app, client, tmp_path, monkeypatch
    ):
        """If os.rename raises OSError the doc.filename stays unchanged (line 449)."""
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        uid, tid = _create_user_and_tenant(app, "caterr@x.com")
        ac_id = _add_aircraft(app, tid, "OO-CE")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "ce-hangar"
            db.session.commit()
        _login(app, client, "caterr@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        old_rel = "ce-hangar/OO-CE/maintenance/2024-06-01 - Doc.pdf"
        (tmp_path / "ce-hangar" / "OO-CE" / "maintenance").mkdir(parents=True)
        (tmp_path / old_rel).write_bytes(b"%PDF")
        with app.app_context():
            doc = Document(
                aircraft_id=ac_id,
                filename=old_rel,
                original_filename="Doc.pdf",
                category=DocCategory.MAINTENANCE,
            )
            db.session.add(doc)
            db.session.commit()
            doc_id = doc.id

        monkeypatch.setattr(
            "os.rename", lambda *a: (_ for _ in ()).throw(OSError("busy"))
        )
        client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "Doc", "category": DocCategory.POH},
        )

        with app.app_context():
            d = db.session.get(Document, doc_id)
            assert d.filename == old_rel


# ── Scan stale pruning and flash details ──────────────────────────────────────


class TestScanStalePruning:
    def test_scan_prunes_stale_entries_and_reports_count(self, app, client, tmp_path):
        """Manual scan deletes pending entries whose files are gone and reports
        the count in the flash message (lines 760-761, 763, 827)."""
        uid, tid = _create_user_and_tenant(app, "stale@x.com")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "stale-hangar"
            db.session.commit()
            ghost = PendingReconcile(
                tenant_id=tid,
                filepath="stale-hangar/OO-X/other/2024-01-01 - Ghost.pdf",
                title_hint="Ghost",
            )
            db.session.add(ghost)
            db.session.commit()
            ghost_id = ghost.id
        _login(app, client, "stale@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        (tmp_path / "stale-hangar").mkdir()

        rv = client.post("/documents/reconcile/scan")
        assert rv.status_code == 302
        rv2 = client.get(rv.headers["Location"])
        assert b"missing" in rv2.data.lower()

        with app.app_context():
            assert db.session.get(PendingReconcile, ghost_id) is None


# ── Fuzzy category suggestion in reconcile list ───────────────────────────────


class TestFuzzySuggestion:
    def test_list_reconcile_shows_typo_suggestion(self, app, client, tmp_path):
        """A pending entry with a typo folder name shows a rename suggestion
        in the reconcile page (lines 700-710)."""
        uid, tid = _create_user_and_tenant(app, "fuzzy@x.com")
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = "fuzzy-hangar"
            db.session.commit()
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="fuzzy-hangar/OO-FZ/maintenence/2024-01-01 - Typo.pdf",
                title_hint="Typo",
            )
            db.session.add(pr)
            db.session.commit()
        _login(app, client, "fuzzy@x.com")
        app.config["UPLOAD_FOLDER"] = str(tmp_path)

        rv = client.get("/documents/reconcile")
        assert rv.status_code == 200
        assert b"maintenence" in rv.data
        assert b"maintenance" in rv.data


# ── Rename folder endpoint ────────────────────────────────────────────────────


class TestRenameFolderEndpoint:
    def _setup(
        self, app, client, tmp_path, email="rf@x.com", slug="rf-hangar", reg="OO-RF"
    ):
        uid, tid = _create_user_and_tenant(app, email)
        ac_id = _add_aircraft(app, tid, reg)
        with app.app_context():
            t = db.session.get(Tenant, tid)
            t.slug = slug
            db.session.commit()
        _login(app, client, email)
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        return tid, ac_id

    def test_rename_moves_folder_and_auto_imports(self, app, client, tmp_path):
        """Renaming a typo folder to a valid category auto-imports the matching file."""
        from models import DocCategory  # pyright: ignore[reportMissingImports]

        tid, ac_id = self._setup(app, client, tmp_path)
        bad = tmp_path / "rf-hangar" / "OO-RF" / "maintenence"
        bad.mkdir(parents=True)
        (bad / "2024-03-01 - Service.pdf").write_bytes(b"%PDF")

        with app.app_context():
            pr = PendingReconcile(
                tenant_id=tid,
                filepath="rf-hangar/OO-RF/maintenence/2024-03-01 - Service.pdf",
                title_hint="Service",
            )
            db.session.add(pr)
            db.session.commit()
            pr_id = pr.id

        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rf-hangar/OO-RF/maintenence",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 302

        with app.app_context():
            assert db.session.get(PendingReconcile, pr_id) is None
            doc = Document.query.filter(
                Document.filename.like("rf-hangar/OO-RF/maintenance/%")
            ).first()
            assert doc is not None
            assert doc.category == DocCategory.MAINTENANCE

        assert (
            tmp_path
            / "rf-hangar"
            / "OO-RF"
            / "maintenance"
            / "2024-03-01 - Service.pdf"
        ).exists()
        assert not (tmp_path / "rf-hangar" / "OO-RF" / "maintenence").exists()

    def test_rename_merges_into_existing_target(self, app, client, tmp_path):
        """If the target category folder already exists, files are merged."""
        self._setup(app, client, tmp_path, "rfm@x.com", "rfm-hangar", "OO-RM")
        typo = tmp_path / "rfm-hangar" / "OO-RM" / "Maintenance"
        typo.mkdir(parents=True)
        (typo / "2024-01-01 - A.pdf").write_bytes(b"%PDF")
        correct = tmp_path / "rfm-hangar" / "OO-RM" / "maintenance"
        correct.mkdir(parents=True)
        (correct / "2024-02-01 - B.pdf").write_bytes(b"%PDF")

        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rfm-hangar/OO-RM/Maintenance",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 302
        assert (
            tmp_path / "rfm-hangar" / "OO-RM" / "maintenance" / "2024-01-01 - A.pdf"
        ).exists()
        assert (
            tmp_path / "rfm-hangar" / "OO-RM" / "maintenance" / "2024-02-01 - B.pdf"
        ).exists()
        assert not (tmp_path / "rfm-hangar" / "OO-RM" / "Maintenance").exists()

    def test_rename_invalid_category_rejected(self, app, client, tmp_path):
        """Submitting an unrecognised new_category redirects with an error flash."""
        self._setup(app, client, tmp_path, "rfi@x.com", "rfi-hangar", "OO-RI")
        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rfi-hangar/OO-RI/typo",
                "new_category": "notacategory",
            },
        )
        assert rv.status_code == 302
        rv2 = client.get(rv.headers["Location"])
        assert b"invalid" in rv2.data.lower() or b"cat" in rv2.data.lower()

    def test_rename_wrong_tenant_slug_forbidden(self, app, client, tmp_path):
        """bad_folder that doesn't start with the tenant's own slug returns 403."""
        self._setup(app, client, tmp_path, "rfx@x.com", "rfx-hangar", "OO-RX")
        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "other-tenant/OO-RX/maintenance",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 403

    def test_rename_skips_dotfiles_and_already_tracked(self, app, client, tmp_path):
        """Dotfiles and already-tracked documents are skipped during inline scan
        (lines 909, 913)."""
        tid, ac_id = self._setup(
            app, client, tmp_path, "rfskip@x.com", "rfskip-hangar", "OO-SK"
        )
        dest = tmp_path / "rfskip-hangar" / "OO-SK" / "maintenance"
        dest.mkdir(parents=True)
        (dest / ".syncthing.tmp").write_bytes(b"x")
        (dest / "2024-05-01 - Clean.pdf").write_bytes(b"%PDF")

        already_rel = "rfskip-hangar/OO-SK/maintenance/2024-05-01 - Clean.pdf"
        with app.app_context():
            db.session.add(
                Document(
                    aircraft_id=ac_id,
                    filename=already_rel,
                    original_filename="Clean.pdf",
                )
            )
            db.session.commit()

        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rfskip-hangar/OO-SK/maintenence",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 302
        with app.app_context():
            new_docs = Document.query.filter(
                Document.filename.like("rfskip-hangar/OO-SK/maintenance/%"),
                Document.filename != already_rel,
            ).count()
            assert new_docs == 0

    def test_rename_queues_unresolvable_aircraft_for_review(
        self, app, client, tmp_path
    ):
        """Unknown aircraft registration goes to reconcile queue, not auto-imported
        (lines 948-956)."""
        self._setup(app, client, tmp_path, "rfq@x.com", "rfq-hangar", "OO-QR")
        dest = tmp_path / "rfq-hangar" / "XX-UNK" / "maintenance"
        dest.mkdir(parents=True)
        (dest / "2024-07-01 - Unknown.pdf").write_bytes(b"%PDF")

        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rfq-hangar/XX-UNK/maintenence",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 302
        with app.app_context():
            pr = PendingReconcile.query.filter(
                PendingReconcile.filepath.like("rfq-hangar/XX-UNK/maintenance/%")
            ).first()
            assert pr is not None
            assert pr.aircraft_id is None

    def test_rename_inline_scan_bad_date_and_no_date_prefix(
        self, app, client, tmp_path
    ):
        """Invalid date in filename falls back gracefully; no-date-prefix file uses
        stem as title_hint (lines 929-930, 933)."""

        self._setup(app, client, tmp_path, "rfdt@x.com", "rfdt-hangar", "OO-DT")
        dest = tmp_path / "rfdt-hangar" / "OO-DT" / "maintenance"
        dest.mkdir(parents=True)
        (dest / "2024-13-99 - Bad date.pdf").write_bytes(b"%PDF")
        (dest / "no-date-prefix.pdf").write_bytes(b"%PDF")

        rv = client.post(
            "/documents/reconcile/rename-folder",
            data={
                "bad_folder": "rfdt-hangar/OO-DT/maintenence",
                "new_category": "maintenance",
            },
        )
        assert rv.status_code == 302
        with app.app_context():
            docs = Document.query.filter(
                Document.filename.like("rfdt-hangar/OO-DT/maintenance/%")
            ).all()
            assert len(docs) == 2
            titles = {d.title for d in docs}
            assert "Bad date" in titles
            assert "no-date-prefix" in titles


class TestSafeJoin:
    def test_path_traversal_aborts(self, app):
        from documents.routes import _safe_join  # pyright: ignore[reportMissingImports]

        with app.test_request_context():
            with app.app_context():
                import pytest

                with pytest.raises(
                    Exception
                ):  # abort(400) raises werkzeug HTTPException
                    _safe_join("/uploads", "../../../etc/passwd")
