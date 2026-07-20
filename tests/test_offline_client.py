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

    def test_offline_db_js_db_version_is_3(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "_DB_VERSION = 3" in content

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
        # The aircraft slot matches a numeric id OR a registration (see
        # AircraftRefConverter, app/utils.py).
        assert r"\/aircraft\/[A-Z0-9][A-Z0-9-]*\/flights$" in content
        assert r"\/aircraft\/[A-Z0-9][A-Z0-9-]*\/logbook\/offline$" in content
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


class TestOfflineChangesFiles:
    def test_offline_changes_js_exists(self):
        assert (_STATIC_DIR / "js" / "offline_changes.js").exists()

    def test_offline_changes_js_guards_root_element(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "root.dataset.ohInited" in content

    def test_offline_changes_js_reads_outbox_and_queue(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "window.OhOffline.getOutbox" in content
        assert "window.OhOffline.getQueue" in content

    def test_offline_changes_js_handles_conflict_and_duplicate(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "buildConflictArea" in content
        assert "buildDuplicateArea" in content
        assert "force_duplicate" in content

    def test_offline_changes_js_discard_actions(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "window.OhOffline.deleteOutbox" in content
        assert "window.OhOffline.deleteQueueEntry" in content

    def test_offline_changes_js_per_field_revert(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "window.OhOffline.updateOutboxRecord" in content
        assert "i18n.revert" in content

    def test_offline_changes_js_listens_for_progress_and_sync_events(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "oh-offline-sync-progress" in content
        assert "oh-offline-sync" in content

    def test_base_html_loads_offline_changes_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "offline_changes.js" in content


class TestPhase35QueueFixes:
    """38f — pre-existing Phase 35 queue bugs this phase closes."""

    def test_queued_entry_stores_form_action_for_replay(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "action: _flightForm.action" in content

    def test_replay_uses_stored_action_not_hardcoded_new(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "row.action || '/flights/new'" in content

    def test_replay_fetches_fresh_csrf_before_submitting(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "/api/offline/csrf" in content
        assert "fd.set('csrf_token'" in content

    def test_permanent_failure_marks_entry_and_stops_retrying(self):
        content = (_STATIC_DIR / "js" / "pwa.js").read_text()
        assert "row.status = 'error'" in content
        assert "row.status !== 'error'" in content

    def test_offline_changes_js_surfaces_failed_queue_entries(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "row.status === 'error'" in content
        assert "i18n.queueEntryFailed" in content


class TestPilotLogbookClient:
    """38i — pilot logbook client + UI (IndexedDB v3, "My logbook" section,
    standalone pilot workbench). Behavioural coverage is Playwright (38l)."""

    def test_offline_db_js_creates_pilot_stores(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "'pilot_snapshot'" in content
        assert "'pilot_outbox'" in content

    def test_offline_db_js_exposes_pilot_helpers(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        for name in (
            "getPilotSnapshot",
            "putPilotSnapshot",
            "getPilotOutbox",
            "upsertPilotOutboxForEntry",
            "deletePilotOutbox",
            "pilotOutboxCount",
            "ohCanonPilot",
        ):
            assert name in content

    def test_offline_db_js_upsert_outbox_accepts_pilot_delta(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "delta.pilot" in content

    def test_offline_db_js_sync_handles_pilot_missing(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "pilot_missing" in content

    def test_offline_db_js_flush_processes_pilot_outbox(self):
        content = (_STATIC_DIR / "js" / "offline_db.js").read_text()
        assert "_syncOnePilotRecord" in content
        assert "getPilotOutbox" in content

    def test_workbench_html_has_my_logbook_section(self):
        content = (_TEMPLATES_DIR / "offline" / "workbench.html").read_text()
        assert "data-pilot-fields" in content
        assert "data-pilot-no-entry" in content
        assert "data-pilot-field=" in content

    def test_offline_workbench_js_renders_pilot_section(self):
        content = (_STATIC_DIR / "js" / "offline_workbench.js").read_text()
        assert "renderPilotSection" in content
        assert "window.OhOffline.ohCanonPilot" in content

    def test_pilot_workbench_files_exist(self):
        assert (_TEMPLATES_DIR / "offline" / "pilot_workbench.html").exists()
        assert (_STATIC_DIR / "js" / "offline_pilot_workbench.js").exists()

    def test_pilot_workbench_js_guards_root_element(self):
        content = (_STATIC_DIR / "js" / "offline_pilot_workbench.js").read_text()
        assert "root.dataset.ohInited" in content

    def test_pilot_workbench_js_uses_pilot_outbox(self):
        content = (_STATIC_DIR / "js" / "offline_pilot_workbench.js").read_text()
        assert "window.OhOffline.getPilotSnapshot" in content
        assert "window.OhOffline.upsertPilotOutboxForEntry" in content

    def test_pilot_workbench_template_has_no_inline_script_nonce(self):
        content = (_TEMPLATES_DIR / "offline" / "pilot_workbench.html").read_text()
        assert "<script nonce" not in content

    def test_base_html_loads_pilot_workbench_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "offline_pilot_workbench.js" in content

    def test_sw_js_has_pilot_workbench_route_pattern(self):
        content = (_STATIC_DIR / "js" / "sw.js").read_text()
        assert r"\/pilot\/logbook\/offline$" in content

    def test_pilots_logbook_has_data_oh_pilot_logbook(self):
        content = (_TEMPLATES_DIR / "pilots" / "logbook.html").read_text()
        assert "data-oh-pilot-logbook=" in content

    def test_pilots_logbook_links_to_pilot_workbench(self):
        content = (_TEMPLATES_DIR / "pilots" / "logbook.html").read_text()
        assert "offline.pilot_workbench" in content


class TestOfflineChangesPilotExtension:
    """38j — offline-changes page extended to the pilot logbook: a third
    card family from pilot_outbox, inline pilot sub-diff on aircraft-logbook
    cards, and per-field conflict resolution across both sources. Full
    conflict UX (incl. pilot_missing) behaviour is Playwright (38l)."""

    def test_offline_changes_js_reads_pilot_outbox(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "window.OhOffline.getPilotOutbox" in content
        assert "renderPilotOutboxCard" in content

    def test_offline_changes_js_discards_pilot_outbox_entries(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "window.OhOffline.deletePilotOutbox" in content

    def test_offline_changes_js_renders_inline_pilot_diff(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "buildPilotDiffTable" in content

    def test_offline_changes_js_handles_pilot_conflicts(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "pilot_conflicts" in content
        assert "buildPilotOutboxConflictArea" in content

    def test_offline_changes_js_handles_pilot_missing(self):
        content = (_STATIC_DIR / "js" / "offline_changes.js").read_text()
        assert "buildPilotMissingArea" in content
        assert "pilot_missing" in content

    def test_changes_html_has_pilot_missing_strings(self):
        content = (_TEMPLATES_DIR / "offline" / "changes.html").read_text()
        assert "pilotMissingMsg" in content
        assert "keepFlightChanges" in content
        assert "myLogbookLabel" in content


class TestOfflineFormGuard:
    """38k — cross-cutting offline-submit guard. Behavioural coverage
    (actual blocked submit, htmx:sendError, the Phase 35 regression check)
    is Playwright (38l)."""

    def test_offline_form_guard_js_exists(self):
        assert (_STATIC_DIR / "js" / "offline_form_guard.js").exists()

    def test_offline_form_guard_js_listens_for_submit_capturing(self):
        content = (_STATIC_DIR / "js" / "offline_form_guard.js").read_text()
        assert "addEventListener('submit'" in content
        assert ", true)" in content

    def test_offline_form_guard_js_listens_for_htmx_send_error(self):
        content = (_STATIC_DIR / "js" / "offline_form_guard.js").read_text()
        assert "htmx:sendError" in content

    def test_offline_form_guard_js_respects_offline_aware_opt_out(self):
        content = (_STATIC_DIR / "js" / "offline_form_guard.js").read_text()
        assert "data-oh-offline-aware" in content

    def test_offline_form_guard_js_checks_navigator_online(self):
        content = (_STATIC_DIR / "js" / "offline_form_guard.js").read_text()
        assert "navigator.onLine" in content

    def test_base_html_loads_offline_form_guard_js(self):
        content = (_TEMPLATES_DIR / "base.html").read_text()
        assert "offline_form_guard.js" in content

    def test_flight_form_has_data_oh_offline_aware(self):
        content = (_TEMPLATES_DIR / "flights" / "flight_form.html").read_text()
        assert "data-oh-offline-aware=" in content

    def test_aircraft_workbench_has_data_oh_offline_aware(self):
        content = (_TEMPLATES_DIR / "offline" / "workbench.html").read_text()
        assert "data-oh-offline-aware=" in content

    def test_pilot_workbench_has_data_oh_offline_aware(self):
        content = (_TEMPLATES_DIR / "offline" / "pilot_workbench.html").read_text()
        assert "data-oh-offline-aware=" in content

    def test_pilot_entry_form_does_not_opt_out(self):
        """The standalone pilot entry_form.html is deliberately NOT
        offline-aware — the guard should still catch it."""
        content = (_TEMPLATES_DIR / "pilots" / "entry_form.html").read_text()
        assert "data-oh-offline-aware" not in content
