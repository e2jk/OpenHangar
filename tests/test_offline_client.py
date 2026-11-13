"""Offline client wiring: read-only logbook snapshots, no offline editing.

Content assertions only (no browser): IndexedDB, service-worker and template
wiring. Behavioural coverage (real offline browsing) is the Playwright suite.
"""

from pathlib import Path

import pytest

_STATIC_DIR = Path(__file__).parent.parent / "app" / "static"
_TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"


def _js(name: str) -> str:
    return (_STATIC_DIR / "js" / name).read_text()


def _template(*parts: str) -> str:
    return _TEMPLATES_DIR.joinpath(*parts).read_text()


class TestOfflineDb:
    def test_exposes_only_snapshot_reads(self):
        content = _js("offline_db.js")
        assert (
            "window.OhOffline = {\n    getSnapshot: getSnapshot,\n    getPilotSnapshot: getPilotSnapshot\n  };"
            in content
        )

    def test_version_4_drops_the_write_stores(self):
        content = _js("offline_db.js")
        assert "_DB_VERSION = 4" in content
        assert "_WRITE_STORES = ['queue', 'outbox', 'pilot_outbox']" in content
        assert "db.deleteObjectStore(store)" in content
        assert "createObjectStore(store" not in content

    def test_snapshots_from_before_version_4_are_refetched(self):
        content = _js("offline_db.js")
        assert "event.oldVersion < 4" in content
        assert "objectStore('snapshots').clear()" in content
        assert "objectStore('pilot_snapshot').clear()" in content

    def test_legacy_unowned_database_is_deleted(self):
        content = _js("offline_db.js")
        assert "indexedDB.deleteDatabase(_DB_NAME)" in content
        assert "indexedDB.databases()" in content

    def test_snapshot_refresh_and_precache_of_the_offline_pages(self):
        content = _js("offline_db.js")
        assert "'/api/offline/aircraft/' + aircraftId + '/logbook'" in content
        assert "'/api/offline/pilot/logbook'" in content
        assert "OH_PRECACHE" in content
        assert "'/aircraft/' + aircraftId + '/logbook/offline'" in content
        assert "_precache('/pilot/logbook/offline')" in content

    @pytest.mark.parametrize(
        "removed",
        [
            "flush",
            "/sync",
            "/api/offline/csrf",
            "ohCanon",
            "resumeQuarantined",
            "Outbox",
        ],
    )
    def test_no_write_path_left(self, removed):
        assert removed not in _js("offline_db.js")


class TestOfflineLogbookRenderer:
    def test_renders_both_snapshots_read_only(self):
        content = _js("offline_logbook.js")
        assert "window.OhOffline.getSnapshot(aircraftId)" in content
        assert "window.OhOffline.getPilotSnapshot()" in content
        assert "cell.textContent = " in content
        assert "innerHTML" not in content
        assert "addEventListener('change'" not in content

    def test_follows_the_module_pattern(self):
        content = _js("offline_logbook.js")
        assert "if (!root || root.dataset.ohInited) return;" in content
        assert "addEventListener('htmx:afterSettle', init)" in content

    def test_editing_scripts_are_gone(self):
        for name in (
            "offline_workbench.js",
            "offline_pilot_workbench.js",
            "offline_changes.js",
        ):
            assert not (_STATIC_DIR / "js" / name).exists()


class TestPwaJs:
    @pytest.mark.parametrize(
        "removed",
        [
            "window.OhOffline.",
            "oh-flight-sync",
            "OH_SYNC_REQUESTED",
            "_syncQueue",
            "queue",
        ],
    )
    def test_no_offline_queue(self, removed):
        assert removed not in _js("pwa.js")


class TestServiceWorker:
    def test_caches_the_read_only_offline_pages(self):
        content = _js("sw.js")
        assert r"\/aircraft\/[A-Z0-9][A-Z0-9-]*\/flights$" in content
        assert r"\/aircraft\/[A-Z0-9][A-Z0-9-]*\/logbook\/offline$" in content
        assert r"\/pilot\/logbook\/offline$" in content
        assert "'/static/js/offline_logbook.js'" in content

    def test_no_background_sync_or_changes_page(self):
        content = _js("sw.js")
        assert "addEventListener('sync'" not in content
        assert r"\/offline\/changes$" not in content

    def test_verifies_identity_without_a_csrf_token(self):
        content = _js("sw.js")
        assert "new Request('/api/offline/identity'" in content
        assert "/api/offline/csrf" not in content


class TestBaseTemplate:
    def test_loads_offline_db_before_pwa_and_the_renderer(self):
        content = _template("base.html")
        assert content.index("offline_db.js") < content.index("js/pwa.js")
        assert "js/offline_logbook.js" in content

    @pytest.mark.parametrize(
        "removed",
        [
            "oh-pwa-queue-badge",
            "offline_workbench.js",
            "offline_changes.js",
            "queued1",
            "flightQueued",
            "pending offline changes",
        ],
    )
    def test_no_offline_queue_left(self, removed):
        assert removed not in _template("base.html")

    def test_fetch_wrapper_does_not_clobber_explicit_csrf_token(self):
        assert "hasToken" in _template("base.html")


class TestLogbookPagesTriggerSnapshots:
    def test_aircraft_logbook_list(self):
        content = _template("flights", "list.html")
        assert "data-oh-aircraft-id=" in content
        assert "offline.aircraft_logbook" in content

    def test_pilot_logbook(self):
        content = _template("pilots", "logbook.html")
        assert 'data-oh-pilot-logbook="1"' in content
        assert "offline.pilot_logbook" in content


class TestOfflineFormGuard:
    """Nothing can be saved offline, so every form is guarded. Behavioural
    coverage (blocked submit, htmx:sendError) is the Playwright suite."""

    def test_listens_for_submit_capturing(self):
        content = _js("offline_form_guard.js")
        assert "addEventListener('submit'" in content
        assert ", true)" in content
        assert "navigator.onLine" in content

    def test_listens_for_htmx_send_error(self):
        assert "htmx:sendError" in _js("offline_form_guard.js")

    def test_no_form_opts_out_any_more(self):
        assert "data-oh-offline-aware" not in _js("offline_form_guard.js")
        for parts in (
            ("flights", "flight_form.html"),
            ("pilots", "entry_form.html"),
            ("base.html",),
        ):
            assert "data-oh-offline-aware" not in _template(*parts)

    def test_base_html_loads_it(self):
        assert "offline_form_guard.js" in _template("base.html")

    def test_shows_proactive_banner_on_load_settle_and_connectivity_change(self):
        content = _js("offline_form_guard.js")
        assert "function updateProactiveBanners" in content
        for event in ("DOMContentLoaded", "htmx:afterSettle", "offline", "online"):
            assert f"addEventListener('{event}', updateProactiveBanners)" in content

    def test_does_not_disable_fields(self):
        content = _js("offline_form_guard.js")
        assert ".disabled" not in content
        assert "readOnly" not in content

    def test_skips_button_only_forms(self):
        content = _js("offline_form_guard.js")
        assert "function hasFillableField" in content
        assert 'input:not([type="hidden"])' in content

    def test_removes_banner_when_back_online(self):
        assert "function removeGuardAlert" in _js("offline_form_guard.js")
