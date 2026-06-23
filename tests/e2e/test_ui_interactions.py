"""
Playwright E2E tests for JavaScript interactions handled by ui.js and
flight_form inline scripts.

These tests verify the client-side behaviours that the Flask test client
cannot observe: data-href row navigation, data-stop-prop action cells,
data-confirm delete dialogs, data-auto-submit dropdowns, and
data-action preset buttons.

Run with:  pytest --e2e tests/e2e/ --override-ini='addopts='
"""

import os

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
            () => [...document.querySelectorAll('script:not([type="application/json"])')]
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
        """Clicking inside an action cell (data-stop-prop) must not trigger the
        row's data-href navigation.  The row's href points to the flight detail
        page (/aircraft/<id>/flights/<fe_id>); we verify that URL is not reached
        even if clicking inside the cell happens to activate another element."""
        page = logged_in_page
        ac_id = seed["ac_stop"]
        fe_id = seed["fe_del2"]  # the top row on ac_stop's flight list (future date)

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        # The detail URL that data-href would navigate to
        detail_url = f"/aircraft/{ac_id}/flights/{fe_id}"

        # Click the very top-left corner of the action cell (avoids any buttons)
        action_cell = page.locator("td[data-stop-prop]").first
        action_cell.click(position={"x": 2, "y": 2})
        page.wait_for_timeout(300)

        assert detail_url not in page.url, (
            "Clicking the action cell must not trigger the row data-href navigation"
        )


# ── data-confirm: delete forms show confirmation dialog ──────────────────────


class TestDeleteConfirmation:
    def test_confirm_cancel_prevents_submit(
        self, logged_in_page, live_server_url, live_app, seed
    ):
        """Cancelling the confirm dialog must not delete the entry."""
        page = logged_in_page
        ac_id = seed["ac_del1"]
        fe_id = seed["fe_del1"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.dismiss())
        page.locator(
            f'form[action*="/{fe_id}/delete"] button.btn-ac-danger'
        ).click()
        page.wait_for_load_state("networkidle")

        # Verify via HTTP so the check uses a fresh Flask request (no stale session).
        resp = page.request.get(
            f"{live_server_url}/aircraft/{ac_id}/flights/{fe_id}"
        )
        assert resp.status == 200, "cancelled delete should leave flight accessible"

    @pytest.mark.destructive
    def test_confirm_accept_submits_form(
        self, logged_in_page, live_server_url, live_app, seed
    ):
        """Accepting the confirm dialog deletes the entry."""
        page = logged_in_page
        ac_id = seed["ac_del2"]
        fe_id = seed["fe_del2"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/flights")
        page.wait_for_load_state("networkidle")

        page.once("dialog", lambda d: d.accept())
        with page.expect_request(
            lambda r: f"/{fe_id}/delete" in r.url and r.method == "POST",
            timeout=10000,
        ):
            page.locator(
                f'form[action*="/{fe_id}/delete"] button.btn-ac-danger'
            ).click()
        page.wait_for_load_state("networkidle")

        # Verify via HTTP so the check uses a fresh Flask request (no stale session).
        resp = page.request.get(
            f"{live_server_url}/aircraft/{ac_id}/flights/{fe_id}"
        )
        assert resp.status == 404, "deleted flight should return 404"


# ── data-auto-submit: select change auto-submits ─────────────────────────────


class TestAutoSubmit:
    @pytest.mark.destructive
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
        assert (
            page.locator('input[name="crew_name_0"]').input_value() == "Preserved Pilot"
        )
        assert (
            page.locator('textarea[name="notes"]').input_value()
            == "Notes that must survive"
        )


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
            pytest.skip(
                "Aircraft select not present — page may require an aircraft_id param"
            )

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

        # Fill in the same date/route as the first seeded jodel entry (from SEED)
        page.fill('input[name="date"]', seed["dup_date"])
        page.fill('input[name="departure_icao"]', seed["dup_dep"])
        page.fill('input[name="arrival_icao"]', seed["dup_arr"])
        page.fill('input[name="crew_name_0"]', "T. Pilot")

        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Server must re-render the form with the duplicate warning
        dup_banner = page.locator(".alert-warning").filter(
            has_text="Possible duplicate"
        )
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

        # Step 1: credentials — dev-seed admin has TOTP enabled
        page.goto(f"{live_server_url}/login")
        page.wait_for_load_state("networkidle")
        page.fill('input[name="email"]', "admin@openhangar.dev")
        page.fill('input[name="password"]', "openhangar-dev-1")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Should now be on the TOTP step
        pw_expect(page.locator("#totp_code")).to_be_visible()

        # Step 2: generate a valid TOTP code and type it character-by-character.
        # page.fill() fires one bulk input event; the auto-submit JS counts digits
        # on each keystroke, so press_sequentially() is required for reliable
        # triggering in headless CI environments.
        # Use the next 30-second window's code: valid_window=1 on the server
        # accepts it, and it is guaranteed unused by the logged_in_page fixture
        # which submits the current window's code.
        import time

        code = pyotp.TOTP(seed["totp_secret"]).at(time.time() + 30)
        page.locator("#totp_code").press_sequentially(code)

        # Auto-submit must navigate away from the login page without any explicit
        # submit-button click — that is the behaviour this test is asserting.
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
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
        assert make_val.strip(), (
            "Manufacturer field should be filled after ICAO selection"
        )
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


# ── Theme switcher ────────────────────────────────────────────────────────────


class TestThemeSwitcher:
    """The navbar theme toggle must update html[data-bs-theme] immediately and
    persist the preference across page navigation."""

    def test_set_theme_dark_applies_html_attribute(
        self, logged_in_page, live_server_url
    ):
        """Navigating to /set-theme/dark must set data-bs-theme='dark' on <html>."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/set-theme/dark?next=/")
        page.wait_for_load_state("networkidle")
        pw_expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")

        # Restore to system so subsequent tests start in a known state
        page.goto(f"{live_server_url}/set-theme/system?next=/")
        page.wait_for_load_state("networkidle")

    def test_toggle_button_cycles_dark_to_light(self, logged_in_page, live_server_url):
        """Clicking the toggle button from dark mode must switch to light mode."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        # Start from known state: dark
        page.goto(f"{live_server_url}/set-theme/dark?next=/")
        page.wait_for_load_state("networkidle")
        pw_expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")

        # Cycle: dark → light
        page.locator(".theme-toggle-btn").click()
        page.wait_for_load_state("networkidle")
        pw_expect(page.locator("html")).to_have_attribute("data-bs-theme", "light")

        # Restore
        page.goto(f"{live_server_url}/set-theme/system?next=/")
        page.wait_for_load_state("networkidle")

    def test_dark_theme_persists_across_navigation(
        self, logged_in_page, live_server_url
    ):
        """After setting dark mode, loading a different page must still render dark."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        page.goto(f"{live_server_url}/set-theme/dark?next=/")
        page.wait_for_load_state("networkidle")

        # Navigate to a completely different page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")

        # The inline <head> script reads the DB-persisted preference and sets
        # data-bs-theme before CSS loads — theme must survive a full navigation.
        pw_expect(page.locator("html")).to_have_attribute("data-bs-theme", "dark")

        # Restore
        page.goto(f"{live_server_url}/set-theme/system?next=/")
        page.wait_for_load_state("networkidle")


# ── Airport autocomplete ──────────────────────────────────────────────────────


class TestAirportAutocomplete:
    """Typing an ICAO code in a departure/arrival field must show a dropdown
    and selecting an entry must set the field value and populate the name hint."""

    def test_airport_dropdown_fills_field_and_hint(
        self, logged_in_page, live_server_url, seed
    ):

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
        assert field_val.strip(), (
            "Departure field should be filled after airport selection"
        )

        # Hint (airport name) must be non-empty
        hint = page.locator('input[name="departure_icao"] ~ .airport-ac-hint').first
        assert hint.inner_text().strip(), (
            "Airport name hint should appear after selection"
        )


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

        # OO-PNH is in the admin's pilot logbook (seeded by dev_seed / _seed_helpers)
        page.locator("#other_ac_reg").fill("OO-PNH")

        # Wait for the AJAX response to populate the type field
        type_input = page.locator("#other_ac_make_model")
        pw_expect(type_input).not_to_have_value("", timeout=3000)
        assert type_input.input_value().strip(), (
            "Aircraft type should be filled after registration lookup"
        )


# ── Aircraft photo lightbox ───────────────────────────────────────────────────


class TestPhotoModal:
    """Clicking a photo thumbnail must open the lightbox modal with the correct
    image and caption; closing it must hide the modal."""

    def test_photo_click_opens_modal_with_correct_image(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_flt"]
        page.goto(f"{live_server_url}/aircraft/{ac_id}")
        page.wait_for_load_state("networkidle")

        thumb = page.locator(".photo-thumb-img").first
        if thumb.count() == 0:
            pytest.skip("no photos on this aircraft")

        expected_src = thumb.get_attribute("data-img-src")
        expected_alt = thumb.get_attribute("data-img-alt")

        thumb.click()

        modal = page.locator("#photoViewModal")
        pw_expect(modal).to_be_visible()

        modal_img = page.locator("#photoViewImg")
        pw_expect(modal_img).to_have_attribute("src", expected_src)
        assert page.locator("#photoViewCaption").inner_text() == expected_alt

        page.locator("#photoViewModal .btn-close").click()
        pw_expect(modal).to_be_hidden()

    def test_photo_upload_button_enabled_after_file_selection(
        self, logged_in_page, live_server_url, seed
    ):
        """The photo upload submit button must be disabled until files are chosen."""
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_flt"]
        page.goto(f"{live_server_url}/aircraft/{ac_id}")
        page.wait_for_load_state("networkidle")

        submit_btn = page.locator("#photo-upload-submit")
        file_input = page.locator('input[name="photos"]')
        if submit_btn.count() == 0:
            pytest.skip("photo upload not available (not an owner)")

        pw_expect(submit_btn).to_be_disabled()

        from pathlib import Path

        seed_jpg = str(
            Path(__file__).parent.parent.parent
            / "app"
            / "dev_seed_docs"
            / "oo-pnh-cockpit.jpg"
        )
        file_input.set_input_files(seed_jpg)
        pw_expect(submit_btn).to_be_enabled()


# ── Airworthiness status filter ───────────────────────────────────────────────


class TestAirworthinessStatusFilter:
    """Clicking a status filter button hides all rows whose data-status does not
    match; clicking 'All' shows every row again.  Uses OO-GRN seed data which
    has documents with multiple distinct statuses (complied, pending_review, …)."""

    def test_filter_hides_non_matching_rows_and_all_restores(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        # ac_del1 is OO-GRN (robin) — the aircraft seeded with airworthiness data
        ac_id = seed["ac_del1"]

        page.goto(f"{live_server_url}/aircraft/{ac_id}/airworthiness/")
        page.wait_for_load_state("networkidle")

        # Filter buttons must be present
        filter_container = page.locator("#doc-filters")
        pw_expect(filter_container).to_be_visible()

        all_rows = page.locator("#docs-table tbody tr")
        total = all_rows.count()
        assert total > 0, "Expected at least one document row for OO-GRN"

        # Pick the "Complied" filter button (data-filter="complied").
        # The seed always has several complied ADs on OO-GRN.
        complied_btn = page.locator("#doc-filters [data-filter='complied']")
        pw_expect(complied_btn).to_be_visible()

        complied_btn.click()
        page.wait_for_timeout(100)  # JS runs synchronously but give paint a moment

        # The clicked button must be active (CSS selector avoids full-class-string match)
        pw_expect(
            page.locator("#doc-filters [data-filter='complied'].active")
        ).to_be_visible()

        # Every visible row must carry data-status="complied"
        for tr in all_rows.all():
            status = tr.get_attribute("data-status")
            display = tr.evaluate("el => window.getComputedStyle(el).display")
            if status == "complied":
                assert display != "none", "Complied row must be visible"
            else:
                assert display == "none", (
                    f"Row with status '{status}' must be hidden when 'complied' filter is active"
                )

        # Click 'All' — every row must be visible again
        all_btn = page.locator("#doc-filters [data-filter='']")
        all_btn.click()
        page.wait_for_timeout(100)

        pw_expect(page.locator("#doc-filters [data-filter=''].active")).to_be_visible()
        for tr in all_rows.all():
            display = tr.evaluate("el => window.getComputedStyle(el).display")
            assert display != "none", "All rows must be visible after 'All' filter"


# ── Document viewer modal ─────────────────────────────────────────────────────


class TestDocumentModal:
    """Clicking 'View' on a PDF or image document must open the inline document
    viewer modal with the correct title and content; closing it must clear the body."""

    def test_document_view_opens_modal_with_content(
        self, logged_in_page, live_server_url, seed
    ):
        from playwright.sync_api import expect as pw_expect

        page = logged_in_page
        ac_id = seed["ac_flt"]
        page.goto(f"{live_server_url}/aircraft/{ac_id}")
        page.wait_for_load_state("networkidle")

        view_btn = page.locator("[data-bs-target='#docModal']").first
        if view_btn.count() == 0:
            pytest.skip("no viewable documents on this aircraft")

        expected_title = view_btn.get_attribute("data-title")
        view_btn.click()

        modal = page.locator("#docModal")
        pw_expect(modal).to_be_visible()

        title_el = page.locator("#docModalLabel")
        assert title_el.inner_text().strip() == expected_title

        body = page.locator("#docModalBody")
        pw_expect(body.locator("img, iframe").first).to_be_visible()

        page.locator("#docModal .btn-close").click()
        pw_expect(modal).to_be_hidden()
        # hidden.bs.modal fires after the CSS transition; the JS handler that clears
        # the body runs asynchronously.  page.wait_for_function(string) uses eval()
        # which the app's strict CSP blocks — use a locator assertion instead.
        pw_expect(body.locator("img, iframe")).to_have_count(0, timeout=3000)


# ── GPS track map: Animate expands map and scrolls panel to top ──────────────

_requires_docker_gps = pytest.mark.skipif(
    not os.environ.get("E2E_BASE_URL"),
    reason="GPS track seed data only exists on the Docker dev server; set E2E_BASE_URL to run",
)


@_requires_docker_gps
class TestAnimateMapExpand:
    """Clicking the Animate button on a GPS tracks map must:
    1. Expand #tracks-map to fill the viewport height (JS sets style.height).
    2. Scroll the containing panel so its top edge is at the top of the viewport.

    Tested on all four pages that embed the _flight_tracks_map.html partial:
    - Dashboard GPS Tracks panel (compact, 320 px initial)
    - Aircraft detail GPS Tracks panel (compact, 320 px initial)
    - Pilot dedicated tracks page (full, 520 px initial)
    - Aircraft dedicated tracks page (full, 520 px initial)
    """

    def _animate_and_check(self, page, url: str) -> None:
        page.goto(url)
        page.wait_for_load_state("networkidle")

        play_btn = page.locator("#anim-play")
        if play_btn.count() == 0:
            pytest.skip(f"No GPS track map (Animate button) on {url}")

        tracks_map = page.locator("#tracks-map")
        initial_h = tracks_map.evaluate("el => el.getBoundingClientRect().height")
        viewport_h = page.evaluate("() => window.innerHeight")

        play_btn.click()
        # The height assignment is synchronous; poll until the smooth-scroll
        # settles (up to 3 s covers even very long pages).
        page.wait_for_timeout(300)  # let JS run and initial scroll begin
        for _ in range(14):
            panel_top = tracks_map.evaluate("""el => {
                var p = el.closest('.dash-panel')
                     || el.closest('.ac-form-card')
                     || el.parentElement;
                return p.getBoundingClientRect().top;
            }""")
            if panel_top <= 80:
                break
            page.wait_for_timeout(200)

        expanded_h = tracks_map.evaluate("el => el.getBoundingClientRect().height")

        assert expanded_h > initial_h, (
            f"Map did not expand after Animate: "
            f"initial={initial_h:.0f}px, after={expanded_h:.0f}px on {url}"
        )
        assert expanded_h >= viewport_h * 0.7, (
            f"Expanded map should fill ≥ 70% of viewport height: "
            f"map={expanded_h:.0f}px, viewport={viewport_h:.0f}px on {url}"
        )

        # The JS scrolls the nearest .dash-panel or .ac-form-card ancestor to the
        # top of the viewport.  Allow a generous margin for smooth-scroll overshoot
        # and for pages with a fixed header that offsets the panel slightly.
        assert -10 <= panel_top <= 80, (
            f"Panel top should be near viewport top after scroll, "
            f"got {panel_top:.0f}px on {url}"
        )

    def test_dashboard(self, logged_in_page, live_server_url):
        """Compact GPS Tracks panel on the main dashboard."""
        self._animate_and_check(logged_in_page, f"{live_server_url}/")

    def test_aircraft_detail(self, logged_in_page, live_server_url, seed):
        """Compact GPS Tracks panel on the aircraft detail page."""
        ac_id = seed["ac_flt"]
        self._animate_and_check(logged_in_page, f"{live_server_url}/aircraft/{ac_id}")

    def test_pilot_tracks_page(self, logged_in_page, live_server_url):
        """Full-size map on the dedicated pilot tracks page."""
        self._animate_and_check(logged_in_page, f"{live_server_url}/pilot/tracks")

    def test_aircraft_tracks_page(self, logged_in_page, live_server_url, seed):
        """Full-size map on the dedicated aircraft tracks page."""
        ac_id = seed["ac_flt"]
        self._animate_and_check(
            logged_in_page, f"{live_server_url}/aircraft/{ac_id}/tracks"
        )
