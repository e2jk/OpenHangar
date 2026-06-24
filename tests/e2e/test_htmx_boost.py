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

        # aircraft_form.js must have re-initialized via htmx:afterSettle
        fuel_select = page.locator("#fuel_type")
        hint = page.locator("#fuel_type_hint")
        pw_expect(fuel_select).to_be_visible()
        fuel_select.select_option("mogas")
        assert "0.74" in hint.inner_text(), (
            "Fuel type hint did not update — aircraft_form.js did not "
            "re-initialize via htmx:afterSettle after hx-boost navigation"
        )


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
