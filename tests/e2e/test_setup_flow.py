"""
E2E tests for the empty-DB welcome and first-time setup flow.

These tests use a function-scoped fresh_server fixture (defined in conftest.py)
that spins up an isolated Flask server with no seed data so they can exercise
paths that only exist before any user account has been created:
  - /          → shows the public landing page
  - /login     → redirects to /setup
  - /setup     → wizard: account → TOTP skip → operating model → done

Each test function gets its own server so DB state never leaks between tests.
Fixtures live in conftest.py to avoid asyncio / pytest-xdist worker conflicts.
"""

import pytest

pytestmark = pytest.mark.e2e

# ── Tests ──────────────────────────────────────────────────────────────────────


class TestSetupFlow:
    def test_landing_page_shown_for_empty_db(self, setup_page):
        """/ shows the public landing page (not the logged-in home) when DB has no users."""
        page, url = setup_page
        page.goto(url + "/")
        page.wait_for_load_state("networkidle")

        # landing.html has a hero section — absent from all logged-in pages
        assert page.locator("section.hero").count() > 0
        assert "OpenHangar" in page.title()

    def test_login_redirects_to_setup_when_empty_db(self, setup_page):
        """/login redirects to /setup when no user account exists yet."""
        page, url = setup_page
        page.goto(url + "/login")
        page.wait_for_load_state("networkidle")

        assert "/setup" in page.url

    def test_setup_wizard_sole_pilot_no_console_errors(self, setup_page):
        """Complete the sole-pilot setup path and verify no console errors or CSS loss."""
        page, url = setup_page

        console_messages: list = []

        def _capture(msg):
            # "Service Worker registration blocked by Playwright" is an infrastructure
            # message from the test harness itself (service_workers="block"), not an
            # application error. Exclude it from the assertion.
            if msg.type in ("error", "warning") and "Service Worker" not in msg.text:
                console_messages.append(msg)

        page.on("console", _capture)

        # ── Landing page ──────────────────────────────────────────────────────
        page.goto(url + "/")
        page.wait_for_load_state("networkidle")
        assert page.locator("section.hero").count() > 0

        # ── Step 1: Account ───────────────────────────────────────────────────
        page.goto(url + "/setup")
        page.wait_for_load_state("networkidle")
        assert "/setup" in page.url

        page.fill('input[name="email"]', "admin@setup-test.local")
        page.fill('input[name="password"]', "setup-test-password-1")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "step=totp" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # ── Step 2: TOTP — skip ───────────────────────────────────────────────
        page.click('button[name="action"][value="skip"]')
        page.wait_for_url(lambda u: "step=operating_model" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # ── Step 3: Operating model — sole pilot (shortest path) ──────────────
        # Radio inputs are visually-hidden; click the card label to select.
        # sole_pilot skips aircraft_count and goes straight to _setup_finish()
        page.locator('label.wizard-choice-card:has(input[value="sole_pilot"])').click()
        page.click('button[type="submit"]')
        # ?_swr_fresh=1 marks the post-setup redirect so the SW bypasses its
        # cache for this one request (see sw.js); pwa.js scrubs it from the
        # visible URL client-side on htmx:pushedIntoHistory, which wait_for_url
        # can't observe directly (history.replaceState isn't a navigation) —
        # so first tolerate either form, then poll for the scrub specifically:
        # it's JS-event-driven, not tied to network activity, so it can lag
        # behind networkidle under load (e.g. parallel test workers).
        page.wait_for_url(
            lambda u: u.split("?")[0].rstrip("/") == url.rstrip("/"), timeout=10000
        )
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => !window.location.search.includes('_swr_fresh')", timeout=5000
        )

        # ── Post-setup checks ─────────────────────────────────────────────────
        # Landed on the home page with a success flash
        assert page.url.rstrip("/") == url.rstrip("/")
        page_html = page.content()
        assert "Setup complete" in page_html or "Welcome" in page_html

        # Navigating to /login now should redirect to / (user is logged in)
        page.goto(url + "/login")
        page.wait_for_load_state("networkidle")
        assert "/login" not in page.url

        assert not console_messages, (
            "Console errors/warnings during setup flow:\n"
            + "\n".join(f"[{m.type}] {m.text}" for m in console_messages)
        )

    def test_setup_wizard_sole_operator_path(self, setup_page):
        """Complete the sole-operator path (includes aircraft_count step)."""
        page, url = setup_page

        console_messages: list = []

        def _capture(msg):
            if msg.type in ("error", "warning") and "Service Worker" not in msg.text:
                console_messages.append(msg)

        page.on("console", _capture)

        # Account step
        page.goto(url + "/setup")
        page.wait_for_load_state("networkidle")
        page.fill('input[name="email"]', "admin@setup-test.local")
        page.fill('input[name="password"]', "setup-test-password-2")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "step=totp" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # TOTP skip
        page.click('button[name="action"][value="skip"]')
        page.wait_for_url(lambda u: "step=operating_model" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # Operating model: sole_operator → aircraft_count step
        page.locator(
            'label.wizard-choice-card:has(input[value="sole_operator"])'
        ).click()
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "step=aircraft_count" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # Aircraft count
        page.fill('input[name="aircraft_count"]', "1")
        page.click('button[type="submit"]')
        # See the _swr_fresh comment in test_setup_wizard_sole_pilot_no_console_errors.
        page.wait_for_url(
            lambda u: u.split("?")[0].rstrip("/") == url.rstrip("/"), timeout=10000
        )
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => !window.location.search.includes('_swr_fresh')", timeout=5000
        )

        assert page.url.rstrip("/") == url.rstrip("/")
        page_html = page.content()
        assert "Setup complete" in page_html or "Welcome" in page_html

        assert not console_messages, (
            "Console errors/warnings during sole-operator setup flow:\n"
            + "\n".join(f"[{m.type}] {m.text}" for m in console_messages)
        )
