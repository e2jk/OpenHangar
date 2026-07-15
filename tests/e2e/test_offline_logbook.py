"""
Playwright e2e tests for Phase 38 — offline logbook editing.

Run with:  pytest --e2e tests/e2e/test_offline_logbook.py --override-ini='addopts='

These tests need real service worker + IndexedDB behaviour, so each uses its
own isolated browser context (service_workers not blocked) rather than the
shared session-scoped `browser_context` fixture (which blocks them for the
rest of the suite's speed/stability). Network offline/online is simulated
with `context.set_offline()`, which only affects that isolated context.

Gotcha diagnosed while writing these: a test that drives the app through a
real browser and then wants to verify server-side state should do so via
a real HTTP round trip (e.g. re-fetch the snapshot API), not by reading
the DB directly from this test's own process via
`with live_app.app_context(): db.session.get(...)`. The latter was
observed to return a stale value even after `db.session.close()` (and
`db.engine.dispose()` is unsafe to call here — it crashed the process,
presumably racing the server thread's own use of the same engine), while
console/response logging confirmed the sync POST and its `{"status":
"ok", ...}` response both carried the correct, already-applied value.
Root-caused while adding the 38h-l tests below: even a real HTTP GET to
the snapshot API can serve one stale read immediately after the sync
POST's commit — reproduced with server-side prints confirming the
commit and a `db.session.get` re-fetch both already showed the new
value while the *next* request's own fresh connection still returned
the old one; a second attempt ~300ms later always sees the committed
value. Consistent with the file-based SQLite (NullPool +
check_same_thread=False) trade-off documented below, not a sync bug.
`_wait_for_snapshot_field` below retries the verification GET a few
times before asserting, for exactly this reason — use it (or the same
pattern) for any new verification read rather than a single fetch.
Also found and fixed along the way: the conflict-resolution test
originally edited the workbench's first (oldest, ascending-sort) row
while writing the "concurrent" change to `seed["fe_flt"]` (the *newest*
flight) — two different rows, so no real conflict was ever exercised;
it now reads the edited row's actual id from the DOM first.

Known residual issues (local environment only):
- `TestOfflineChangesPage`, `TestWorkbenchConflictResolution`,
  `TestPhase35RegressionEditReplaysToEditUrl`, and the 38l pilot-logbook
  additions (`TestAircraftWorkbenchPilotSection`,
  `TestStandalonePilotWorkbench`, `TestOfflineChangesShowsAllCardFamilies`,
  `TestOfflineFormGuardBlocksNonAwareSubmit`) intermittently error at
  setup/mid-test with a SQLite cursor read
  `IndexError` (deep in sqlalchemy's cyextension row processor) on an
  unrelated page's context processor — reproduced even on the plain
  post-login dashboard load, so unrelated to this phase's own routes. The
  in-process `live_server` fixture backs onto a file-based SQLite DB via
  NullPool + check_same_thread=False, a documented concurrency trade-off
  for local speed; the extra background traffic this phase's
  snapshot-refresh/precache machinery generates seems to be what surfaces
  it — worse with more tests/fixtures sharing one `live_server` in a
  single run, so these pass reliably in isolation or small groups but can
  error when the full file runs back-to-back. The `offline_page` fixture
  retries the initial dashboard load once to absorb some of this. CI's
  e2e run (.github/workflows/ci.yml) targets a Docker/PostgreSQL-backed
  server instead and has not been observed to hit this.
- `TestWorkbenchConflictResolution` intermittently applies the server's
  value instead of the (default-selected) offline value on "Apply
  resolution" — likely a click/render race on the conflict card rather
  than a resolution-logic bug (the conflict itself, and the card showing
  both candidate values, were confirmed correct); needs a closer look at
  whether the click can land before `buildConflictArea`'s radio default
  is attached.
"""

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def offline_page(browser_context, live_server_url, seed):
    """Authenticated page in its own context, with service workers enabled."""
    import pyotp

    ctx = browser_context.browser.new_context(
        base_url=live_server_url,
        ignore_https_errors=live_server_url.startswith("https://"),
    )
    ctx.set_default_timeout(10000)
    pg = ctx.new_page()
    pg.goto(f"{live_server_url}/login")
    pg.wait_for_load_state("networkidle")
    pg.fill('input[name="email"]', "admin@openhangar.dev")
    pg.fill('input[name="password"]', "openhangar-dev-1")
    pg.click('button[type="submit"]')
    pg.wait_for_load_state("networkidle")
    if pg.locator("#totp_code").count() > 0:
        # totp_autosubmit.js calls form.requestSubmit() as soon as the 6th
        # digit lands — the submit button is never clicked in practice.
        # Filling then also clicking races the auto-submit and can double
        # -submit (see AGENTS.md "TOTP login form auto-submits" gotcha);
        # just fill and wait for the navigation the auto-submit triggers.
        #
        # A code computed right before typing can still straddle the 30 s
        # TOTP window boundary by the time `press_sequentially` finishes and
        # the server validates it — more likely under CI/Docker load, where
        # typing and request round-trips are slower. Retry once with a
        # freshly computed code rather than a single unconditional wait.
        for _totp_attempt in range(2):
            code = pyotp.TOTP(seed["totp_secret"]).now()
            pg.locator("#totp_code").fill("")
            pg.locator("#totp_code").press_sequentially(code)
            try:
                pg.wait_for_url(lambda url: "/login" not in url, timeout=8000)
                break
            except Exception:
                if _totp_attempt == 1:
                    raise
                pg.goto(f"{live_server_url}/login")
                pg.wait_for_load_state("networkidle")
                pg.fill('input[name="email"]', "admin@openhangar.dev")
                pg.fill('input[name="password"]', "openhangar-dev-1")
                pg.click('button[type="submit"]')
                pg.wait_for_load_state("networkidle")

    # The post-login redirect can occasionally land on a transient 500 from
    # a rare SQLite read hiccup in this local (non-Docker) e2e server mode
    # (see module docstring) — confirm a real dashboard load before handing
    # off, retrying once if needed, so that flakiness doesn't leak into the
    # actual test logic below.
    for _attempt in range(3):
        resp = pg.goto(f"{live_server_url}/")
        if resp and resp.status == 200:
            break
        pg.wait_for_timeout(300)
    pg.wait_for_load_state("networkidle")

    yield pg
    ctx.close()


def _outbox_count(page):
    return page.evaluate("() => window.OhOffline.getOutbox().then(r => r.length)")


def _wait_for_precache(page, path, timeout=10000):
    """Poll the Cache Storage API until some cache holds a response for
    *path* — the SW precaches /offline/changes (and the workbench URL) via
    an OH_PRECACHE message sent when visiting the aircraft logbook list
    online, but that happens in the background, so navigating there while
    offline needs to wait for it first."""
    page.wait_for_function(
        """(path) => caches.keys().then(keys =>
            Promise.all(keys.map(k => caches.open(k).then(c => c.match(path))))
                .then(matches => matches.some(m => m))
        )""",
        arg=path,
        timeout=timeout,
    )


def _wait_for_snapshot_field(
    page, ac_id, flight_id, field, expected, retries=8, delay=300
):
    """Poll the aircraft snapshot API for `field == expected` on `flight_id`.

    A real HTTP GET right after a sync POST's commit can still serve one
    stale read on this local SQLite (NullPool, check_same_thread=False)
    harness — see the module docstring. Returns the last-seen entry
    regardless of outcome so the caller's assertion produces a useful
    failure message rather than a bare timeout.
    """
    entry = None
    for _ in range(retries):
        snapshot = page.evaluate(
            "(id) => fetch('/api/offline/aircraft/' + id + '/logbook').then(r => r.json())",
            ac_id,
        )
        entry = next(e for e in snapshot["entries"] if e["id"] == flight_id)
        if entry["fields"][field] == expected:
            break
        page.wait_for_timeout(delay)
    return entry


def _wait_for_pilot_snapshot_field(
    page, entry_id, field, expected, retries=8, delay=300
):
    """Same as `_wait_for_snapshot_field`, for the standalone pilot-logbook
    snapshot (no aircraft_id involved)."""
    entry = None
    for _ in range(retries):
        snapshot = page.evaluate(
            "() => fetch('/api/offline/pilot/logbook').then(r => r.json())"
        )
        entry = next(e for e in snapshot["entries"] if e["id"] == entry_id)
        if entry["fields"][field] == expected:
            break
        page.wait_for_timeout(delay)
    return entry


def _edit_nature_of_flight(page, value):
    """nature_of_flight lives in the workbench row's collapsed detail
    section — open it before interacting with the field."""
    first_row = page.locator("#oh-wb-tbody tr[data-row]:first-child")
    first_row.locator("[data-toggle-detail]").click()
    detail_row = first_row.locator("xpath=following-sibling::tr[1]")
    field = detail_row.locator('[data-field="nature_of_flight"]')
    field.wait_for(state="visible")
    field.fill(value)
    field.dispatch_event("change")
    # The change handler's outbox write is async (real IndexedDB I/O) —
    # give it time to land instead of racing it with an immediate check.
    page.wait_for_function(
        "() => window.OhOffline.getOutbox().then(r => r.length > 0)",
        timeout=10000,
    )


class TestWorkbenchOfflineEditAndReconnectSync:
    def test_offline_edit_queues_then_auto_syncs_when_back_online(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        ac_id = seed["ac_flt"]

        # Visiting the workbench directly caches its own snapshot (it also
        # carries data-oh-aircraft-id) — no need for a separate list-page
        # visit first.
        page.goto(f"{live_server_url}/aircraft/{ac_id}/logbook/offline")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#oh-wb-tbody tr[data-row]")

        page.context.set_offline(True)
        try:
            _edit_nature_of_flight(page, "E2E offline edit")
            assert _outbox_count(page) == 1

            flight_id = int(
                page.locator("#oh-wb-tbody tr[data-row]:first-child").get_attribute(
                    "data-flight-id"
                )
            )
        finally:
            page.context.set_offline(False)

        # Reconnect: flush() fires on the `online` event and syncs the
        # queued edit.
        page.wait_for_function(
            "() => window.OhOffline.getOutbox().then(r => r.length === 0)",
            timeout=10000,
        )

        # Verify via a real HTTP round trip through the snapshot API rather
        # than a direct DB read from this test's own process — see the
        # module docstring for why this needs a retry-tolerant helper.
        entry = _wait_for_snapshot_field(
            page, ac_id, flight_id, "nature_of_flight", "E2E offline edit"
        )
        assert entry["fields"]["nature_of_flight"] == "E2E offline edit"


class TestOfflineChangesPage:
    def test_lists_pending_edit_and_discard_removes_it(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        ac_id = seed["ac_flt"]

        # Visiting the aircraft logbook list online triggers the SW
        # OH_PRECACHE message for the workbench + /offline/changes — wait
        # for it so the later offline navigation to /offline/changes can
        # actually load.
        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")
        _wait_for_precache(page, "/offline/changes")

        page.goto(f"{live_server_url}/aircraft/{ac_id}/logbook/offline")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#oh-wb-tbody tr[data-row]")

        page.context.set_offline(True)
        try:
            _edit_nature_of_flight(page, "Pending review")
            assert _outbox_count(page) == 1

            page.goto(f"{live_server_url}/offline/changes")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#oh-changes-list .ac-form-card")
            assert "Pending review" in page.content()

            page.click(
                "#oh-changes-list button:has-text('Discard'), #oh-changes-list button:has-text('Ignorer'), #oh-changes-list button:has-text('Negeren')"
            )
            page.wait_for_function(
                "() => window.OhOffline.getOutbox().then(r => r.length === 0)",
                timeout=10000,
            )
        finally:
            page.context.set_offline(False)


class TestWorkbenchConflictResolution:
    def test_conflicting_field_offers_choice_and_applies_offline_value(
        self, offline_page, live_server_url, live_app, seed
    ):
        from models import FlightEntry, db  # pyright: ignore[reportMissingImports]

        page = offline_page
        ac_id = seed["ac_flt"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/logbook/offline")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#oh-wb-tbody tr[data-row]")

        page.context.set_offline(True)
        try:
            _edit_nature_of_flight(page, "Offline choice")
            assert _outbox_count(page) == 1

            # The row the workbench edits is the aircraft's oldest flight
            # (ascending sort) — read its real id rather than assuming which
            # seeded flight that is, so the "concurrent" edit below lands on
            # the exact same row (otherwise there is never a real conflict).
            flight_id = int(
                page.locator("#oh-wb-tbody tr[data-row]:first-child").get_attribute(
                    "data-flight-id"
                )
            )

            # Simulate a concurrent online edit by a second session, made
            # directly against the DB (this test's own "second session").
            # db.session.close() first: this test's scoped session can
            # otherwise hold a stale SQLite read snapshot predating
            # whatever the running server has already committed.
            with live_app.app_context():
                db.session.close()
                fe = db.session.get(FlightEntry, flight_id)
                fe.nature_of_flight = "Server choice"
                db.session.commit()
        finally:
            page.context.set_offline(False)

        page.goto(f"{live_server_url}/offline/changes")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#oh-changes-list .ac-form-card")
        assert "Server choice" in page.content()
        assert "Offline choice" in page.content()

        # Default radio selection is the offline value — just apply.
        page.click(
            "#oh-changes-list button:has-text('Apply resolution'), "
            "#oh-changes-list button:has-text('Appliquer'), "
            "#oh-changes-list button:has-text('Oplossing')"
        )
        page.wait_for_function(
            "() => window.OhOffline.getOutbox().then(r => r.length === 0)",
            timeout=10000,
        )

        # Verify via the snapshot API (real HTTP round trip) rather than a
        # direct DB read from this test's own process — see the module
        # docstring for why this needs a retry-tolerant helper.
        entry = _wait_for_snapshot_field(
            page, ac_id, flight_id, "nature_of_flight", "Offline choice"
        )
        assert entry["fields"]["nature_of_flight"] == "Offline choice"


class TestPhase35RegressionEditReplaysToEditUrl:
    def test_offline_edit_via_plain_form_replays_to_edit_not_new(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        flight_id = seed["fe_flt"]

        page.goto(f"{live_server_url}/flights/{flight_id}/edit")
        page.wait_for_load_state("networkidle")

        page.context.set_offline(True)
        try:
            page.fill("#notes", "38f regression check")
            page.click('#flight-form button[type="submit"]')
            page.wait_for_timeout(500)  # let the offline-queue handler run

            action = page.evaluate(
                "() => window.OhOffline.getQueue().then(r => r[r.length - 1] && r[r.length - 1].action)"
            )
            assert action is not None
            assert action.endswith(f"/flights/{flight_id}/edit")
        finally:
            page.context.set_offline(False)


class TestAircraftWorkbenchPilotSection:
    """38i/38l — the "My logbook" section on an aircraft-workbench row for
    a linked PilotLogbookEntry (see conftest.py's `pe_linked` E2E extra,
    linked to `seed["fe_flt"]`)."""

    def test_offline_edit_of_linked_pilot_field_syncs_on_reconnect(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        ac_id = seed["ac_flt"]
        flight_id = seed["fe_flt"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/logbook/offline")
        page.wait_for_load_state("networkidle")
        row = page.locator(f'#oh-wb-tbody tr[data-row][data-flight-id="{flight_id}"]')
        row.wait_for(state="visible")

        # Docker/CI E2E mode seeds from the dev seed's existing data rather
        # than creating a guaranteed linked entry (see conftest.py's
        # `pe_linked` E2E-only extra, which only exists in local/in-process
        # mode) — skip cleanly if this flight has none here.
        snapshot = page.evaluate(
            "(id) => fetch('/api/offline/aircraft/' + id + '/logbook').then(r => r.json())",
            ac_id,
        )
        entry_data = next(
            (e for e in snapshot["entries"] if e["id"] == flight_id), None
        )
        if not entry_data or "pilot" not in entry_data:
            pytest.skip(
                "no linked PilotLogbookEntry for this flight in this environment"
            )

        page.context.set_offline(True)
        try:
            row.locator("[data-toggle-detail]").click()
            detail_row = row.locator("xpath=following-sibling::tr[1]")
            field = detail_row.locator('[data-pilot-field="night_time"]')
            field.wait_for(state="visible")
            field.fill("0.7")
            field.dispatch_event("change")
            page.wait_for_function(
                "() => window.OhOffline.getOutbox().then(r => r.length > 0)",
                timeout=10000,
            )
        finally:
            page.context.set_offline(False)

        page.wait_for_function(
            "() => window.OhOffline.getOutbox().then(r => r.length === 0)",
            timeout=10000,
        )

        entry = None
        for _ in range(8):
            snapshot = page.evaluate(
                "(id) => fetch('/api/offline/aircraft/' + id + '/logbook').then(r => r.json())",
                ac_id,
            )
            entry = next(e for e in snapshot["entries"] if e["id"] == flight_id)
            if entry.get("pilot", {}).get("fields", {}).get("night_time") == "0.7":
                break
            page.wait_for_timeout(300)
        assert entry is not None
        assert entry["pilot"]["fields"]["night_time"] == "0.7"


class TestStandalonePilotWorkbench:
    """38i/38l — /pilot/logbook/offline: a standalone FSTD entry (see
    conftest.py's `pe_standalone_fstd` E2E extra)."""

    def test_offline_edit_of_fstd_entry_syncs_on_reconnect(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        entry_id = seed["pe_standalone_fstd_id"]
        if entry_id is None:
            pytest.skip("no standalone FSTD entry in this environment's dev seed")

        page.goto(f"{live_server_url}/pilot/logbook/offline")
        page.wait_for_load_state("networkidle")
        row = page.locator(f'#oh-pwb-tbody tr[data-row][data-entry-id="{entry_id}"]')
        row.wait_for(state="visible")

        # FSTD-only column should be visible, flight-only columns hidden.
        assert row.locator(".oh-pwb-fstd-only").first.is_visible()

        page.context.set_offline(True)
        try:
            row.locator("[data-toggle-detail]").click()
            detail_row = row.locator("xpath=following-sibling::tr[1]")
            field = detail_row.locator('[data-field="instrument_time"]')
            field.wait_for(state="visible")
            field.fill("0.5")
            field.dispatch_event("change")
            page.wait_for_function(
                "() => window.OhOffline.getPilotOutbox().then(r => r.length > 0)",
                timeout=10000,
            )
        finally:
            page.context.set_offline(False)

        page.wait_for_function(
            "() => window.OhOffline.getPilotOutbox().then(r => r.length === 0)",
            timeout=10000,
        )

        entry = _wait_for_pilot_snapshot_field(page, entry_id, "instrument_time", "0.5")
        assert entry["fields"]["instrument_time"] == "0.5"


class TestOfflineChangesShowsAllCardFamilies:
    def test_pilot_outbox_card_appears_alongside_aircraft_card(
        self, offline_page, live_server_url, seed
    ):
        page = offline_page
        ac_id = seed["ac_flt"]
        entry_id = seed["pe_standalone_fstd_id"]
        if entry_id is None:
            pytest.skip("no standalone FSTD entry in this environment's dev seed")

        # Precache /offline/changes for offline navigation later.
        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")
        _wait_for_precache(page, "/offline/changes")

        page.goto(f"{live_server_url}/pilot/logbook/offline")
        page.wait_for_load_state("networkidle")
        row = page.locator(f'#oh-pwb-tbody tr[data-row][data-entry-id="{entry_id}"]')
        row.wait_for(state="visible")

        page.context.set_offline(True)
        try:
            row.locator("[data-toggle-detail]").click()
            detail_row = row.locator("xpath=following-sibling::tr[1]")
            field = detail_row.locator('[data-field="remarks"]')
            field.wait_for(state="visible")
            field.fill("Changes-page check")
            field.dispatch_event("change")
            page.wait_for_function(
                "() => window.OhOffline.getPilotOutbox().then(r => r.length > 0)",
                timeout=10000,
            )

            page.goto(f"{live_server_url}/offline/changes")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#oh-changes-list .ac-form-card")
            assert "Changes-page check" in page.content()
        finally:
            page.context.set_offline(False)


class TestOfflineFormGuardBlocksNonAwareSubmit:
    """38k/38l — a non-offline-aware form (the standalone pilot
    entry_form.html) submitted while offline is blocked with a friendly
    message instead of a raw network error."""

    def test_guard_blocks_submit_and_shows_message(self, offline_page, live_server_url):
        page = offline_page
        page.goto(f"{live_server_url}/pilot/logbook/new")
        page.wait_for_load_state("networkidle")

        page.context.set_offline(True)
        try:
            page.fill("#date", "2024-01-01")
            page.click('form button[type="submit"]')
            page.wait_for_selector("[data-oh-guard-alert]", timeout=5000)
            assert "/pilot/logbook/new" in page.url
        finally:
            page.context.set_offline(False)
