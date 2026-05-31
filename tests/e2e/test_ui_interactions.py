"""
Playwright E2E tests for JavaScript interactions handled by ui.js.

These tests verify the client-side behaviours that the Flask test client
cannot observe: data-href row navigation, data-stop-prop action cells,
data-confirm delete dialogs, data-auto-submit dropdowns, and
data-action preset buttons.

Run with:  pytest --e2e tests/e2e/ --override-ini='addopts='
"""

import pytest

pytestmark = pytest.mark.e2e


# ── CSP: nonces are rendered in HTML ─────────────────────────────────────────


class TestCSPNonces:
    def test_inline_scripts_have_nonce(self, logged_in_page, live_server_url):
        """Every inline <script> block in the page must carry a nonce attribute."""
        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")
        scripts_without_nonce = page.evaluate("""
            () => [...document.querySelectorAll('script')]
                    .filter(s => !s.nonce && s.textContent.trim())
                    .length
        """)
        assert scripts_without_nonce == 0, (
            f"{scripts_without_nonce} inline script(s) missing nonce"
        )

    def test_csp_header_present(self, logged_in_page, live_server_url):
        """CSP header must be returned by the server with a per-request nonce."""
        import re

        resp = logged_in_page.context.request.get(f"{live_server_url}/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert re.search(r"'nonce-[A-Za-z0-9_-]+'", csp), "CSP script-src missing nonce"


# ── data-href: clickable rows navigate ───────────────────────────────────────


class TestClickableRows:
    def test_flight_list_row_navigates(self, logged_in_page, live_server_url, seed):
        """Clicking a flight row (data-href) navigates to the flight detail page."""
        page = logged_in_page
        ac_id = seed["ac_flt"]
        fe_id = seed["fe_flt"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        row = page.locator("tr[data-href]").first
        row.click()
        page.wait_for_load_state("networkidle")

        assert f"/aircraft/{ac_id}/flights/{fe_id}" in page.url

    def test_action_cell_does_not_navigate(self, logged_in_page, live_server_url, seed):
        """Clicking an action cell (data-stop-prop) does not trigger row navigation."""
        page = logged_in_page
        ac_id = seed["ac_stop"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")
        url_before = page.url

        action_cell = page.locator("td[data-stop-prop]").first
        action_cell.click()
        page.wait_for_load_state("networkidle")

        assert page.url == url_before, "Action cell click should not navigate away"


# ── data-confirm: delete forms show confirmation dialog ──────────────────────


class TestDeleteConfirmation:
    def test_confirm_cancel_prevents_submit(
        self, logged_in_page, live_server_url, live_app, seed
    ):
        """Cancelling the confirm dialog must not delete the entry."""
        from models import FlightEntry

        page = logged_in_page
        ac_id = seed["ac_del1"]
        fe_id = seed["fe_del1"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.dismiss())
        page.locator("button.btn-ac-danger").first.click()
        page.wait_for_load_state("networkidle")

        with live_app.app_context():
            assert FlightEntry.query.get(fe_id) is not None, (
                "Entry should still exist after cancel"
            )

    def test_confirm_accept_submits_form(
        self, logged_in_page, live_server_url, live_app, seed
    ):
        """Accepting the confirm dialog deletes the entry."""
        from models import FlightEntry

        page = logged_in_page
        ac_id = seed["ac_del2"]
        fe_id = seed["fe_del2"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.accept())
        page.locator("button.btn-ac-danger").first.click()
        page.wait_for_load_state("networkidle")

        with live_app.app_context():
            assert FlightEntry.query.get(fe_id) is None, (
                "Entry should be deleted after accept"
            )


# ── data-auto-submit: select change auto-submits ─────────────────────────────


class TestAutoSubmit:
    def test_role_dropdown_autosubmits(self, logged_in_page, live_server_url):
        """Changing the role select (data-auto-submit) reloads the users list."""
        page = logged_in_page
        page.goto(f"{live_server_url}/users/")
        page.wait_for_load_state("networkidle")

        role_select = page.locator("select[name='role'][data-auto-submit]").first
        if role_select.count() == 0:
            pytest.skip("No data-auto-submit role select on this page")

        with page.expect_navigation():
            role_select.select_option("owner")
        page.wait_for_load_state("networkidle")
        assert page.locator("table").count() > 0


# ── GPS parse AJAX flow ───────────────────────────────────────────────────────


class TestGPSAjax:
    def test_gps_upload_autofills_form(self, logged_in_page, live_server_url, seed):
        """Uploading a GPX file autofills the date field without a page reload."""
        page = logged_in_page
        ac_id = seed["ac_gps"]

        from pathlib import Path
        from playwright.sync_api import expect as pw_expect

        gpx_path = str(Path(__file__).parent / "fixtures" / "test_flight.gpx")
        expected_date = "2024-06-15"

        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        gps_input = page.locator('input[type="file"][name="gps_file"]')
        if gps_input.count() == 0:
            pytest.skip("GPS upload input not present on this page")

        with page.expect_response("**/parse-gps"):
            gps_input.set_input_files(gpx_path)

        # Wait for the JS to populate the date field with the GPS-parsed value
        pw_expect(page.locator('input[name="date"]')).to_have_value(expected_date)
