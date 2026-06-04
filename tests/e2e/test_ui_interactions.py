"""
Playwright E2E tests for JavaScript interactions handled by ui.js and
flight_form inline scripts.

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
        from models import FlightEntry, db

        page = logged_in_page
        ac_id = seed["ac_del1"]
        fe_id = seed["fe_del1"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.dismiss())
        page.locator("button.btn-ac-danger").first.click()
        page.wait_for_load_state("networkidle")

        with live_app.app_context():
            assert db.session.get(FlightEntry, fe_id) is not None, (
                "Entry should still exist after cancel"
            )

    def test_confirm_accept_submits_form(
        self, logged_in_page, live_server_url, live_app, seed
    ):
        """Accepting the confirm dialog deletes the entry."""
        from models import FlightEntry, db

        page = logged_in_page
        ac_id = seed["ac_del2"]
        fe_id = seed["fe_del2"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.accept())
        page.locator("button.btn-ac-danger").first.click()
        page.wait_for_load_state("networkidle")

        with live_app.app_context():
            assert db.session.get(FlightEntry, fe_id) is None, (
                "Entry should be deleted after accept"
            )


# ── data-auto-submit: select change auto-submits ─────────────────────────────


class TestAutoSubmit:
    def test_role_dropdown_autosubmits(self, logged_in_page, live_server_url, seed):
        """Changing the role select (data-auto-submit) submits the form and
        reloads the users list. The pilot seed user provides the second row
        whose role select is rendered (the admin's own row shows text only)."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/config/users/")
        page.wait_for_load_state("networkidle")

        role_select = page.locator("select[name='role'][data-auto-submit]").first
        pw_expect(role_select).to_be_visible()

        with page.expect_navigation():
            role_select.select_option("viewer")
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


# ── GPS parse: form-state preservation ───────────────────────────────────────


class TestGPSFormStatePreservation:
    """GPS AJAX parse must only overwrite the mapped fields (date, route, times).
    Fields not in the field map — crew name and notes — must survive unchanged."""

    def test_crew_and_notes_survive_gps_parse(
        self, logged_in_page, live_server_url, seed
    ):
        from pathlib import Path
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_gps"]
        gpx_path = str(Path(__file__).parent / "fixtures" / "test_flight.gpx")

        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        gps_input = page.locator('input[type="file"][name="gps_file"]')
        if gps_input.count() == 0:
            pytest.skip("GPS upload input not present on this page")

        # Pre-fill crew and notes before the GPS upload
        page.fill('input[name="crew_name_0"]', "Preserved Pilot")
        page.fill('textarea[name="notes"]', "Notes that must survive")

        with page.expect_response("**/parse-gps"):
            gps_input.set_input_files(gpx_path)

        # Gate on the date being filled so JS has finished running
        pw_expect(page.locator('input[name="date"]')).to_have_value("2024-06-15")

        # Crew and notes are not in the GPS field map — they must not be touched
        assert page.locator('input[name="crew_name_0"]').input_value() == "Preserved Pilot"
        assert page.locator('textarea[name="notes"]').input_value() == "Notes that must survive"


# ── "Other aircraft" dropdown ─────────────────────────────────────────────────


class TestOtherAircraftDropdown:
    """Selecting 'Aircraft not in this instance' must show a warning banner;
    re-selecting a managed aircraft must hide it again."""

    def test_warning_shows_and_hides(self, logged_in_page, live_server_url, seed):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/flights/new")
        page.wait_for_load_state("networkidle")

        ac_select = page.locator('select[name="aircraft_id"]')
        if ac_select.count() == 0:
            pytest.skip("Aircraft select not present — page may require an aircraft_id param")

        warning = page.locator("#other-aircraft-warning")

        # Initially hidden (new, unsubmitted form)
        pw_expect(warning).to_be_hidden()

        # Select "other" → warning must appear
        ac_select.select_option("other")
        pw_expect(warning).to_be_visible()

        # Select a real managed aircraft → warning must disappear
        ac_select.select_option(str(seed["ac_flt"]))
        pw_expect(warning).to_be_hidden()


# ── Duplicate-flight banner ───────────────────────────────────────────────────


class TestDuplicateBanner:
    """Submitting a flight whose date/route matches an existing entry must
    cause the server to re-render the form with the duplicate warning banner."""

    def test_duplicate_banner_shown_on_matching_submission(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_dup"]

        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        # Fill in same date/route as the pre-seeded fe_dup entry
        page.fill('input[name="date"]', "2024-05-10")
        page.fill('input[name="departure_icao"]', "EBOS")
        page.fill('input[name="arrival_icao"]', "EBBR")
        page.fill('input[name="crew_name_0"]', "T. Pilot")

        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Server must re-render the form with the duplicate warning
        dup_banner = page.locator(".alert-warning").filter(has_text="Possible duplicate")
        pw_expect(dup_banner).to_be_visible()


# ── Logbook section toggle ────────────────────────────────────────────────────


class TestLogbookToggle:
    """Selecting 'Not logging in my pilot logbook' hides the pilot log section;
    switching back to PIC or dual shows it again."""

    def test_pilot_log_section_toggles_on_role_change(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_flt"]

        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        pilot_log = page.locator("#pilot-log-section")

        # PIC is the default — pilot log section must be visible
        pw_expect(pilot_log).to_be_visible()

        # Switch to "not logging in my pilot logbook"
        page.click("#pilot_role_none")
        pw_expect(pilot_log).to_be_hidden()

        # Switch back to dual
        page.click("#pilot_role_dual")
        pw_expect(pilot_log).to_be_visible()

        # Switch to none again then back to PIC
        page.click("#pilot_role_none")
        pw_expect(pilot_log).to_be_hidden()
        page.click("#pilot_role_pic")
        pw_expect(pilot_log).to_be_visible()


# ── TOTP auto-submit ──────────────────────────────────────────────────────────


class TestTOTPAutoSubmit:
    """Entering the 6th TOTP digit must trigger automatic form submission
    without the user pressing Enter or clicking the button."""

    def test_form_submits_on_sixth_digit(
        self, unauthenticated_page, live_server_url, seed
    ):
        import pyotp
        from playwright.sync_api import expect as pw_expect

        page = unauthenticated_page

        # Step 1: credentials
        page.goto(f"{live_server_url}/login")
        page.wait_for_load_state("networkidle")
        page.fill('input[name="email"]', "totp@e2e.test")
        page.fill('input[name="password"]', "TotpPass1!")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Should now be on the TOTP step
        pw_expect(page.locator("#totp_code")).to_be_visible()

        # Step 2: generate a valid TOTP code and fill the field
        code = pyotp.TOTP(seed["totp_secret"]).now()
        page.fill("#totp_code", code)

        # Auto-submit must navigate away from the login page
        page.wait_for_url(lambda url: "/login" not in url, timeout=5000)
        assert "/login" not in page.url


# ── ICAO type autocomplete ────────────────────────────────────────────────────


class TestICAOTypeAutocomplete:
    """Selecting a variant from the ICAO type autocomplete must pre-fill
    the Manufacturer and Model fields on the new-aircraft form."""

    def test_selecting_variant_fills_make_and_model(
        self, logged_in_page, live_server_url
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/new")
        page.wait_for_load_state("networkidle")

        icao_input = page.locator("#icao_type_input")
        pw_expect(icao_input).to_be_visible()

        # Type enough to trigger the autocomplete (debounce 200 ms)
        icao_input.fill("C172")
        page.wait_for_selector(".aircraft-type-ac-list", timeout=3000)

        # Select the first dropdown item
        page.locator(".aircraft-type-ac-item").first.click()

        # Both fields must now be non-empty
        make_val = page.locator("#make").input_value()
        model_val = page.locator("#model").input_value()
        assert make_val.strip(), "Manufacturer field should be filled after ICAO selection"
        assert model_val.strip(), "Model field should be filled after ICAO selection"


# ── Language switcher ─────────────────────────────────────────────────────────


class TestLanguageSwitcher:
    """Clicking a flag in the language dropdown must re-render the page in
    the selected locale; the html[lang] attribute must reflect the change."""

    def test_language_switches_and_reverts(self, logged_in_page, live_server_url):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")

        # Switch to French
        page.locator(".lang-flag-btn").click()
        page.locator(".lang-dropdown-menu a[href*='/fr']").click()
        page.wait_for_load_state("networkidle")
        pw_expect(page.locator("html")).to_have_attribute("lang", "fr")

        # Switch back to English so subsequent tests see English UI
        page.locator(".lang-flag-btn").click()
        page.locator(".lang-dropdown-menu a[href*='/en']").click()
        page.wait_for_load_state("networkidle")
        pw_expect(page.locator("html")).to_have_attribute("lang", "en")


# ── Airport autocomplete ──────────────────────────────────────────────────────


class TestAirportAutocomplete:
    """Typing an ICAO code in a departure/arrival field must show a dropdown
    and selecting an entry must set the field value and populate the name hint."""

    def test_airport_dropdown_fills_field_and_hint(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_gps"]
        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        dep_input = page.locator('input[name="departure_icao"]')
        dep_input.fill("EBBR")
        page.wait_for_selector(".airport-ac-list", timeout=3000)

        # Select the first suggestion
        page.locator(".airport-ac-item").first.click()

        # Field value must be a non-empty ICAO code
        field_val = dep_input.input_value()
        assert field_val.strip(), "Departure field should be filled after airport selection"

        # Hint (airport name) must be non-empty
        hint = page.locator('input[name="departure_icao"] ~ .airport-ac-hint').first
        assert hint.inner_text().strip(), "Airport name hint should appear after selection"


# ── Aircraft form: flight counter offset toggle ───────────────────────────────


class TestFlightCounterOffsetToggle:
    """Unchecking 'Has flight time counter' must reveal the offset field;
    re-checking it must hide the field again."""

    def test_offset_field_toggles_with_checkbox(self, logged_in_page, live_server_url):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/new")
        page.wait_for_load_state("networkidle")

        offset_row = page.locator("#flight_counter_offset_row")
        checkbox = page.locator("#has_flight_counter")

        # Default: checkbox checked → offset row hidden
        pw_expect(offset_row).to_be_hidden()

        # Uncheck → offset row appears
        checkbox.uncheck()
        pw_expect(offset_row).to_be_visible()

        # Re-check → offset row hides again
        checkbox.check()
        pw_expect(offset_row).to_be_hidden()


# ── Aircraft form: fuel density hint ─────────────────────────────────────────


class TestFuelDensityHint:
    """Changing the fuel type select must update the density hint text to
    reflect the data-density value of the selected option."""

    def test_hint_updates_on_fuel_type_change(self, logged_in_page, live_server_url):
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/new")
        page.wait_for_load_state("networkidle")

        hint = page.locator("#fuel_type_hint")
        fuel_select = page.locator("#fuel_type")

        fuel_select.select_option("mogas")
        assert "0.74" in hint.inner_text()

        fuel_select.select_option("jet_a1")
        assert "0.81" in hint.inner_text()

        fuel_select.select_option("avgas")
        assert "0.72" in hint.inner_text()


# ── Flight form: fuel-added fields toggle ─────────────────────────────────────


class TestFuelFieldsToggle:
    """Selecting a 'Fuel added' refuel option must show the quantity fields;
    selecting 'No fuel added' must hide them."""

    def test_fuel_fields_show_and_hide(self, logged_in_page, live_server_url, seed):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_gps"]
        page.goto(f"{live_server_url}/flights/new?aircraft_id={ac_id}")
        page.wait_for_load_state("networkidle")

        fuel_fields = page.locator("#fuel-added-fields")

        # Default: "No fuel added" selected → fields hidden
        pw_expect(fuel_fields).to_be_hidden()

        # Select "before" → fields appear
        page.locator("#fuel_event_before").click()
        pw_expect(fuel_fields).to_be_visible()

        # Select "after" → fields stay visible
        page.locator("#fuel_event_after").click()
        pw_expect(fuel_fields).to_be_visible()

        # Back to "none" → fields hide
        page.locator("#fuel_event_none").click()
        pw_expect(fuel_fields).to_be_hidden()


# ── Other-aircraft: registration lookup ───────────────────────────────────────


class TestRegistrationLookup:
    """Typing a previously-logged registration in the other-aircraft field
    must trigger an AJAX lookup that auto-fills the aircraft type field."""

    def test_registration_lookup_fills_type_field(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/flights/new")
        page.wait_for_load_state("networkidle")

        # Switch to other-aircraft mode
        page.locator('select[name="aircraft_id"]').select_option("other")
        pw_expect(page.locator("#other-aircraft-fields")).to_be_visible()

        # Type the seeded registration (debounce 300 ms)
        page.locator("#other_ac_reg").fill("E2E-LOOKUP")

        # Wait for the AJAX response to populate the type field
        type_input = page.locator("#other_ac_make_model")
        pw_expect(type_input).not_to_have_value("", timeout=3000)
        assert type_input.input_value().strip(), (
            "Aircraft type should be filled after registration lookup"
        )
