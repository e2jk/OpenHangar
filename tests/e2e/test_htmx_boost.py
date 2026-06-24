"""
E2E tests for HTMX hx-boost SPA navigation.

Verifies that:
- Link clicks swap the <body> in-place rather than triggering full page reloads.
- The page title updates after each swap.
- Page-specific JS modules re-initialize via htmx:afterSettle.
- hx-boost body swaps are distinguishable from full page reloads.

Technique — sentinel variable:
  A JS variable set on the current page survives an hx-boost body swap
  (the window object is not replaced) but is destroyed by a full page reload
  (new JS context). Setting window.__htmxSentinel before a navigation and
  checking it after is the most direct way to distinguish the two cases.

Navigation anchor:
  The brand link (<a class="navbar-brand" href="/">) sits outside the
  collapsible navbar and is always visible at any viewport width. Tests that
  need to navigate via hx-boost use this link as a reliable click target.

  The Add-aircraft button (<a href="/aircraft/new">) lives in the content area
  of the aircraft list page and is always visible for admin/owner users.

Run with:  pytest --e2e tests/e2e/test_htmx_boost.py --override-ini='addopts='
"""

import pytest

pytestmark = pytest.mark.e2e


class TestHtmxBodySwap:
    """Prove that hx-boost intercepts link clicks and swaps the body
    without a full page reload."""

    def test_nav_link_swaps_body_not_full_reload(self, logged_in_page, live_server_url):
        """A JS sentinel set before clicking a link must survive the
        navigation — proving the body was swapped in-place and the window
        object (along with all its JS state) was not replaced.

        Starts at /aircraft/ and uses the brand link (always visible) to
        navigate back to /."""
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        # Brand link (a.navbar-brand) is outside the collapsible nav — always visible
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "JS sentinel destroyed — navigation triggered a full page reload "
            "instead of an hx-boost body swap"
        )

    def test_page_title_updates_after_body_swap(self, logged_in_page, live_server_url):
        """HTMX must update <title> when swapping the body — the title on
        the aircraft list page must differ from the dashboard title."""
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")
        aircraft_title = page.title()

        page.locator("a.navbar-brand").click()
        # Wait for the URL to become exactly "/" — "**/" would also match the
        # current "/aircraft/" URL and return immediately, causing a race where
        # page.title() is read before HTMX has finished updating <title>.
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        dashboard_title = page.title()
        assert aircraft_title != dashboard_title, (
            "Page title did not change after hx-boost body swap"
        )

    def test_widget_reinitializes_via_aftersettle(
        self, logged_in_page, live_server_url
    ):
        """After an hx-boost navigation, page-specific JS must re-initialize
        via htmx:afterSettle.

        Path: /aircraft/ → /aircraft/new via the Add Aircraft link.
        HTMX 2.x attaches click handlers to specific elements at processing
        time (querySelectorAll, not broad event delegation), so we use a real
        link that HTMX has already registered. The navigation must be a body
        swap (sentinel survives). On /aircraft/new the fuel-type hint from
        aircraft_form.js must respond to dropdown changes, proving the module
        re-initialized via htmx:afterSettle."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        # The Add Aircraft link is processed by HTMX at page load, so clicking it
        # triggers an hx-boost body swap rather than a full page reload.
        page.locator("a[href='/aircraft/new']").first.click()
        page.wait_for_url("**/aircraft/new**", timeout=10000)
        page.wait_for_load_state("networkidle")

        # The navigation must have been an hx-boost body swap, not a full reload
        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "Sentinel destroyed — navigation to /aircraft/new triggered a full page reload "
            "instead of an hx-boost body swap"
        )

        # aircraft_form.js must have re-initialized via htmx:afterSettle.
        # htmx:afterSettle fires 20 ms after the body swap; network can go idle
        # before the settle timer fires, so wait_for_function is required here
        # (a direct assert right after networkidle would be a race condition).
        fuel_select = page.locator("#fuel_type")
        pw_expect(fuel_select).to_be_visible()
        fuel_select.select_option("mogas")
        try:
            page.wait_for_function(
                "() => (document.getElementById('fuel_type_hint')?.textContent || '')"
                ".includes('0.74')",
                timeout=3000,
            )
        except Exception as exc:
            raise AssertionError(
                "Fuel type hint did not update after selecting mogas — "
                "aircraft_form.js did not re-initialize via htmx:afterSettle "
                "after hx-boost navigation"
            ) from exc


class TestSentinelTechnique:
    """Validate the two sides of the sentinel technique: hx-boost body swaps
    preserve the sentinel while full page reloads destroy it.

    This proves that tests 1-3 can reliably distinguish the two navigation
    types — a body swap that leaves the window intact versus a full reload
    that creates a fresh JS context."""

    def test_full_reload_destroys_sentinel_unlike_body_swap(
        self, logged_in_page, live_server_url
    ):
        """Two-phase proof of the sentinel technique:

        Phase 1 — hx-boost body swap (brand link → /):
          The sentinel must survive, confirming the window was not replaced.

        Phase 2 — full page reload (page.goto → /aircraft/):
          The sentinel must be destroyed, confirming a new JS context was created.

        Together these two phases prove the sentinel reliably separates the two
        navigation types and that the hx-boost body-swap and full-reload paths
        behave differently."""
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        # Phase 1: hx-boost body swap — sentinel must survive
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "Phase 1 failed: sentinel destroyed by hx-boost body swap (expected survival)"
        )

        # Phase 2: full page reload — sentinel must be destroyed
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        sentinel = page.evaluate("() => window.__htmxSentinel")
        assert sentinel is None, (
            "Phase 2 failed: sentinel survived full page reload (expected destruction)"
        )


class TestMapReInitAfterHtmxNavigation:
    """flight_tracks_map.js must re-initialize the Leaflet map after hx-boost."""

    def test_dashboard_map_reloads_after_htmx_back(
        self, logged_in_page, live_server_url
    ):
        """Navigate dashboard → aircraft list → dashboard via hx-boost.

        Leaflet sets class 'leaflet-container' on #tracks-map when it
        initialises. After the body swap back to the dashboard, the new
        #tracks-map div comes from the server with no Leaflet classes; the
        htmx:afterSettle listener in flight_tracks_map.js must re-run init()
        and restore 'leaflet-container'. An empty div means re-init failed.
        """
        page = logged_in_page

        # Full page load on the dashboard
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        # Skip if there are no GPS tracks (the map card won't render at all)
        if page.locator("#tracks-map").count() == 0:
            pytest.skip(
                "No GPS tracks in seed — flight-tracks map absent from dashboard"
            )

        # Leaflet must have initialised on the first (full-page) load
        assert page.evaluate(
            "() => document.getElementById('tracks-map').classList.contains('leaflet-container')"
        ), "Leaflet did not initialise on the initial dashboard load"

        # hx-boost swap 1: dashboard → aircraft list
        page.locator("a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=10000)
        page.wait_for_load_state("networkidle")

        # hx-boost swap 2: aircraft list → dashboard
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        # htmx:afterSettle fires 20 ms after the body swap; wait for Leaflet
        # to add 'leaflet-container' instead of asserting immediately after
        # networkidle (the network can go idle before the settle timer fires).
        try:
            page.wait_for_function(
                "() => { var el = document.getElementById('tracks-map');"
                " return !!el && el.classList.contains('leaflet-container'); }",
                timeout=5000,
            )
        except Exception as exc:
            raise AssertionError(
                "Leaflet did not re-initialise after hx-boost navigation back to "
                "the dashboard — #tracks-map is empty"
            ) from exc

    def test_dashboard_map_reloads_after_browser_back(
        self, logged_in_page, live_server_url
    ):
        """Leaflet must re-initialize when returning to the dashboard via browser Back.

        The existing test covers the htmx:afterSettle path (triggered by a
        link-click forward navigation). This test covers the htmx:historyRestore
        path (triggered by the browser Back button). flight_tracks_map.js must
        listen to both events; if htmx:historyRestore is ever dropped from that
        file, this test catches the regression while the other test stays green.
        """
        page = logged_in_page

        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        if page.locator("#tracks-map").count() == 0:
            pytest.skip(
                "No GPS tracks in seed — flight-tracks map absent from dashboard"
            )

        assert page.evaluate(
            "() => document.getElementById('tracks-map').classList.contains('leaflet-container')"
        ), "Leaflet did not initialise on the initial dashboard load"

        # hx-boost swap: dashboard → /aircraft/
        page.locator("a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=10000)
        page.wait_for_load_state("networkidle")

        # Sentinel: an HTMX history restore keeps the window object alive;
        # a full page reload destroys it.  Without this guard, go_back()
        # triggering a reload would re-init Leaflet via DOMContentLoaded and
        # the test would pass vacuously without exercising historyRestore.
        page.evaluate("window.__backSentinel = 'alive'")

        # Browser Back: HTMX intercepts popstate → htmx:historyRestore fires →
        # ui.js dispatches a synthetic htmx:afterSettle → flight_tracks_map.js
        # init() runs via that event.
        page.go_back()
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__backSentinel === 'alive'"), (
            "browser Back triggered a full page reload instead of HTMX history "
            "restore — the test did not exercise the htmx:historyRestore path"
        )

        try:
            page.wait_for_function(
                "() => { var el = document.getElementById('tracks-map');"
                " return !!el && el.classList.contains('leaflet-container'); }",
                timeout=5000,
            )
        except Exception as exc:
            raise AssertionError(
                "Leaflet did not re-initialise after browser Back to the dashboard "
                "— flight_tracks_map.js may not listen to htmx:historyRestore"
            ) from exc


class TestHistoryRestoreCSP:
    """Browser Back must not produce style-src-attr CSP violations.

    When HTMX intercepts a link click it first serialises the current page DOM
    to localStorage via htmx:beforeHistorySave. If that snapshot contains inline
    style= attributes (e.g. from Leaflet map panes or the smart-navbar scroll
    handler) the history-restore settle step calls setAttribute('style', …) for
    each element whose ID appears in both the stored snapshot and the page being
    left — each unique value triggers a separate style-src-attr violation because
    the CSP carries style-src-attr 'none'.

    Unlike forward hx-boost swaps (which go through htmx:beforeSwap), history
    restore calls Ve() directly without firing htmx:beforeSwap, so the
    htmx:beforeSwap cleanup handler in ui.js is never invoked. The fix lives in
    htmx:beforeHistorySave: strip all inline styles before the snapshot is saved.
    """

    def test_no_csp_errors_on_browser_back_from_flight_form(
        self, logged_in_page, live_server_url
    ):
        """Navigate dashboard → /flights/new → browser Back; no CSP violations.

        The dashboard initialises a Leaflet map that writes many inline style=
        attributes to map panes and tile containers.  Unless those are stripped
        before the htmx:beforeHistorySave snapshot is written, the settle step
        on Back re-applies them and the browser reports a violation for each
        distinct style value — explaining the large number of different SHA
        hashes seen in a single back-navigation.
        """
        page = logged_in_page
        errors: list = []
        page.on(
            "console", lambda msg: errors.append(msg) if msg.type == "error" else None
        )

        # Full page load — let Leaflet (and any other JS) initialise and write
        # inline style= attributes so that the htmx:beforeHistorySave snapshot
        # produced during the next navigation is realistic.
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")
        errors.clear()

        # hx-boost swap: dashboard → /flights/new.  HTMX serialises the current
        # dashboard DOM (with Leaflet inline styles) to localStorage here.
        page.locator("a[href='/flights/new']").first.click()
        page.wait_for_url("**/flights/new**", timeout=10000)
        page.wait_for_load_state("networkidle")
        errors.clear()  # ignore any errors from the forward swap itself

        # Sentinel: survives HTMX DOM swap but is destroyed by a full page reload.
        # If the back navigation turns into a full reload, the second assertion
        # fires first with an explicit "full reload" message before the CSP check.
        page.evaluate("window.__historyRestoreSentinel = 'alive'")

        # Browser Back: HTMX intercepts the popstate event, reads the saved
        # dashboard body from localStorage, and does a body swap + settle step.
        # The settle step calls setAttribute('style', …) for each element that
        # has an inline style in either the stored snapshot or the current page —
        # this is the exact code path that triggers the CSP violations.
        page.go_back()
        page.wait_for_load_state("networkidle")

        # If go_back() triggered a full page reload the sentinel is gone; in that
        # case the test is silently vacuous (no HTMX restore = no violations).
        # Fail explicitly so the test doesn't give a false green.
        assert page.evaluate("() => window.__historyRestoreSentinel === 'alive'"), (
            "browser Back triggered a full page reload instead of HTMX history "
            "restore — the test did not exercise the code path under test"
        )

        assert not errors, (
            "CSP style-src-attr violations after browser Back from /flights/new:\n"
            + "\n".join(msg.text for msg in errors)
        )

    def test_no_csp_errors_on_browser_forward_to_dashboard(
        self, logged_in_page, live_server_url
    ):
        """Navigate /aircraft/ → dashboard → Back → Forward; no CSP violations.

        The 20ms restore task (Oe(t, s)) reverts each element to its pre-settle
        clone s = t.cloneNode() taken before the settle step ran. If the stored
        dashboard snapshot contains Leaflet inline styles, the restore task calls
        setAttribute('style', …) for each — the violation source unique to the
        Forward direction (the Back direction triggers violations during the
        earlier settle step instead).
        """
        page = logged_in_page
        errors: list = []
        page.on(
            "console", lambda msg: errors.append(msg) if msg.type == "error" else None
        )

        # Start on /aircraft/ — no Leaflet, no inline styles to save.
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")
        errors.clear()

        # hx-boost swap: /aircraft/ → dashboard.  Leaflet initialises and adds
        # inline styles; htmx:beforeHistorySave strips them when saving /aircraft/.
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")
        errors.clear()

        # Back: htmx:beforeHistorySave strips the dashboard's Leaflet styles
        # before writing the snapshot, then HTMX restores /aircraft/.
        page.go_back()
        page.wait_for_load_state("networkidle")
        errors.clear()

        page.evaluate("window.__forwardSentinel = 'alive'")

        # Forward: HTMX restores the stored dashboard body.  The 20ms restore
        # task reverts elements to their pre-settle clone — without the fix that
        # clone carries Leaflet styles and setAttribute('style', …) fires here.
        page.go_forward()
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__forwardSentinel === 'alive'"), (
            "browser Forward triggered a full page reload instead of HTMX history "
            "restore — the test did not exercise the code path under test"
        )
        assert not errors, (
            "CSP style-src-attr violations after browser Forward to dashboard:\n"
            + "\n".join(msg.text for msg in errors)
        )


class TestModalCleanupOnNavigation:
    """Bootstrap modal artefacts must not survive an hx-boost body swap.

    When a Bootstrap modal is open the library adds .modal-open to <body>,
    sets body style="overflow:hidden;padding-right:…", and appends a
    .modal-backdrop div. An hx-boost link click replaces the body's children
    but leaves the <body> element itself intact, so those artefacts would
    persist into the next page — the grey overlay stays visible, scrolling is
    locked, and body[style] triggers a style-src-attr CSP violation during the
    settle step. The htmx:beforeSwap handler in ui.js strips all three before
    the swap; the test below verifies it.
    """

    def test_modal_backdrop_removed_before_htmx_swap(
        self, logged_in_page, live_server_url
    ):
        """Bootstrap modal state injected before a link click must be cleared.

        Simulates the exact DOM state Bootstrap produces when a modal is open,
        fires an hx-boost link click, then asserts all three artefacts
        (.modal-backdrop, body.modal-open, body[style]) are gone after the swap.
        """
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        # Reproduce the DOM state Bootstrap creates when opening a modal.
        page.evaluate(
            "() => {"
            "  var bd = document.createElement('div');"
            "  bd.className = 'modal-backdrop fade show';"
            "  document.body.appendChild(bd);"
            "  document.body.classList.add('modal-open');"
            "  document.body.style.overflow = 'hidden';"
            "  document.body.style.paddingRight = '15px';"
            "}"
        )
        assert page.evaluate(
            "() => document.querySelector('.modal-backdrop') !== null"
        ), "Test setup failed: .modal-backdrop was not injected"

        # Trigger navigation via JS .click() directly on the DOM element.
        # Playwright's locator.click() moves to screen coordinates, which land
        # on the full-screen backdrop overlay and never reach the anchor.
        # element.click() in JS dispatches the event directly on the element,
        # bypassing hit-testing, so HTMX's bubbling click handler intercepts it
        # and fires the hx-boost swap (and therefore htmx:beforeSwap).
        page.evaluate("() => document.querySelector('a.navbar-brand').click()")
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate(
            "() => document.querySelector('.modal-backdrop') === null"
        ), ".modal-backdrop survived the hx-boost swap — htmx:beforeSwap cleanup failed"
        assert page.evaluate("() => !document.body.classList.contains('modal-open')"), (
            "body.modal-open survived the hx-boost swap"
        )
        assert page.evaluate("() => !document.body.hasAttribute('style')"), (
            "body style= survived the hx-boost swap — would trigger a CSP violation"
        )


class TestCsrfAfterBodySwap:
    """CSRF tokens must remain valid and form-injectable after hx-boost navigation.

    base.html sets up CSRF in two ways:

    1. <meta name="csrf-token"> in <head> — carries the session token value.
       HTMX only swaps <body>, so this survives body swaps automatically.

    2. An inline IIFE captures the token from the meta tag once at page load
       and registers a document-level 'submit' listener that injects a hidden
       csrf_token input into every POST form on submit.

    The failure mode: if hx-boost somehow stopped swapping only the body (e.g.
    a config change causes it to also replace <head>), or if the meta tag were
    absent from the server-rendered head, the token captured by the IIFE would
    be stale or None, and every POST form would return HTTP 400 silently.

    These tests verify both invariants after a body swap, and prove that the
    form submit listener injects the token into forms that arrived in the new body.
    """

    def test_csrf_meta_tag_present_after_htmx_navigation(
        self, logged_in_page, live_server_url
    ):
        """The CSRF meta tag in <head> must survive an hx-boost body swap.

        The tag is not in <body>, so HTMX should never touch it during a swap.
        This test guards against configuration changes that accidentally cause
        HTMX to replace <head> as well, or against the tag being omitted from
        a server response.

        The sentinel guard ensures the navigation was a body swap — if a full
        reload happened, the meta tag would be re-rendered server-side anyway
        and the CSRF check would be vacuously true."""
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "Sentinel destroyed — navigation was a full page reload, not a body "
            "swap. The CSRF meta tag check below would be vacuously true."
        )

        token = page.evaluate(
            "() => document.querySelector('meta[name=\"csrf-token\"]')?.content"
        )
        assert token and len(token) > 10, (
            "CSRF meta tag missing or empty after hx-boost body swap — "
            "the form submit IIFE will read None or '' and all POST forms "
            "on this page will return HTTP 400"
        )

    def test_csrf_token_injected_into_form_after_htmx_navigation(
        self, logged_in_page, live_server_url
    ):
        """The CSRF form-injection listener must wire up forms arriving in swapped bodies.

        The IIFE in base.html captures the token and registers a document-level
        'submit' listener. Since the listener is on document (not destroyed by
        the body swap), it must still fire after a swap and inject a hidden
        csrf_token input into forms that were NOT present at the initial page
        load (i.e. forms that arrived in the swapped body).

        Path: /aircraft/ → /aircraft/new via hx-boost. The add-aircraft form
        arrives in the new body. Simulating a submit event and checking the
        hidden input was injected proves the listener is still wired and the
        closure token is non-empty."""
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        page.locator("a[href='/aircraft/new']").first.click()
        page.wait_for_url("**/aircraft/new**", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "Sentinel destroyed — navigation to /aircraft/new was a full reload, "
            "not a body swap. The form arrived via DOMContentLoaded, not HTMX swap."
        )

        # Simulate the submit event on the add-aircraft form.
        # The document-level listener (from the IIFE) must inject a hidden
        # csrf_token input. We check the value is non-empty — empty would mean
        # the meta tag was absent when the IIFE ran.
        injected_token = page.evaluate(
            "() => {"
            "  var form = document.querySelector('form[method=\"post\"]')"
            "           || document.querySelector('form');"
            "  if (!form) return null;"
            "  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));"
            "  return form.querySelector('input[name=\"csrf_token\"]')?.value || null;"
            "}"
        )
        assert injected_token is not None, (
            "No POST form found on /aircraft/new after hx-boost navigation — "
            "cannot verify CSRF injection"
        )
        assert len(injected_token) > 10, (
            f"CSRF hidden input was injected but its value is {injected_token!r} — "
            "the IIFE captured an empty token, likely because the meta tag was "
            "absent at initial page load. POST forms on swapped pages will return "
            "HTTP 400."
        )


class TestScrollPositionAfterBodySwap:
    """Scroll position must reset to the top after an hx-boost body swap.

    HTMX 2.x calls window.scrollTo(0, 0) during body swaps by default.
    This behaviour can be accidentally suppressed by an htmx:afterSwap
    handler or an HTMX config change. If it breaks, users land mid-page
    on every hx-boost navigation — the old scroll offset survives into
    the new page, and the symptom is invisible in tests that don't check
    window.scrollY.
    """

    def test_scroll_resets_to_top_after_htmx_navigation(
        self, logged_in_page, live_server_url
    ):
        """After an hx-boost body swap, window.scrollY must be 0.

        Scrolls down on /aircraft/ before navigating, then asserts the
        position is 0 after the brand-link hx-boost swap to /.
        """
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        # Extend the page height artificially so we can scroll regardless of
        # how many aircraft are seeded — we are testing scroll-reset behaviour,
        # not page content.
        page.evaluate("() => { document.body.style.paddingBottom = '2000px'; }")
        page.evaluate("window.scrollTo(0, 500)")
        scroll_before = page.evaluate("() => window.scrollY")
        assert scroll_before > 0, (
            "Could not scroll down even after padding — unexpected browser behaviour"
        )

        # hx-boost body swap: brand link navigates from /aircraft/ to /
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        # The app sets htmx.config.scrollBehavior = "smooth" so the scroll
        # from 500 → 0 is animated. networkidle resolves before the animation
        # finishes, so we must wait for window.scrollY to actually reach 0
        # rather than asserting immediately after networkidle.
        try:
            page.wait_for_function("() => window.scrollY === 0", timeout=3000)
        except Exception as exc:
            scroll_y = page.evaluate("() => window.scrollY")
            raise AssertionError(
                f"scrollY={scroll_y} after hx-boost navigation — HTMX did not "
                "reset scroll position to top. Check whether an htmx:afterSwap "
                "handler or an htmx.config change suppressed the scroll reset."
            ) from exc


class TestDataOhInitedClearedOnHistoryRestore:
    """JS widgets on history-restored pages must re-initialize via htmx:afterSettle.

    The contract:
      1. htmx:beforeHistorySave (in ui.js) strips data-oh-inited from all
         elements in the page before HTMX serialises it to localStorage.
      2. htmx:historyRestore (in ui.js) dispatches a synthetic htmx:afterSettle
         so that init() re-runs on the restored DOM, re-attaching event listeners.

    If step 1 breaks (data-oh-inited survives the snapshot), the restored page's
    init() guard sees the attribute and skips re-initialization — event listeners
    are never re-attached, widgets are dead.

    If step 2 breaks (synthetic htmx:afterSettle is never dispatched), init()
    never runs at all on the restored page.

    The test navigates /aircraft/new → dashboard → Back to /aircraft/new.
    The HTMX history-restore path is confirmed by a sentinel that must survive
    the Back navigation (destroyed on full reload, alive on history restore).
    aircraft_form.js is confirmed re-initialized by verifying the fuel-type hint
    responds to a select change — the same signal used in
    test_widget_reinitializes_via_aftersettle for the forward-navigation path.
    """

    def test_js_reinit_after_browser_back_to_form_page(
        self, logged_in_page, live_server_url
    ):
        """JS widgets on a history-restored page must respond to user interaction.

        Failure mode A (data-oh-inited not stripped): init() guard sees the old
        attribute on the restored DOM, skips re-initialization, and the fuel-type
        hint select never fires.

        Failure mode B (synthetic htmx:afterSettle not dispatched): init() is
        never called at all on the history-restored page.
        """
        page = logged_in_page

        # Full page load on /aircraft/new — aircraft_form.js runs init() via
        # DOMContentLoaded and sets data-oh-inited on the select element.
        page.goto(f"{live_server_url}/aircraft/new")
        page.wait_for_load_state("networkidle")

        # hx-boost swap away: HTMX serialises /aircraft/new to localStorage
        # via htmx:beforeHistorySave; ui.js must strip data-oh-inited here.
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        # Sentinel set on dashboard — survives historyRestore (window unchanged)
        # but is destroyed by a full page reload (new JS context).
        page.evaluate("window.__backSentinel = 'alive'")

        # Browser Back: HTMX intercepts popstate → htmx:historyRestore fires →
        # ui.js dispatches synthetic htmx:afterSettle → aircraft_form.js init()
        # re-runs on the restored DOM.
        page.go_back()
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__backSentinel === 'alive'"), (
            "browser Back triggered a full page reload instead of HTMX history "
            "restore — the test did not exercise the data-oh-inited code path"
        )

        # aircraft_form.js must have re-initialized: the fuel-type hint must
        # respond to a select change. Same wait_for_function pattern as
        # test_widget_reinitializes_via_aftersettle (htmx:afterSettle fires
        # 20 ms after settle; asserting immediately after networkidle is a race).
        page.locator("#fuel_type").select_option("mogas")
        try:
            page.wait_for_function(
                "() => (document.getElementById('fuel_type_hint')?.textContent || '')"
                ".includes('0.74')",
                timeout=3000,
            )
        except Exception as exc:
            raise AssertionError(
                "Fuel hint did not update after browser Back to /aircraft/new — "
                "aircraft_form.js did not re-initialize via htmx:afterSettle. "
                "Either data-oh-inited was not stripped in htmx:beforeHistorySave "
                "or htmx:historyRestore did not dispatch the synthetic afterSettle."
            ) from exc


class TestHxBoostFalseLinks:
    """Links carrying hx-boost="false" must trigger full page reloads, not body swaps.

    auth.logout and set_language perform session side-effects that require a full
    reload: logout must clear the session cookie server-side and cause a new JS
    context (so auth state is actually reset), and set_language must send a
    redirect that the browser follows as a real navigation (so the locale change
    takes effect in the session and the page re-renders in the new language).

    If hx-boost intercepts either of these links, the redirect response is
    swapped as body HTML and discarded — no cookie is cleared, no locale is
    updated, the bug is completely invisible, and there is no console error.

    The sentinel technique used here is the INVERSE of the body-swap tests: the
    variable must be DESTROYED after the click (new JS context = full reload)
    rather than surviving (window retained = body swap).
    """

    def test_logout_link_triggers_full_page_reload(
        self, fresh_logged_in_page, live_server_url
    ):
        """Clicking the logout link must destroy the JS context.

        A body-swap logout would leave the session cookie intact — the page
        visually navigates but auth state never changes. The sentinel is
        destroyed only if the browser creates a fresh JS context (full reload).

        Uses fresh_logged_in_page (isolated browser context) so that logging
        out during this test does not destroy the shared session auth state
        and cause subsequent tests to fail their fixture setup.
        """
        page = fresh_logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__sentinel = 'alive'")

        page.locator("a[href='/logout']").click()
        page.wait_for_load_state("networkidle")

        sentinel = page.evaluate("() => window.__sentinel")
        assert sentinel is None, (
            "Sentinel survived the logout click — the logout <a> is being "
            "intercepted by hx-boost as a body swap instead of triggering a "
            'full page reload. Add or restore hx-boost="false" in base.html.'
        )

    def test_set_language_link_triggers_full_page_reload(
        self, fresh_viewer_page, live_server_url
    ):
        """Clicking a language-switch link must destroy the JS context.

        set_language mutates the session locale and issues a redirect. If
        hx-boost intercepts the click, the redirect response is body-swapped
        and the locale is never written to the session — the language silently
        stays unchanged. The sentinel must be destroyed to prove a full reload.

        The language links live inside a collapsed Bootstrap dropdown; they are
        present in the DOM but not visible. element.click() in JS dispatches
        the click event directly on the element (bypassing visibility/coordinate
        hit-testing), so hx-boost's body-level event delegation still sees it —
        the same technique used in TestModalCleanupOnNavigation.

        Uses fresh_viewer_page (viewer account, no TOTP) rather than
        fresh_logged_in_page (admin, TOTP) to avoid TOTP code-reuse rejection
        when both tests run in the same 30-second TOTP window.
        """
        page = fresh_viewer_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        link_count = page.locator("a[href^='/set-language/']").count()
        if link_count == 0:
            pytest.skip("No set_language links found on the dashboard")

        page.evaluate("window.__sentinel = 'alive'")

        # Dispatch click via JS to bypass collapsed-dropdown visibility.
        # The event still bubbles to <body>, so hx-boost would intercept it
        # if hx-boost="false" were absent.
        # Wrap in expect_navigation so Playwright waits for the full page load
        # (set-language redirects back to /, replacing the JS context) before
        # we attempt to evaluate against the new context.
        with page.expect_navigation(wait_until="networkidle", timeout=10000):
            page.evaluate(
                "() => document.querySelector(\"a[href^='/set-language/']\").click()"
            )

        sentinel = page.evaluate("() => window.__sentinel")
        assert sentinel is None, (
            "Sentinel survived the language-switch click — the set_language <a> "
            "is being intercepted by hx-boost as a body swap. Add or restore "
            'hx-boost="false" on language links in base.html.'
        )


class TestHtmxConsoleErrors:
    """HTMX body-swap navigation must not produce browser console errors.

    The htmx:beforeSwap handler in ui.js strips inline styles from all ID'd
    elements before HTMX's settle step runs. Without it, HTMX calls
    setAttribute('style', …) to copy old inline styles to new elements, which
    violates style-src-attr 'none' CSP and is caught here as a console error.
    """

    def test_no_console_errors_during_htmx_navigation(
        self, logged_in_page, live_server_url
    ):
        """Two consecutive hx-boost body swaps must not log any console errors.

        Navigates dashboard → aircraft list → dashboard. Both swaps exercise
        the settle step on elements that are shared across all pages (PWA badges
        from base.html). The test asserts that no CSP violations or other errors
        appear — errors during the initial full page load are excluded."""
        page = logged_in_page
        errors: list = []
        page.on(
            "console", lambda msg: errors.append(msg) if msg.type == "error" else None
        )

        # Full page load — establishes baseline; clear any start-up errors.
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")
        errors.clear()

        # First hx-boost swap: dashboard → aircraft list
        page.locator("a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=10000)
        page.wait_for_load_state("networkidle")

        # Second hx-boost swap: aircraft list → dashboard
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert not errors, (
            "Console errors found during HTMX body-swap navigation:\n"
            + "\n".join(msg.text for msg in errors)
        )
