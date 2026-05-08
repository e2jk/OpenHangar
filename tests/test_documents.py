"""
Tests for Phase 9: Document & Photo Uploads.
"""
import os
from io import BytesIO

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, Component, ComponentType, Document,
    Role, Tenant, TenantUser, User, db,
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
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
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
        ac = Aircraft(tenant_id=tenant_id, registration=registration,
                      make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(app, aircraft_id):
    with app.app_context():
        comp = Component(
            aircraft_id=aircraft_id,
            type=ComponentType.ENGINE,
            make="Lycoming", model="IO-360",
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
            doc = Document(aircraft_id=1, component_id=2, filename="f", original_filename="f")
            assert doc.owner_type == "component"

    def test_owner_type_entry(self, app):
        with app.app_context():
            doc = Document(aircraft_id=1, flight_entry_id=3, filename="f", original_filename="f")
            assert doc.owner_type == "entry"

    def test_is_image_true(self, app):
        with app.app_context():
            doc = Document(aircraft_id=1, filename="f", original_filename="f",
                           mime_type="image/jpeg")
            assert doc.is_image is True

    def test_is_image_false(self, app):
        with app.app_context():
            doc = Document(aircraft_id=1, filename="f", original_filename="f",
                           mime_type="application/pdf")
            assert doc.is_image is False

    def test_is_image_none_mime(self, app):
        with app.app_context():
            doc = Document(aircraft_id=1, filename="f", original_filename="f", mime_type=None)
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
        uid2, tid2 = _create_user_and_tenant(app, "b@x.com")
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
        assert b"Upload Document" in rv.data

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
        rv = client.post(f"/aircraft/{ac_id}/documents/{doc_id}/edit",
                         data={"title": "New Title"})
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
        client.post(f"/aircraft/{ac_id}/documents/{doc_id}/edit",
                    data={"title": "", "is_sensitive": "1"})
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
