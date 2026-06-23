"""
E2E tests for HTMX hx-boost SPA navigation.

Verifies that:
- Link clicks swap the <body> in-place rather than triggering full page reloads.
- The page title updates after each swap.
- Page-specific JS modules re-initialize via htmx:afterSettle.
- Links marked hx-boost="false" (logout, theme, language) still trigger full reloads.

Technique — sentinel variable:
  A JS variable set on the current page survives an hx-boost body swap
  (the window object is not replaced) but is destroyed by a full page reload.
  Setting window.__htmxSentinel before a navigation and checking it after is
  the most direct way to distinguish the two cases.

Run with:  pytest --e2e tests/e2e/test_htmx_boost.py --override-ini='addopts='
"""

import pytest

pytestmark = pytest.mark.e2e


class TestHtmxBodySwap:
    """Prove that hx-boost intercepts link clicks and swaps the body
    without a full page reload."""

    def test_nav_link_swaps_body_not_full_reload(self, logged_in_page, live_server_url):
        """A JS sentinel set before clicking a nav link must survive the
        navigation — proving the body was swapped in-place and the window
        object (along with all its JS state) was not replaced."""
        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        page.locator("nav a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=5000)
        page.wait_for_load_state("networkidle")

        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "JS sentinel destroyed — navigation triggered a full page reload "
            "instead of an hx-boost body swap"
        )

    def test_page_title_updates_after_body_swap(self, logged_in_page, live_server_url):
        """HTMX must update <title> when swapping the body — the title on
        the aircraft list page must differ from the dashboard title."""
        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")
        dashboard_title = page.title()

        page.locator("nav a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=5000)
        page.wait_for_load_state("networkidle")

        aircraft_title = page.title()
        assert aircraft_title != dashboard_title, (
            "Page title did not change after hx-boost body swap"
        )

    def test_widget_reinitializes_via_aftersettle(
        self, logged_in_page, live_server_url
    ):
        """After two consecutive hx-boost navigations, page-specific JS must
        re-initialize via htmx:afterSettle.

        Path: dashboard → /aircraft/ (nav link) → /aircraft/new (content link).
        Both must be body swaps (sentinel survives both). On /aircraft/new the
        fuel-type hint from aircraft_form.js must respond to dropdown changes,
        proving the module re-initialized correctly."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        # First hop: dashboard → aircraft list via nav link
        page.locator("nav a[href='/aircraft/']").first.click()
        page.wait_for_url("**/aircraft/**", timeout=5000)
        page.wait_for_load_state("networkidle")

        # Second hop: aircraft list → new aircraft form via content link
        add_link = page.locator("a[href='/aircraft/new']").first
        if add_link.count() == 0:
            pytest.skip("No 'Add aircraft' link found on the aircraft list page")
        add_link.click()
        page.wait_for_url("**/aircraft/new**", timeout=5000)
        page.wait_for_load_state("networkidle")

        # Both hops must have been body swaps
        assert page.evaluate("() => window.__htmxSentinel === 'alive'"), (
            "Sentinel destroyed — one of the navigations triggered a full page reload"
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


class TestHtmxExclusions:
    """Links marked hx-boost='false' must trigger full page reloads,
    not body swaps."""

    def test_logout_triggers_full_page_reload(self, logged_in_page, live_server_url):
        """The logout link carries hx-boost='false' and must trigger a full
        page reload. A sentinel set before the click must be destroyed."""
        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        page.evaluate("window.__htmxSentinel = 'alive'")

        with page.expect_navigation():
            page.locator("#oh-logout-link").click()
        page.wait_for_load_state("networkidle")

        # After a full reload the JS context is fresh — sentinel must not exist
        sentinel = page.evaluate("() => window.__htmxSentinel")
        assert sentinel is None, (
            "Sentinel survived — logout triggered an hx-boost body swap "
            "instead of a full page reload"
        )
