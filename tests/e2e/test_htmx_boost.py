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
