"""
E2E tests for the empty-DB welcome and first-time setup flow.

These tests spin up their own isolated Flask server (no seed data) so they can
exercise paths that only exist before any user account has been created:
  - /          → shows the public landing page
  - /login     → redirects to /setup
  - /setup     → wizard: account → TOTP skip → operating model → done

Each test function gets a fresh server and browser context so the DB state from
one test never leaks into the next.
"""

import os
import shutil
import socket
import tempfile
import threading
import time

import pytest

pytestmark = pytest.mark.e2e

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="function")
def fresh_server():
    """Isolated Flask server with an empty SQLite database (no seed)."""
    from sqlalchemy.pool import NullPool

    from init import create_app  # type: ignore[import]
    from models import db  # type: ignore[import]

    upload_dir = tempfile.mkdtemp()
    db_file = os.path.join(upload_dir, "fresh_e2e.db")

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file}",
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"check_same_thread": False},
            "poolclass": NullPool,
        },
        UPLOAD_FOLDER=upload_dir,
        SERVER_NAME=None,
    )

    with app.app_context():
        db.create_all()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    time.sleep(0.8)

    yield f"http://127.0.0.1:{port}"

    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture(scope="function")
def setup_page(fresh_server):
    """A fresh Playwright page pointed at the empty-DB server."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            base_url=fresh_server,
            ignore_https_errors=False,
            service_workers="block",
        )
        ctx.set_default_timeout(10000)
        pg = ctx.new_page()
        yield pg, fresh_server
        pg.close()
        ctx.close()
        browser.close()


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
        page.wait_for_url(url + "/", timeout=10000)
        page.wait_for_load_state("networkidle")

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
        page.locator('label.wizard-choice-card:has(input[value="sole_operator"])').click()
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "step=aircraft_count" in u, timeout=10000)
        page.wait_for_load_state("networkidle")

        # Aircraft count
        page.fill('input[name="aircraft_count"]', "1")
        page.click('button[type="submit"]')
        page.wait_for_url(url + "/", timeout=10000)
        page.wait_for_load_state("networkidle")

        assert page.url.rstrip("/") == url.rstrip("/")
        page_html = page.content()
        assert "Setup complete" in page_html or "Welcome" in page_html

        assert not console_messages, (
            "Console errors/warnings during sole-operator setup flow:\n"
            + "\n".join(f"[{m.type}] {m.text}" for m in console_messages)
        )
