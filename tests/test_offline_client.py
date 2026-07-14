"""Tests for Phase 38c — offline logbook client data layer.

Content assertions only (no browser): IndexedDB/service-worker/template
wiring. Behavioural coverage (actual offline browsing) is Playwright (38g).
"""

from pathlib import Path

_STATIC_DIR = Path(__file__).parent.parent / "app" / "static"
_TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"


class TestOfflineDbFile:
    def test_offline_db_js_exists(self):
        assert (_STATIC_DIR / "js" / "offline_db.js").exists()

    def test_offline_db_js_defines_oh_offline_global(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "window.OhOffline" in content

    def test_offline_db_js_creates_snapshots_and_outbox_stores(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "'snapshots'" in content
        assert "'outbox'" in content
        assert "'queue'" in content

    def test_offline_db_js_db_version_is_2(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "_DB_VERSION = 2" in content

    def test_offline_db_js_exposes_oh_canon(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "ohCanon" in content

    def test_offline_db_js_sends_precache_message(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "OH_PRECACHE" in content
        assert "logbook/offline" in content
        assert "/offline/changes" in content


class TestPwaJsUsesOhOffline:
    def test_pwa_js_no_longer_owns_db_version(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "_DB_VERSION" not in content

    def test_pwa_js_reads_queue_through_oh_offline(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "window.OhOffline.getQueue" in content
        assert "window.OhOffline.addQueueEntry" in content
        assert "window.OhOffline.deleteQueueEntry" in content

    def test_pwa_js_combines_badge_with_outbox_count(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "window.OhOffline.outboxCount" in content


class TestServiceWorkerOfflineLogbookRoutes:
    def test_sw_js_has_new_route_patterns(self):
        content = (_STATIC_DIR / "js" / "sw.js").read_text()
        assert r"\/aircraft\/\d+\/flights$" in content
        assert r"\/aircraft\/\d+\/logbook\/offline$" in content
        assert r"\/offline\/changes$" in content

    def test_sw_js_has_precache_message_handler(self):
        content = (_STATIC_DIR / "js" / "sw.js").read_text()
        assert "OH_PRECACHE" in content
        assert "addEventListener('message'" in content


class TestBaseTemplateLoadsOfflineDbBeforePwa:
    def test_offline_db_js_loaded(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "offline_db.js" in content

    def test_offline_db_js_loaded_before_pwa_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert content.index("offline_db.js") < content.index("js/pwa.js")


class TestAircraftLogbookListDataAttribute:
    def test_flights_list_has_data_oh_aircraft_id(self):
        content = (_TEMPLATES_DIR / "flights" / "list.html").read_text()
        assert "data-oh-aircraft-id=" in content

    def test_flights_list_links_to_workbench(self):
        content = (_TEMPLATES_DIR / "flights" / "list.html").read_text()
        assert "offline.workbench" in content


class TestFlushSyncEngine:
    """OhOffline.flush() (pulled forward from 38e — the workbench needs it
    to make edits save immediately when online)."""

    def test_offline_db_js_exposes_flush(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "flush: flush" in content

    def test_flush_fetches_fresh_csrf_per_batch(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "/api/offline/csrf" in content

    def test_flush_posts_to_sync_endpoint(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "/sync" in content
        assert "X-CSRFToken" in content

    def test_flush_handles_all_documented_statuses(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        for status in ("'ok'", "'conflict'", "'duplicate'"):
            assert status in content

    def test_flush_fires_sync_event(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "oh-offline-sync" in content

    def test_flush_triggers_on_online_event_and_sw_message(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "addEventListener('online'" in content
        assert "OH_SYNC_REQUESTED" in content


class TestBaseHtmlCsrfWrapperRespectsExplicitToken:
    def test_fetch_wrapper_does_not_clobber_explicit_csrf_token(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "hasToken" in content


class TestOfflineWorkbenchFiles:
    def test_offline_workbench_js_exists(self):
        assert (_STATIC_DIR / "js" / "offline_workbench.js").exists()

    def test_offline_workbench_js_uses_oh_canon(self):
        content = (_STATIC_DIR / "js" / "offline_workbench.js").read_text()
        assert "window.OhOffline.ohCanon" in content

    def test_offline_workbench_js_calls_flush_after_edit(self):
        content = (_STATIC_DIR / "js" / "offline_workbench.js").read_text()
        assert "window.OhOffline.flush" in content

    def test_offline_workbench_js_has_continuity_check(self):
        content = (_STATIC_DIR / "js" / "offline_workbench.js").read_text()
        assert "CONTINUITY_PAIRS" in content

    def test_offline_workbench_js_guards_root_element(self):
        content = (_STATIC_DIR / "js" / "offline_workbench.js").read_text()
        assert "root.dataset.ohInited" in content

    def test_base_html_loads_offline_workbench_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "offline_workbench.js" in content
