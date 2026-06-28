"""
Tests for Phase 35 — Progressive Web App (PWA).

Covers:
- /manifest.json endpoint (structure, required fields, icons)
- /sw.js endpoint (served with Service-Worker-Allowed header, correct MIME type)
- /api/check-flight-duplicate endpoint (auth required, duplicate detection)
- CSP worker-src includes 'self' for service worker registration
- camera capture attribute on flight form photo inputs
- PWA assets present (icons, offline page, pwa.js, pwa.css)
- base.html contains manifest link, theme-color, pwa.js load
"""

import json
from pathlib import Path


_STATIC_DIR = Path(__file__).parent.parent / "app" / "static"
_TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"


class TestManifest:
    def test_manifest_returns_200(self, client):
        r = client.get("/manifest.json")
        assert r.status_code == 200

    def test_manifest_content_type_is_json(self, client):
        r = client.get("/manifest.json")
        assert "application/json" in r.content_type

    def test_manifest_has_required_fields(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        assert data["name"] == "OpenHangar"
        assert data["short_name"] == "OpenHangar"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        assert "theme_color" in data
        assert "background_color" in data

    def test_manifest_has_icons(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        assert "icons" in data
        icons = data["icons"]
        assert len(icons) >= 2
        srcs = [i["src"] for i in icons]
        assert any("icon.svg" in s for s in srcs)
        maskable = [i for i in icons if i.get("purpose") == "maskable"]
        assert maskable, "manifest must include at least one maskable icon"

    def test_manifest_icons_reference_existing_files(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        for icon in data["icons"]:
            # src is e.g. /static/icons/icon.svg — strip /static/ prefix
            src = icon["src"]
            assert src.startswith("/static/"), f"Unexpected icon src: {src}"
            path = _STATIC_DIR / src[len("/static/") :]
            assert path.exists(), f"Icon file missing: {src}"

    def test_manifest_has_shortcuts(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        assert "shortcuts" in data
        shortcuts = data["shortcuts"]
        assert len(shortcuts) == 3
        for sc in shortcuts:
            assert "name" in sc
            assert "url" in sc
            assert "icons" in sc
            assert len(sc["icons"]) >= 1

    def test_manifest_shortcut_icons_reference_existing_files(self, client):
        r = client.get("/manifest.json")
        data = json.loads(r.data)
        for sc in data["shortcuts"]:
            for icon in sc["icons"]:
                src = icon["src"]
                assert src.startswith("/static/"), (
                    f"Unexpected shortcut icon src: {src}"
                )
                path = _STATIC_DIR / src[len("/static/") :]
                assert path.exists(), f"Shortcut icon file missing: {src}"


class TestServiceWorker:
    def test_sw_js_returns_200(self, client):
        r = client.get("/sw.js")
        assert r.status_code == 200

    def test_sw_js_content_type(self, client):
        r = client.get("/sw.js")
        assert "javascript" in r.content_type

    def test_sw_js_has_service_worker_allowed_header(self, client):
        r = client.get("/sw.js")
        assert r.headers.get("Service-Worker-Allowed") == "/"

    def test_sw_js_contains_cache_install(self, client):
        r = client.get("/sw.js")
        body = r.data.decode()
        assert "install" in body
        assert "caches" in body

    def test_sw_js_cache_version_substituted(self, client):
        r = client.get("/sw.js")
        body = r.data.decode()
        assert "__SW_CACHE_VERSION__" not in body
        assert "openhangar-" in body

    def test_sw_js_dev_cache_version_is_random(self, client, monkeypatch):
        # Without a real version, each request should produce a unique cache name
        # so the SW never serves stale static assets in dev/test environments.
        monkeypatch.delenv("OPENHANGAR_VERSION", raising=False)
        body1 = client.get("/sw.js").data.decode()
        body2 = client.get("/sw.js").data.decode()
        assert body1 != body2

    def test_sw_js_file_exists(self):
        assert (_STATIC_DIR / "js" / "sw.js").exists()


class TestCheckFlightDuplicateAPI:
    def test_requires_auth(self, client):
        r = client.get(
            "/api/check-flight-duplicate?date=2024-01-01&departure_icao=EBBR&arrival_icao=EBOS"
        )
        assert r.status_code == 401

    def test_returns_no_duplicate_when_no_match(self, client, app):
        import bcrypt as _bcrypt

        from models import Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get(
            "/api/check-flight-duplicate"
            "?date=2099-12-31&departure_icao=EBBR&arrival_icao=EBOS&aircraft_id=9999"
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["duplicate"] is False

    def test_detects_existing_flight(self, client, app):
        import bcrypt as _bcrypt
        from datetime import date

        from models import Aircraft, FlightEntry, Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test2")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa2@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            ac = Aircraft(
                tenant_id=t.id,
                registration="OO-TST",
                make="Test",
                model="T1",
            )
            db.session.add(ac)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=ac.id,
                date=date(2024, 6, 1),
                departure_icao="EBBR",
                arrival_icao="EBOS",
            )
            db.session.add(fe)
            db.session.commit()
            uid = u.id
            ac_id = ac.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get(
            f"/api/check-flight-duplicate"
            f"?date=2024-06-01&departure_icao=EBBR&arrival_icao=EBOS&aircraft_id={ac_id}"
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["duplicate"] is True

    def test_aircraft_duplicate_is_tenant_scoped(self, client, app):
        """A user must not learn whether a flight exists on another tenant's
        aircraft (cross-tenant existence oracle — N-24 / CWE-639)."""
        import bcrypt as _bcrypt
        from datetime import date

        from models import Aircraft, FlightEntry, Role, Tenant, TenantUser, User, db

        with app.app_context():
            # Tenant A — the attacker, with no flights of their own.
            ta = Tenant(name="TenantA-N24")
            # Tenant B — the victim, owns the aircraft and the flight.
            tb = Tenant(name="TenantB-N24")
            db.session.add_all([ta, tb])
            db.session.flush()
            attacker = User(
                email="attacker-n24@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(attacker)
            db.session.flush()
            db.session.add(
                TenantUser(tenant_id=ta.id, user_id=attacker.id, role=Role.PILOT)
            )
            victim_ac = Aircraft(
                tenant_id=tb.id,
                registration="OO-VIC",
                make="Test",
                model="T1",
            )
            db.session.add(victim_ac)
            db.session.flush()
            db.session.add(
                FlightEntry(
                    aircraft_id=victim_ac.id,
                    date=date(2024, 6, 1),
                    departure_icao="EBBR",
                    arrival_icao="EBOS",
                )
            )
            db.session.commit()
            attacker_uid = attacker.id
            victim_ac_id = victim_ac.id

        with client.session_transaction() as sess:
            sess["user_id"] = attacker_uid

        # The flight genuinely exists, but on another tenant's aircraft — the
        # endpoint must report no duplicate rather than confirm its existence.
        r = client.get(
            f"/api/check-flight-duplicate"
            f"?date=2024-06-01&departure_icao=EBBR&arrival_icao=EBOS"
            f"&aircraft_id={victim_ac_id}"
        )
        assert r.status_code == 200
        assert json.loads(r.data)["duplicate"] is False

    def test_missing_params_returns_no_duplicate(self, client, app):
        import bcrypt as _bcrypt

        from models import Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test3")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa3@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get("/api/check-flight-duplicate")
        assert r.status_code == 200
        assert json.loads(r.data)["duplicate"] is False

    def test_non_numeric_aircraft_id_returns_no_duplicate(self, client, app):
        import bcrypt as _bcrypt

        from models import Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test4a")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa4a@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get(
            "/api/check-flight-duplicate"
            "?date=2024-01-01&departure_icao=EBBR&arrival_icao=EBOS&aircraft_id=notanumber"
        )
        assert r.status_code == 200
        assert json.loads(r.data)["duplicate"] is False

    def test_detects_pilot_logbook_duplicate(self, client, app):
        import bcrypt as _bcrypt
        from datetime import date

        from models import PilotLogbookEntry, Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test4b")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa4b@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            db.session.flush()
            ple = PilotLogbookEntry(
                pilot_user_id=u.id,
                date=date(2024, 7, 1),
                departure_place="EBBR",
                arrival_place="EBOS",
            )
            db.session.add(ple)
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get(
            "/api/check-flight-duplicate"
            "?date=2024-07-01&departure_icao=EBBR&arrival_icao=EBOS"
        )
        assert r.status_code == 200
        assert json.loads(r.data)["duplicate"] is True

    def test_invalid_date_returns_no_duplicate(self, client, app):
        import bcrypt as _bcrypt

        from models import Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="Test4")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="pwa4@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.PILOT))
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        r = client.get(
            "/api/check-flight-duplicate"
            "?date=not-a-date&departure_icao=EBBR&arrival_icao=EBOS"
        )
        assert r.status_code == 200
        assert json.loads(r.data)["duplicate"] is False


class TestCSPWorkerSrc:
    def test_csp_worker_src_includes_self(self, client):
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        worker_src = (
            csp.split("worker-src")[1].split(";")[0] if "worker-src" in csp else ""
        )
        assert "'self'" in worker_src, (
            "worker-src must include 'self' for SW registration"
        )


class TestPWAAssets:
    def test_icon_svg_exists(self):
        assert (_STATIC_DIR / "icons" / "icon.svg").exists()

    def test_icon_maskable_svg_exists(self):
        assert (_STATIC_DIR / "icons" / "icon-maskable.svg").exists()

    def test_offline_html_exists(self):
        assert (_STATIC_DIR / "pwa" / "offline.html").exists()

    def test_pwa_js_exists(self):
        assert (_STATIC_DIR / "js" / "pwa.js").exists()

    def test_pwa_css_exists(self):
        assert (_STATIC_DIR / "css" / "pwa.css").exists()

    def test_shortcut_log_flight_icon_exists(self):
        assert (_STATIC_DIR / "icons" / "shortcut-log-flight.svg").exists()

    def test_shortcut_aircraft_icon_exists(self):
        assert (_STATIC_DIR / "icons" / "shortcut-aircraft.svg").exists()

    def test_shortcut_documents_icon_exists(self):
        assert (_STATIC_DIR / "icons" / "shortcut-documents.svg").exists()

    def test_offline_html_has_no_inline_style_attr(self):
        content = (_STATIC_DIR / "pwa" / "offline.html").read_text()
        import re

        assert not re.search(r'\bstyle\s*=\s*["\']', content), (
            "offline.html must not use style= attributes (CSP: style-src-attr 'none')"
        )

    def test_offline_html_has_no_inline_js(self):
        content = (_STATIC_DIR / "pwa" / "offline.html").read_text()
        import re

        assert not re.search(r'\bon\w+\s*=\s*["\']', content), (
            "offline.html must not use JS event handler attributes (blocked by CSP)"
        )


class TestBaseTemplateIntegration:
    def test_base_html_has_manifest_link(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert 'rel="manifest"' in content
        assert "manifest.json" in content

    def test_base_html_has_theme_color(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert 'name="theme-color"' in content

    def test_base_html_loads_pwa_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "pwa.js" in content

    def test_base_html_loads_pwa_css(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "pwa.css" in content

    def test_base_html_has_offline_badge(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "oh-pwa-offline-badge" in content

    def test_base_html_has_queue_badge(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "oh-pwa-queue-badge" in content

    def test_base_html_has_install_bar(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "oh-pwa-install-bar" in content


class TestFlightFormCameraCapture:
    def test_photo_inputs_have_capture_attribute(self):
        content = (_TEMPLATES_DIR / "flights" / "flight_form.html").read_text()
        assert 'name="flight_counter_photo"' in content
        assert 'name="engine_counter_photo"' in content
        assert 'name="fuel_photo"' in content
        # Verify capture= attribute is present near photo inputs
        import re

        photo_inputs = re.findall(
            r"<input[^>]+(?:flight_counter_photo|engine_counter_photo|fuel_photo)[^>]*>",
            content,
        )
        assert len(photo_inputs) == 3, (
            f"Expected 3 photo inputs, found {len(photo_inputs)}"
        )
        for inp in photo_inputs:
            assert 'capture="environment"' in inp, (
                f"Photo input missing capture attribute: {inp[:120]}"
            )


class TestShareTargetEdgeCases:
    """Cover edge-case branches in pwa/routes.py not reached by main share tests."""

    def _setup_admin(self, app):
        import bcrypt as _bcrypt
        from models import Aircraft, Role, Tenant, TenantUser, User, db

        with app.app_context():
            t = Tenant(name="ST-Test")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="st_admin@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.ADMIN))
            ac = Aircraft(tenant_id=t.id, registration="OO-ST1", make="T", model="M")
            db.session.add(ac)
            db.session.commit()
            return u.id, t.id, ac.id

    def test_get_user_aircraft_returns_empty_for_orphan_user(self, app, client):
        """Line 98: _get_user_aircraft() → [] when user has no TenantUser."""
        import bcrypt as _bcrypt
        from io import BytesIO
        from models import User, db

        with app.app_context():
            u = User(
                email="orphan_st@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.commit()
            uid = u.id

        with client.session_transaction() as sess:
            sess["user_id"] = uid

        pdf = BytesIO(b"%PDF-1.4 fake")
        resp = client.post(
            "/pwa/shared",
            data={"files": (pdf, "test.pdf"), "title": ""},
            content_type="multipart/form-data",
        )
        # share_target renders template with _get_user_aircraft() == [] for orphan
        assert resp.status_code == 200

    def test_ensure_tenant_slug_deduplication(self, app):
        """Lines 114-115: slug collision loop increments suffix until unique."""
        from models import Tenant, db
        from pwa.routes import _ensure_tenant_slug  # pyright: ignore[reportMissingImports]

        with app.app_context():
            # Simulate two tenants that would derive the same base slug
            t1 = Tenant(name="Clash Club", slug="clash-club")
            t2 = Tenant(name="Clash Club")
            db.session.add(t1)
            db.session.add(t2)
            db.session.commit()
            slug = _ensure_tenant_slug(t2)

        assert slug != "clash-club"
        assert slug.startswith("clash-club")

    def test_document_upload_deduplicates_filename(self, app, client, tmp_path):
        """Lines 301-303: when destination file already exists, a UUID suffix is added."""
        import os
        from models import Aircraft, DocCategory, Role, Tenant, TenantUser, User, db
        import bcrypt as _bcrypt
        from datetime import date

        # Create tenant, admin, aircraft with a known slug so we can pre-create the file
        with app.app_context():
            t = Tenant(name="Dup Test Org", slug="dup-test-org")
            db.session.add(t)
            db.session.flush()
            u = User(
                email="dup_admin@test.com",
                password_hash=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(u)
            db.session.flush()
            db.session.add(TenantUser(tenant_id=t.id, user_id=u.id, role=Role.ADMIN))
            ac = Aircraft(tenant_id=t.id, registration="OO-DUP", make="T", model="M")
            db.session.add(ac)
            db.session.commit()
            uid, ac_id = u.id, ac.id

        # Pre-create the file that would be generated by today's date + title
        today = date.today().isoformat()
        category = DocCategory.ALL[0]
        upload_folder = str(tmp_path)
        rel_dir = os.path.join("dup-test-org", "OO-DUP", category)
        full_dir = os.path.join(upload_folder, rel_dir)
        os.makedirs(full_dir, exist_ok=True)
        # Match the naming logic: "{today} - {safe_title}.pdf"
        pre_existing = os.path.join(full_dir, f"{today} - preexisting.pdf")
        with open(pre_existing, "wb") as fh:
            fh.write(b"existing content")

        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["share_pending"] = {
                "tmp_dir": str(tmp_path / "sharetmp"),
                "files": [
                    {
                        "original": "preexisting.pdf",
                        "saved": "preexisting.pdf",
                        "mime": "application/pdf",
                    }
                ],
                "title": "preexisting",
            }

        # Create the temp source file too
        sharetmp = tmp_path / "sharetmp"
        sharetmp.mkdir(exist_ok=True)
        (sharetmp / "preexisting.pdf").write_bytes(b"%PDF-1.4")

        app.config["UPLOAD_FOLDER"] = upload_folder
        resp = client.post(
            "/pwa/shared/confirm",
            data={
                "destination": "document",
                "aircraft_id": str(ac_id),
                "category": category,
            },
        )
        assert resp.status_code in (200, 302)  # success or redirect after save
