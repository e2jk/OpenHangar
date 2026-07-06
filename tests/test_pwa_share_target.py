"""
Tests for PWA Share Target — /pwa/shared and /pwa/shared/confirm.

Covers:
- GET /pwa/shared redirects to index
- POST /pwa/shared with no files flashes and redirects
- POST /pwa/shared with valid files renders disambiguation page
- POST /pwa/shared/confirm with no session data redirects
- Document destination: creates Document record and redirects to document list
- Document destination: missing aircraft_id flashes and redirects
- Document destination: cross-tenant aircraft_id returns 404
- Document destination: non-owner role returns 403
- Document destination: canonical path used when category is set
- Document destination: flat path used when no category
- Expense destination: redirects to expense add with flash
- Expense destination: no aircraft_id falls back to index
- Maintenance destination: redirects to maintenance list with flash
- Maintenance destination: no aircraft_id falls back to index
- Flight photo destination: redirects to new flight with flash
- Unknown destination: redirects to index with flash
- Manifest includes share_target field
"""

import io
import json


def _make_user_and_aircraft(app, email="share@test.com", role=None):
    import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
    from models import Aircraft, Role, Tenant, TenantUser, User, db

    if role is None:
        role = Role.OWNER

    with app.app_context():
        t = Tenant(name="ShareTest")
        db.session.add(t)
        db.session.flush()
        u = User(
            email=email,
            password_hash=_pw_hash.hash("x"),
            is_active=True,
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=role))
        ac = Aircraft(
            tenant_id=t.id,
            registration="OO-SHR",
            make="Test",
            model="T1",
        )
        db.session.add(ac)
        db.session.commit()
        return u.id, ac.id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _pdf_file():
    return (io.BytesIO(b"%PDF-1.4 test"), "test.pdf", "application/pdf")


def _image_file():
    return (io.BytesIO(b"\xff\xd8\xff test"), "photo.jpg", "image/jpeg")


class TestShareTargetGet:
    def test_get_redirects_to_index(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "get@test.com")
        _login(client, uid)
        r = client.get("/pwa/shared")
        assert r.status_code == 302
        assert "/" in r.headers["Location"]

    def test_get_requires_auth(self, client):
        r = client.get("/pwa/shared")
        assert r.status_code == 302
        assert "login" in r.headers["Location"].lower()


class TestShareTargetPost:
    def test_post_requires_auth(self, client):
        r = client.post("/pwa/shared", data={})
        assert r.status_code == 302
        assert "login" in r.headers["Location"].lower()

    def test_no_files_flashes_and_redirects(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "nofile@test.com")
        _login(client, uid)
        r = client.post("/pwa/shared", data={})
        assert r.status_code == 302

    def test_empty_filename_flashes_and_redirects(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "emptyname@test.com")
        _login(client, uid)
        data = {"files": (io.BytesIO(b""), "")}
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert r.status_code == 302

    def test_valid_pdf_renders_disambiguation(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "pdf@test.com")
        _login(client, uid)
        data = {"files": _pdf_file(), "title": "My PDF"}
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert r.status_code == 200
        assert b"test.pdf" in r.data

    def test_valid_image_renders_disambiguation(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "img@test.com")
        _login(client, uid)
        data = {"files": _image_file()}
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert r.status_code == 200
        assert b"photo.jpg" in r.data

    def test_multiple_files_rendered(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "multi@test.com")
        _login(client, uid)
        data = {
            "files": [
                _pdf_file(),
                (io.BytesIO(b"\xff\xd8\xff"), "other.jpg", "image/jpeg"),
            ]
        }
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert r.status_code == 200
        assert b"test.pdf" in r.data
        assert b"other.jpg" in r.data

    def test_session_set_after_upload(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "sess@test.com")
        _login(client, uid)
        data = {"files": _pdf_file(), "title": "Session title"}
        client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        with client.session_transaction() as sess:
            assert "share_pending" in sess
            assert sess["share_pending"]["title"] == "Session title"

    def test_destinations_filtered_by_mime(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "filtermime@test.com")
        _login(client, uid)
        # A PDF is compatible with document/expense/maintenance but NOT flight_photo
        data = {"files": _pdf_file()}
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert b"flight_photo" not in r.data

    def test_no_compatible_destination_shows_message(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "nocompat@test.com")
        _login(client, uid)
        # text/plain is only accepted by "document"
        # An SVG is not accepted by any destination
        data = {
            "files": (
                io.BytesIO(b"<svg/>"),
                "image.svg",
                "image/svg+xml",
            )
        }
        r = client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        assert r.status_code == 200
        assert b"No compatible destination" in r.data


class TestShareConfirm:
    def test_requires_auth(self, client):
        r = client.post("/pwa/shared/confirm", data={})
        assert r.status_code == 302
        assert "login" in r.headers["Location"].lower()

    def test_no_pending_session_redirects(self, client, app):
        uid, _ = _make_user_and_aircraft(app, "nopending@test.com")
        _login(client, uid)
        r = client.post("/pwa/shared/confirm", data={"destination": "document"})
        assert r.status_code == 302

    def _setup_pending(self, client, app, email, role=None, file_tuple=None):
        uid, ac_id = _make_user_and_aircraft(app, email, role=role)
        _login(client, uid)
        data = {"files": file_tuple or _pdf_file(), "title": "Test doc"}
        client.post("/pwa/shared", data=data, content_type="multipart/form-data")
        return uid, ac_id

    def test_document_creates_record(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "docok@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": str(ac_id)},
        )
        assert r.status_code == 302
        assert "documents" in r.headers["Location"]
        from models import Document

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.original_filename == "test.pdf"

    def test_document_with_category_creates_record(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "doccat@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={
                "destination": "document",
                "aircraft_id": str(ac_id),
                "category": "insurance",
                "valid_until": "2027-01-01",
                "is_sensitive": "1",
            },
        )
        assert r.status_code == 302
        from models import Document

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.category == "insurance"
            assert doc.is_sensitive is True

    def test_document_invalid_category_ignored(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "badcat@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={
                "destination": "document",
                "aircraft_id": str(ac_id),
                "category": "notavalidcategory",
            },
        )
        assert r.status_code == 302
        from models import Document

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.category is None

    def test_document_invalid_valid_until_ignored(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "baddate@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={
                "destination": "document",
                "aircraft_id": str(ac_id),
                "valid_until": "not-a-date",
            },
        )
        assert r.status_code == 302
        from models import Document

        with app.app_context():
            doc = Document.query.filter_by(aircraft_id=ac_id).first()
            assert doc is not None
            assert doc.valid_until is None

    def test_document_missing_aircraft_id_redirects(self, client, app):
        self._setup_pending(client, app, "noac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": ""},
        )
        assert r.status_code == 302

    def test_document_non_numeric_aircraft_id_redirects(self, client, app):
        self._setup_pending(client, app, "badac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": "abc"},
        )
        assert r.status_code == 302

    def test_document_cross_tenant_aircraft_returns_404(self, client, app):

        from models import Aircraft, Tenant, db

        # Create a second tenant with its own aircraft
        with app.app_context():
            t2 = Tenant(name="OtherTenant")
            db.session.add(t2)
            db.session.flush()
            other_ac = Aircraft(
                tenant_id=t2.id, registration="OO-OTH", make="X", model="Y"
            )
            db.session.add(other_ac)
            db.session.commit()
            other_ac_id = other_ac.id

        uid, _ = self._setup_pending(client, app, "xten@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": str(other_ac_id)},
        )
        assert r.status_code == 404

    def test_document_non_owner_role_returns_403(self, client, app):
        from models import Role

        self._setup_pending(client, app, "pilot@test.com", role=Role.PILOT)
        _, ac_id = _make_user_and_aircraft(app, "pilot2@test.com", role=Role.PILOT)
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": str(ac_id)},
        )
        assert r.status_code == 403

    def test_expense_redirects_with_aircraft(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "exp@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "expense", "aircraft_id": str(ac_id)},
        )
        assert r.status_code == 302
        assert "expenses" in r.headers["Location"]

    def test_expense_no_aircraft_falls_back_to_index(self, client, app):
        self._setup_pending(client, app, "expnoac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "expense", "aircraft_id": ""},
        )
        assert r.status_code == 302

    def test_expense_non_numeric_aircraft_falls_back(self, client, app):
        self._setup_pending(client, app, "expbadac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "expense", "aircraft_id": "notanumber"},
        )
        assert r.status_code == 302

    def test_maintenance_redirects_with_aircraft(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "maint@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "maintenance", "aircraft_id": str(ac_id)},
        )
        assert r.status_code == 302
        assert "maintenance" in r.headers["Location"]

    def test_maintenance_no_aircraft_falls_back(self, client, app):
        self._setup_pending(client, app, "maintnoac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "maintenance", "aircraft_id": ""},
        )
        assert r.status_code == 302

    def test_maintenance_non_numeric_aircraft_falls_back(self, client, app):
        self._setup_pending(client, app, "maintbadac@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "maintenance", "aircraft_id": "xyz"},
        )
        assert r.status_code == 302

    def test_flight_photo_redirects_to_new_flight(self, client, app):
        # jpeg is compatible with every destination, including flight_photo.
        self._setup_pending(client, app, "flphoto@test.com", file_tuple=_image_file())
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "flight_photo"},
        )
        assert r.status_code == 302
        assert "flights/new" in r.headers["Location"]

    def test_unknown_destination_redirects(self, client, app):
        self._setup_pending(client, app, "unkdest@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "nonsense"},
        )
        assert r.status_code == 302

    def test_destination_incompatible_with_shared_mime_rejected(self, client, app):
        """A PDF cannot be confirmed to flight_photo — not in its accepted MIME set (N-28)."""
        self._setup_pending(client, app, "mismatch@test.com")
        r = client.post(
            "/pwa/shared/confirm",
            data={"destination": "flight_photo"},
        )
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert "share_pending" not in sess
            flashes = sess.get("_flashes", [])
        assert any("Unknown destination" in msg for _, msg in flashes)

    def test_document_filename_collision_gets_unique_suffix(self, client, app):
        import os

        uid, ac_id = self._setup_pending(client, app, "collision@test.com")
        # Pre-create the file that the canonical path would produce so the
        # collision-avoidance branch runs.
        with app.app_context():
            from models import Tenant, TenantUser, db

            tu = TenantUser.query.filter_by(user_id=uid).first()
            tenant = db.session.get(Tenant, tu.tenant_id)
            from pwa.routes import _ensure_tenant_slug

            slug = _ensure_tenant_slug(tenant)
            from models import Aircraft

            ac = db.session.get(Aircraft, ac_id)
            safe_reg = ac.registration.replace("/", "-").replace(" ", "-").upper()
            from datetime import date

            today = date.today().isoformat()
            upload_folder = app.config["UPLOAD_FOLDER"]
            cat_dir = os.path.join(upload_folder, slug, safe_reg, "insurance")
            os.makedirs(cat_dir, exist_ok=True)
            # Create the file the route would pick as its first-choice name
            existing = os.path.join(cat_dir, f"{today} - test doc.pdf")
            with open(existing, "wb") as fh:
                fh.write(b"existing")
            db.session.commit()

        r = client.post(
            "/pwa/shared/confirm",
            data={
                "destination": "document",
                "aircraft_id": str(ac_id),
                "category": "insurance",
                "title": "test doc",
            },
        )
        assert r.status_code == 302
        from models import Document

        with app.app_context():
            docs = Document.query.filter_by(aircraft_id=ac_id).all()
            assert len(docs) == 1
            assert docs[0].filename != os.path.join(
                slug, safe_reg, "insurance", f"{today} - test doc.pdf"
            )

    def test_session_cleared_after_confirm(self, client, app):
        uid, ac_id = self._setup_pending(client, app, "sessclr@test.com")
        client.post(
            "/pwa/shared/confirm",
            data={"destination": "document", "aircraft_id": str(ac_id)},
        )
        with client.session_transaction() as sess:
            assert "share_pending" not in sess


class TestAllowedDestinations:
    def test_pdf_allows_document_expense_maintenance(self):
        from pwa.routes import _allowed_destinations

        dests = _allowed_destinations(["application/pdf"])
        assert "document" in dests
        assert "expense" in dests
        assert "maintenance" in dests
        assert "flight_photo" not in dests

    def test_jpeg_allows_all_destinations(self):
        from pwa.routes import _allowed_destinations

        dests = _allowed_destinations(["image/jpeg"])
        assert "document" in dests
        assert "expense" in dests
        assert "maintenance" in dests
        assert "flight_photo" in dests

    def test_svg_allows_no_destination(self):
        from pwa.routes import _allowed_destinations

        dests = _allowed_destinations(["image/svg+xml"])
        assert dests == []

    def test_mixed_pdf_and_jpeg_excludes_flight_photo(self):
        from pwa.routes import _allowed_destinations

        dests = _allowed_destinations(["application/pdf", "image/jpeg"])
        assert "document" in dests
        assert "flight_photo" not in dests


class TestManifestShareTarget:
    def test_manifest_has_share_target(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        assert "share_target" in data

    def test_share_target_action_is_pwa_shared(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        assert data["share_target"]["action"] == "/pwa/shared"

    def test_share_target_accepts_files(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        st = data["share_target"]
        assert st["method"] == "POST"
        assert st["enctype"] == "multipart/form-data"
        params_files = st["params"]["files"]
        assert len(params_files) >= 1
        assert params_files[0]["name"] == "files"
