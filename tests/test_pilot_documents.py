"""
Tests for Phase 27: Document Improvements.

Covers:
- DocType constants
- Document.is_pdf and is_expiring_soon properties
- owner_type for pilot documents
- Pilot profile document upload/delete routes
- Insurance certificate upload with supersession
- Title suggestions endpoint
- Download-all ZIP endpoint
- Aircraft detail shows active_insurance_cert
- Upload/edit document with doc_type and valid_until fields
"""

import io
import zipfile
from datetime import date, timedelta
from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DocType,
    Document,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAllAircraftAccess,
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


def _add_aircraft(app, tenant_id, registration="OO-TST", insurance_expiry=None):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            insurance_expiry=insurance_expiry,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_document(
    app,
    aircraft_id=None,
    pilot_user_id=None,
    title=None,
    doc_type=None,
    valid_until=None,
    is_sensitive=False,
    mime_type="text/plain",
    stored_content=b"placeholder",
    upload_folder=None,
):
    with app.app_context():
        filename = f"doc_test_{title or 'x'}_{id(title)}.txt"
        if upload_folder:
            import os

            path = os.path.join(upload_folder, filename)
            os.makedirs(upload_folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(stored_content)
        doc = Document(
            aircraft_id=aircraft_id,
            pilot_user_id=pilot_user_id,
            filename=filename,
            original_filename="test.txt",
            mime_type=mime_type,
            size_bytes=len(stored_content),
            title=title,
            doc_type=doc_type,
            valid_until=valid_until,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)
        db.session.commit()
        return doc.id


def _fake_file(name="doc.txt", content=b"hello", content_type="text/plain"):
    return (BytesIO(content), name, content_type)


# ── DocType constants ─────────────────────────────────────────────────────────


class TestDocTypeConstants:
    def test_license_value(self):
        assert DocType.LICENSE == "license"

    def test_medical_value(self):
        assert DocType.MEDICAL == "medical"

    def test_insurance_cert_value(self):
        assert DocType.INSURANCE_CERT == "insurance_certificate"


# ── Document model properties ─────────────────────────────────────────────────


class TestDocumentModelPhase27:
    def test_is_pdf_true(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f.pdf",
                mime_type="application/pdf",
            )
            assert doc.is_pdf is True

    def test_is_pdf_false(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f.txt",
                mime_type="text/plain",
            )
            assert doc.is_pdf is False

    def test_is_pdf_none_mime(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1, filename="f", original_filename="f", mime_type=None
            )
            assert doc.is_pdf is False

    def test_is_expiring_soon_within_90_days(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                valid_until=date.today() + timedelta(days=30),
            )
            assert doc.is_expiring_soon is True

    def test_is_expiring_soon_today(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                valid_until=date.today(),
            )
            assert doc.is_expiring_soon is True

    def test_is_expiring_soon_exactly_90(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                valid_until=date.today() + timedelta(days=90),
            )
            assert doc.is_expiring_soon is True

    def test_is_expiring_soon_91_days_false(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                valid_until=date.today() + timedelta(days=91),
            )
            assert doc.is_expiring_soon is False

    def test_is_expiring_soon_no_date(self, app):
        with app.app_context():
            doc = Document(
                aircraft_id=1,
                filename="f",
                original_filename="f",
                valid_until=None,
            )
            assert doc.is_expiring_soon is False

    def test_owner_type_pilot(self, app):
        with app.app_context():
            doc = Document(pilot_user_id=1, filename="f", original_filename="f")
            assert doc.owner_type == "pilot"


# ── Pilot document upload ─────────────────────────────────────────────────────


class TestPilotDocumentUpload:
    def test_get_form(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/pilot/documents/upload")
        assert rv.status_code == 200
        assert b"Upload document" in rv.data

    def test_post_creates_document(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post(
            "/pilot/documents/upload",
            data={
                "file": _fake_file("licence.txt", b"PPL scan"),
                "title": "PPL Licence",
                "doc_type": "license",
                "valid_until": "2030-03-31",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(pilot_user_id=uid).first()
            assert doc is not None
            assert doc.title == "PPL Licence"
            assert doc.doc_type == DocType.LICENSE
            assert doc.valid_until == date(2030, 3, 31)
            assert doc.is_sensitive is True
            assert doc.aircraft_id is None

    def test_post_no_file_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post(
            "/pilot/documents/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200
        assert b"select a file" in rv.data

    def test_post_disallowed_extension_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post(
            "/pilot/documents/upload",
            data={"file": _fake_file("virus.exe", b"\x00", "application/octet-stream")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200
        assert b"not allowed" in rv.data

    def test_requires_login(self, app, client):
        rv = client.get("/pilot/documents/upload")
        assert rv.status_code in (302, 401)

    def test_invalid_valid_until_ignored(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _login(app, client)
        rv = client.post(
            "/pilot/documents/upload",
            data={
                "file": _fake_file("med.txt", b"data"),
                "valid_until": "not-a-date",
            },
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(pilot_user_id=uid).first()
            assert doc.valid_until is None


# ── Pilot document delete ─────────────────────────────────────────────────────


class TestPilotDocumentDelete:
    def test_delete_own_document(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        doc_id = _add_document(app, pilot_user_id=uid, title="My Licence")
        _login(app, client)
        rv = client.post(f"/pilot/documents/{doc_id}/delete")
        assert rv.status_code == 302
        with app.app_context():
            assert db.session.get(Document, doc_id) is None

    def test_cannot_delete_other_user_document(self, app, client):
        uid1, _ = _create_user_and_tenant(app, "a@x.com")
        uid2, _ = _create_user_and_tenant(app, "b@x.com")
        doc_id = _add_document(app, pilot_user_id=uid2, title="Other's doc")
        _login(app, client, "a@x.com")
        rv = client.post(f"/pilot/documents/{doc_id}/delete")
        assert rv.status_code == 404

    def test_requires_login(self, app, client):
        rv = client.post("/pilot/documents/999/delete")
        assert rv.status_code in (302, 401)


# ── Pilot profile shows documents ─────────────────────────────────────────────


class TestPilotProfileDocuments:
    def test_profile_shows_pilot_docs(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_document(app, pilot_user_id=uid, title="PPL Licence", doc_type="license")
        _login(app, client)
        rv = client.get("/pilot/profile")
        assert rv.status_code == 200
        assert b"PPL Licence" in rv.data

    def test_profile_shows_expiry_warning(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_document(
            app,
            pilot_user_id=uid,
            title="Medical",
            doc_type="medical",
            valid_until=date.today() + timedelta(days=30),
        )
        _login(app, client)
        rv = client.get("/pilot/profile")
        assert b"Expires" in rv.data

    def test_profile_no_warning_far_away(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_document(
            app,
            pilot_user_id=uid,
            title="Licence",
            valid_until=date.today() + timedelta(days=400),
        )
        _login(app, client)
        rv = client.get("/pilot/profile")
        assert b"Expires" not in rv.data
        assert b"Valid until" in rv.data

    def test_profile_no_docs_shows_empty_state(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        rv = client.get("/pilot/profile")
        assert b"No documents" in rv.data


# ── Insurance certificate upload ──────────────────────────────────────────────


class TestInsuranceCertUpload:
    def test_upload_creates_doc_with_insurance_cert_type(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, insurance_expiry=date(2027, 6, 1))
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/insurance-cert/upload",
            data={"file": _fake_file("cert.pdf", b"%PDF-1.4", "application/pdf")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302
        with app.app_context():
            doc = Document.query.filter_by(
                aircraft_id=ac_id, doc_type=DocType.INSURANCE_CERT
            ).first()
            assert doc is not None
            assert doc.valid_until == date(2027, 6, 1)
            assert doc.is_sensitive is True
            assert doc.superseded_by_id is None

    def test_upload_supersedes_previous_cert(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, insurance_expiry=date(2027, 6, 1))
        old_doc_id = _add_document(
            app,
            aircraft_id=ac_id,
            title="Old Cert",
            doc_type=DocType.INSURANCE_CERT,
            is_sensitive=True,
        )
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/insurance-cert/upload",
            data={"file": _fake_file("new_cert.pdf", b"data")},
            content_type="multipart/form-data",
        )
        with app.app_context():
            old_doc = db.session.get(Document, old_doc_id)
            assert old_doc.superseded_by_id is not None

    def test_upload_no_file_redirects_with_flash(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, insurance_expiry=date(2027, 6, 1))
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/insurance-cert/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

    def test_upload_disallowed_extension(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, insurance_expiry=date(2027, 6, 1))
        _login(app, client)
        rv = client.post(
            f"/aircraft/{ac_id}/insurance-cert/upload",
            data={"file": _fake_file("cert.exe", b"\x00", "application/octet-stream")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 302

    def test_upload_requires_owner_role(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        # Add a viewer user to the same tenant
        with app.app_context():
            viewer = User(
                email="viewer@x.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(viewer)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=viewer.id, tenant_id=tid, role=Role.VIEWER)
            )
            db.session.commit()
            viewer_id = viewer.id
        ac_id = _add_aircraft(app, tid)
        with client.session_transaction() as sess:
            sess["user_id"] = viewer_id
        rv = client.post(
            f"/aircraft/{ac_id}/insurance-cert/upload",
            data={"file": _fake_file("cert.txt", b"data")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 403


# ── Title suggestions ─────────────────────────────────────────────────────────


class TestTitleSuggestions:
    def test_returns_aircraft_titles(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, aircraft_id=ac_id, title="Annual Review 2025")
        _add_document(app, aircraft_id=ac_id, title="Weight & Balance")
        _login(app, client)
        rv = client.get("/documents/title-suggestions?owner_type=aircraft")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "Annual Review 2025" in data
        assert "Weight & Balance" in data

    def test_filters_by_prefix(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, aircraft_id=ac_id, title="Annual Review 2025")
        _add_document(app, aircraft_id=ac_id, title="Weight & Balance")
        _login(app, client)
        rv = client.get("/documents/title-suggestions?q=Ann&owner_type=aircraft")
        data = rv.get_json()
        assert "Annual Review 2025" in data
        assert "Weight & Balance" not in data

    def test_returns_pilot_titles_for_pilot_owner_type(self, app, client):
        uid, _ = _create_user_and_tenant(app)
        _add_document(app, pilot_user_id=uid, title="PPL Licence")
        _login(app, client)
        rv = client.get("/documents/title-suggestions?owner_type=pilot")
        data = rv.get_json()
        assert "PPL Licence" in data

    def test_scoped_to_tenant(self, app, client):
        uid1, tid1 = _create_user_and_tenant(app, "a@x.com")
        uid2, tid2 = _create_user_and_tenant(app, "b@x.com")
        ac1_id = _add_aircraft(app, tid1, "OO-A")
        ac2_id = _add_aircraft(app, tid2, "OO-B")
        _add_document(app, aircraft_id=ac1_id, title="Tenant A Doc")
        _add_document(app, aircraft_id=ac2_id, title="Tenant B Doc")
        _login(app, client, "a@x.com")
        rv = client.get("/documents/title-suggestions?owner_type=aircraft")
        data = rv.get_json()
        assert "Tenant A Doc" in data
        assert "Tenant B Doc" not in data

    def test_excludes_null_titles(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(app, aircraft_id=ac_id, title=None)
        _login(app, client)
        rv = client.get("/documents/title-suggestions?owner_type=aircraft")
        data = rv.get_json()
        assert None not in data

    def test_component_owner_type(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            from models import Component, ComponentType

            comp = Component(
                aircraft_id=ac_id, type=ComponentType.ENGINE, make="Lyc", model="IO360"
            )
            db.session.add(comp)
            db.session.flush()
            comp_id = comp.id
            doc = Document(
                aircraft_id=ac_id,
                component_id=comp_id,
                filename="f",
                original_filename="f.txt",
                title="Engine Log",
            )
            db.session.add(doc)
            db.session.commit()
        _login(app, client)
        rv = client.get("/documents/title-suggestions?owner_type=component")
        data = rv.get_json()
        assert "Engine Log" in data

    def test_requires_login(self, app, client):
        rv = client.get("/documents/title-suggestions")
        assert rv.status_code in (302, 401)


# ── Download-all ZIP ──────────────────────────────────────────────────────────


class TestDownloadAllDocuments:
    def test_returns_zip(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(
            app,
            aircraft_id=ac_id,
            title="ARC",
            stored_content=b"content",
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/download-all")
        assert rv.status_code == 200
        assert rv.content_type == "application/zip"
        buf = io.BytesIO(rv.data)
        with zipfile.ZipFile(buf) as zf:
            assert "manifest.txt" in zf.namelist()

    def test_zip_excludes_sensitive_for_non_owner(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        # Add a pilot (non-owner) user with all-aircraft access
        with app.app_context():
            crew_user = User(
                email="crew@x.com",
                password_hash=_pw_hash.hash("pw"),
                is_active=True,
            )
            db.session.add(crew_user)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=crew_user.id, tenant_id=tid, role=Role.PILOT)
            )
            db.session.add(UserAllAircraftAccess(user_id=crew_user.id, tenant_id=tid))
            db.session.commit()
            crew_id = crew_user.id
        ac_id = _add_aircraft(app, tid)
        _add_document(
            app,
            aircraft_id=ac_id,
            title="Secret Cert",
            is_sensitive=True,
            stored_content=b"secret",
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        with client.session_transaction() as sess:
            sess["user_id"] = crew_id
        rv = client.get(f"/aircraft/{ac_id}/documents/download-all")
        assert rv.status_code == 200
        buf = io.BytesIO(rv.data)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "manifest.txt" in names
            assert not any("Secret" in n for n in names)

    def test_zip_includes_sensitive_for_owner(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_document(
            app,
            aircraft_id=ac_id,
            title="Secret Cert",
            is_sensitive=True,
            stored_content=b"sensitive data",
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/download-all")
        buf = io.BytesIO(rv.data)
        with zipfile.ZipFile(buf) as zf:
            # manifest.txt should mention the sensitive doc
            manifest = zf.read("manifest.txt").decode()
            assert "Secret Cert" in manifest

    def test_zip_missing_file_still_returns(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        # Add a document whose file doesn't exist on disk
        _add_document(app, aircraft_id=ac_id, title="Ghost Doc")
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}/documents/download-all")
        assert rv.status_code == 200
        buf = io.BytesIO(rv.data)
        with zipfile.ZipFile(buf) as zf:
            manifest = zf.read("manifest.txt").decode()
            assert "Ghost Doc" in manifest

    def test_requires_login(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        rv = client.get(f"/aircraft/{ac_id}/documents/download-all")
        assert rv.status_code in (302, 401)


# ── Aircraft detail: active_insurance_cert ────────────────────────────────────


class TestAircraftDetailInsuranceCert:
    def test_detail_shows_view_certificate_link(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(
            app, tid, insurance_expiry=date.today() + timedelta(days=180)
        )
        _add_document(
            app,
            aircraft_id=ac_id,
            title="Insurance Certificate",
            doc_type=DocType.INSURANCE_CERT,
            is_sensitive=True,
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}")
        assert rv.status_code == 200
        assert b"View certificate" in rv.data

    def test_detail_no_cert_no_link(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(
            app, tid, insurance_expiry=date.today() + timedelta(days=180)
        )
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}")
        assert b"View certificate" not in rv.data

    def test_superseded_cert_not_shown(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(
            app, tid, insurance_expiry=date.today() + timedelta(days=180)
        )
        old_id = _add_document(
            app,
            aircraft_id=ac_id,
            title="Old Cert",
            doc_type=DocType.INSURANCE_CERT,
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        # Supersede old cert
        new_id = _add_document(
            app,
            aircraft_id=ac_id,
            title="New Cert",
            doc_type=DocType.INSURANCE_CERT,
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        with app.app_context():
            old_doc = db.session.get(Document, old_id)
            old_doc.superseded_by_id = new_id
            db.session.commit()
        _login(app, client)
        rv = client.get(f"/aircraft/{ac_id}")
        # Should show upload cert button for owners even without a cert
        assert rv.status_code == 200


# ── Upload document: doc_type and valid_until fields ─────────────────────────


class TestUploadDocumentPhase27Fields:
    def test_upload_saves_doc_type(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("cert.txt", b"data"),
                "doc_type": "license",
                "valid_until": "2030-01-15",
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.doc_type == "license"
            assert doc.valid_until == date(2030, 1, 15)

    def test_upload_invalid_valid_until_stored_as_none(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _fake_file("doc.txt", b"data"),
                "valid_until": "not-a-date",
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc.valid_until is None


# ── Edit document: valid_until field ─────────────────────────────────────────


class TestEditDocumentPhase27Fields:
    def test_edit_sets_valid_until(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, aircraft_id=ac_id, title="ARC")
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "ARC", "valid_until": "2026-12-31"},
        )
        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.valid_until == date(2026, 12, 31)

    def test_edit_clears_valid_until_when_blank(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(
            app, aircraft_id=ac_id, title="ARC", valid_until=date(2026, 6, 1)
        )
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "ARC", "valid_until": ""},
        )
        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.valid_until is None

    def test_edit_invalid_valid_until_ignored(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        doc_id = _add_document(app, aircraft_id=ac_id, title="ARC")
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/documents/{doc_id}/edit",
            data={"title": "ARC", "valid_until": "bad-date"},
        )
        with app.app_context():
            doc = db.session.get(Document, doc_id)
            assert doc.valid_until is None
