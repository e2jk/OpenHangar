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
