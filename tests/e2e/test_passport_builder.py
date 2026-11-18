"""Real-browser builder, map reinitialisation and mobile layout."""

import re
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, expect

from tests.e2e.conftest import _block_external_network

pytestmark = [pytest.mark.e2e, pytest.mark.destructive]


@pytest.fixture
def passport_page(
    browser_context: BrowserContext, live_server_url: str
) -> Iterator[Page]:
    browser = browser_context.browser
    assert browser is not None
    context = browser.new_context(
        base_url=live_server_url,
        service_workers="block",
        ignore_https_errors=live_server_url.startswith("https://"),
    )
    context.set_default_timeout(10000)
    _block_external_network(context)
    page = context.new_page()
    page.goto(live_server_url + "/login")
    page.locator('input[name="email"]').fill("pilot@openhangar.dev")
    page.locator('input[name="password"]').fill("openhangar-dev-3")
    page.locator('button[type="submit"]').click()
    page.wait_for_url(lambda url: "/login" not in url)
    try:
        yield page
    finally:
        context.close()


def test_passport_builder_map_survives_htmx_navigation(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
    title = "Browser passport " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/")
    page.get_by_role("link", name="Create passport", exact=True).click()
    expect(page.locator("#passport-builder")).to_have_attribute("data-oh-inited", "1")
    expect(page.locator("#passport-map-data")).to_have_count(1)
    expect(page.locator("#passport-selection-map .leaflet-map-pane")).to_have_count(1)
    page.get_by_role("button", name="Review passport", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Enter a passport name.")
    expect(page.locator("#passport-selection-map .leaflet-map-pane")).to_have_count(1)
    page.locator("#passport-title").fill(title)
    page.locator("#passport-codes").fill("EBNM; EBOS")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/passports/new")
        )
    ) as added:
        page.get_by_role("button", name="Add codes", exact=True).click()
    assert added.value.status == 200
    expect(page.locator("#passport-builder table")).to_contain_text("EBOS")
    expect(page.locator("#passport-builder")).to_have_attribute("data-oh-inited", "1")
    assert errors == [], errors
    expect(
        page.locator("#passport-selection-map .leaflet-overlay-pane path")
    ).to_have_count(2)
    page.get_by_role("button", name="Review passport", exact=True).click()
    expect(page.get_by_role("button", name="Save passport", exact=True)).to_be_visible()
    expect(page.locator("#passport-selection-map .leaflet-map-pane")).to_have_count(1)
    page.get_by_role("button", name="Back to selection", exact=True).click()
    expect(
        page.locator("#passport-selection-map .leaflet-overlay-pane path")
    ).to_have_count(2)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Edit passport", exact=True).click()
    expect(page.locator("#passport-selection-map .leaflet-map-pane")).to_have_count(1)
    expect(
        page.locator("#passport-selection-map .leaflet-overlay-pane path")
    ).to_have_count(2)
    page.get_by_role("link", name="Cancel", exact=True).click()
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    page.get_by_role("link", name="Create passport", exact=True).click()
    expect(page.locator("#passport-builder")).to_have_attribute("data-oh-inited", "1")
    page.locator('input[name="draft"]').evaluate("el => { el.value = 'expired'; }")
    page.get_by_role("button", name="Review passport", exact=True).click()
    expect(page.get_by_role("link", name="Start again", exact=True)).to_be_visible()
    page.get_by_role("link", name="Start again", exact=True).click()
    expect(page.locator("#passport-selection-map .leaflet-map-pane")).to_have_count(1)
    assert errors == []


def test_passport_builder_without_javascript(
    passport_page: Page, live_server_url: str
) -> None:
    browser = passport_page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(passport_page.context.cookies())
    page = context.new_page()
    title = "No-JS passport " + uuid4().hex[:8]
    try:
        page.goto(live_server_url + "/passports/new")
        page.locator("#passport-title").fill(title)
        page.locator("#passport-codes").fill("EBNM")
        page.get_by_role("button", name="Add codes", exact=True).click()
        expect(page.locator("#passport-builder table")).to_contain_text("EBNM")
        page.get_by_role("button", name="Review passport", exact=True).click()
        page.get_by_role("button", name="Save passport", exact=True).click()
        page.wait_for_url(re.compile(r"/passports/\d+/$"))
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
        page.locator("table tbody a").first.click()
        page.locator("#visit-date").fill("2025-01-01")
        page.get_by_role("button", name="Save visit", exact=True).click()
        expect(
            page.get_by_role("button", name="Delete manual visit", exact=True)
        ).to_be_visible()
        page.get_by_role("link", name=title, exact=True).click()
        expect(page.locator("table tbody")).to_contain_text("Visited")
        page.get_by_role("link", name="Delete passport", exact=True).click()
        page.get_by_role("button", name="Delete passport", exact=True).click()
        page.wait_for_url(live_server_url + "/passports/")
    finally:
        context.close()


def test_passport_visits_frequency_and_htmx_map(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Visit passport " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-codes").fill("EBNM")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    page.locator("table tbody a").first.click()
    page.locator("#visit-date").fill("2025-01-01")
    page.locator("#visit-note").fill("Browser visit")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(page.get_by_text("Browser visit", exact=True)).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(
        page.locator("#passport-progress-map .leaflet-overlay-pane path")
    ).to_have_count(1)
    page.locator("#passport-frequency").check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#passport-progress")).to_have_attribute("data-frequency", "1")
    page.locator("#passport-progress-map .leaflet-overlay-pane path").click(force=True)
    expect(page.locator(".leaflet-popup-content a")).to_be_visible()
    page.locator(".leaflet-popup-content a").click()
    expect(page.get_by_text("Browser visit", exact=True)).to_be_visible()
    page.goto(passport_url)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_passport_destination_discovery(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Discovery " + uuid4().hex[:8]
    destination = "Discovery field " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.get_by_text("Add a missing airfield", exact=True).click()
    page.locator("#custom-name").fill(destination)
    page.locator("#custom-latitude").fill("50.4")
    page.locator("#custom-longitude").fill("4.7")
    page.get_by_role("button", name="Add custom airfield", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text(destination)
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.get_by_role("link", name="Where next?", exact=True).click()
    page.locator("#discovery-query").fill("EBNM")
    page.get_by_role("button", name="Search airfields", exact=True).click()
    option = page.locator("#discovery-origin option").filter(has_text="EBNM").first
    expect(option).to_have_count(1)
    value = option.get_attribute("value")
    assert value is not None
    page.locator("#discovery-origin").select_option(value)
    # The search reloaded the page (an htmx swap): the new airfield option
    # shows before passport_discovery.js re-attaches its slider/number sync
    # on htmx:afterSettle. Pressing Home in that gap moved only the slider.
    expect(page.locator("#passport-discovery")).to_have_attribute("data-oh-inited", "1")
    page.locator("#discovery-slider").press("Home")
    expect(page.locator("#discovery-limit")).to_have_value("1")
    page.locator("#discovery-limit").fill("100")
    expect(page.locator("#discovery-slider")).to_have_value("100")
    # The slider spans a practical range per mode (nm, then minutes), not the
    # server's 21,600-minute cap, so 100 nm isn't stuck at its far left.
    expect(page.locator("#discovery-slider")).to_have_attribute("max", "500")
    page.locator("#discovery-mode").select_option("time")
    expect(page.locator("#discovery-slider")).to_have_attribute("max", "300")
    page.locator("#discovery-slider").press("End")
    expect(page.locator("#discovery-limit")).to_have_value("300")
    page.locator("#discovery-mode").select_option("distance")
    page.locator("#discovery-limit").fill("100")
    expect(page.locator("#discovery-slider")).to_have_value("100")
    page.get_by_role("button", name="Find destinations", exact=True).click()
    expect(page.locator("table tbody")).to_contain_text(destination)
    expect(
        page.locator("#passport-discovery-map .leaflet-overlay-pane path")
    ).to_have_count(3)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(page.url)
        expect(plain.locator("table tbody")).to_contain_text(destination)
        plain.locator("#discovery-limit").fill("1")
        plain.get_by_role("button", name="Find destinations", exact=True).click()
        expect(
            plain.get_by_text(
                "No open, unvisited airfields match these inputs.", exact=True
            )
        ).to_be_visible()
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_passport_source_import_review_and_no_javascript(
    passport_page: Page,
    live_server_url: str,
    live_server: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if live_server[1] is None:
        pytest.skip("Source fixtures require the in-process browser server")
    from passports import imports
    from passports.source_fetch import Fetched

    def fetched(url: str, **kwargs: Any) -> Fetched:
        return Fetched(
            "https://source.example/list",
            "text/html",
            b"<p>EBNM ZZZZ</p><script>EVIL</script>",
        )

    monkeypatch.setattr(imports, "fetch", fetched)
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Imported " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#import-url").fill("https://source.example/list")
    page.get_by_role("button", name="Preview source import", exact=True).click()
    expect(
        page.get_by_role("heading", name="Review source import", exact=True)
    ).to_be_visible()
    expect(
        page.locator("#passport-selection-map .leaflet-overlay-pane path")
    ).to_have_count(1)
    expect(page.get_by_role("button", name="Save passport", exact=True)).to_have_count(
        0
    )
    page.get_by_role(
        "button", name="Add imported list to selection", exact=True
    ).click()
    expect(page.locator("#passport-codes")).to_have_value("ZZZZ")
    page.get_by_role("button", name="Drop these unresolved codes", exact=True).click()
    expect(page.locator("#passport-codes")).to_have_value("")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    expect(
        page.get_by_role("link", name="https://source.example/list", exact=True)
    ).to_be_visible()
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(passport_url + "edit")
        plain.get_by_role("button", name="Preview source import", exact=True).click()
        expect(
            plain.get_by_role("heading", name="Review source import", exact=True)
        ).to_be_visible()
        plain.get_by_role(
            "button", name="Replace selection with imported list", exact=True
        ).click()
        plain.get_by_role(
            "button", name="Drop these unresolved codes", exact=True
        ).click()
        plain.get_by_role("button", name="Review passport", exact=True).click()
        plain.get_by_role("button", name="Save passport", exact=True).click()
        plain.wait_for_url(passport_url)
    finally:
        context.close()
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_linked_editions_copy_combined_map_and_no_js(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Edition source " + uuid4().hex[:8]
    next_title = "Next " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-codes").fill("EBNM")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    source_url = page.url
    page.locator("table tbody a").first.click()
    page.locator("#visit-date").fill("2025-01-01")
    page.locator("#visit-note").fill("Private edition evidence")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(page.get_by_text("Private edition evidence", exact=True)).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Create next edition", exact=True).click()
    page.locator("#next-title").fill(next_title)
    page.locator("#next-start").fill("2027-05-01")
    page.locator("#next-end").fill("2027-08-31")
    page.get_by_role("button", name="Copy and review targets", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text("EBNM")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    new_url = page.url
    page.get_by_role("link", name="Linked editions", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/series/\d+/$"))
    series_url = page.url
    expect(page.locator("#edition-selector")).not_to_have_value("all")
    page.locator("#edition-selector").select_option("all")
    page.locator("#passport-frequency").check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    expect(
        page.locator("#passport-progress-map .leaflet-overlay-pane path")
    ).to_have_count(1)
    page.locator("#passport-progress-map .leaflet-overlay-pane path").click(force=True)
    expect(page.locator(".leaflet-popup-content")).to_contain_text(next_title)
    expect(page.locator(".leaflet-popup-content")).not_to_contain_text(
        "Private edition evidence"
    )
    page.locator("#edition-priority").select_option("oldest")
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(series_url)
        plain.locator("#edition-selector").select_option("all")
        plain.get_by_role("button", name="Apply", exact=True).click()
        expect(
            plain.get_by_role("heading", name="Compare editions", exact=True)
        ).to_be_visible()
        expect(plain.locator("table").first).to_contain_text("EBNM")
    finally:
        context.close()
    for url in (source_url, new_url):
        page.goto(url)
        page.get_by_role("link", name="Delete passport", exact=True).click()
        page.get_by_role("button", name="Delete passport", exact=True).click()
        page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_individual_vouchers_stay_separate_from_stamps_and_work_without_js(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Own voucher " + uuid4().hex[:8]
    destination = "Voucher field " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.get_by_text("Add a missing airfield", exact=True).click()
    page.locator("#custom-name").fill(destination)
    page.locator("#custom-latitude").fill("50.4")
    page.locator("#custom-longitude").fill("4.7")
    page.get_by_role("button", name="Add custom airfield", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text(destination)
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.get_by_role("link", name="Vouchers", exact=True).click()
    expect(page.get_by_role("heading", name="Vouchers", exact=True)).to_be_visible()
    page.wait_for_url(re.compile(r"/passports/\d+/vouchers$"))
    voucher_url = page.url
    page.locator("#voucher-default-quantity").fill("1")
    page.get_by_role("button", name="Set individual allowances", exact=True).click()
    expect(
        page.get_by_text("Each participant has one voucher per airfield.", exact=False)
    ).to_be_visible()
    page.locator("#voucher-date").fill("2025-01-01")
    page.locator("#voucher-saving").fill("12.00")
    page.locator("#voucher-currency").fill("EUR")
    page.locator("#voucher-note").fill("Own voucher use")
    page.get_by_role("button", name="Save voucher use", exact=True).click()
    expect(page.get_by_text("Own voucher use", exact=True)).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.locator("table tbody")).to_contain_text("Not visited")
    page.get_by_role("link", name="Vouchers", exact=True).click()
    expect(page.get_by_role("heading", name="Vouchers", exact=True)).to_be_visible()
    page.get_by_role("link", name="Correct voucher use", exact=True).click()
    expect(
        page.get_by_role("button", name="Save correction", exact=True)
    ).to_be_visible()
    page.locator("#voucher-saving").fill("15.50")
    page.locator("#voucher-note").fill("Corrected own voucher")
    page.get_by_role("button", name="Save correction", exact=True).click()
    expect(page.get_by_text("Corrected own voucher", exact=True)).to_be_visible()
    page.get_by_role("button", name="Undo voucher use", exact=True).click()
    expect(
        page.get_by_role("button", name="Undo voucher use", exact=True)
    ).to_have_count(0)
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.locator("table tbody a").first.click()
    page.locator("#visit-date").fill("2025-03-01")
    page.locator("#visit-note").fill("Visit without voucher use")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(page.get_by_text("Visit without voucher use", exact=True)).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Vouchers", exact=True).click()
    expect(page.get_by_role("heading", name="Vouchers", exact=True)).to_be_visible()
    expect(page.locator("table").first.locator("tbody tr td").nth(3)).to_have_text("1")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(voucher_url)
        plain.locator("#voucher-date").fill("2025-02-01")
        plain.locator("#voucher-note").fill("No-JS personal voucher")
        plain.get_by_role("button", name="Save voucher use", exact=True).click()
        expect(plain.get_by_text("No-JS personal voucher", exact=True)).to_be_visible()
        expect(plain.locator("table").first.locator("tbody tr td").nth(3)).to_have_text(
            "0"
        )
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_group_consent_series_map_mobile_and_without_js(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Group consent " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-start").fill("2001-01-01")
    page.locator("#passport-end").fill("2001-12-31")
    page.locator("#passport-codes").fill("EBNM EBOS")
    page.get_by_role("button", name="Add codes", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text("EBOS")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.locator("table tbody a").first.click()
    page.locator("#visit-date").fill("2001-01-01")
    page.locator("#visit-note").fill("Private group visit note")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(page.get_by_text("Private group visit note", exact=True)).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Group visit sharing", exact=True).click()
    page.locator("#group-name").fill("Browser group pilot")
    page.locator("#share-visits").check()
    page.locator("#share-history").check()
    page.get_by_role("button", name="Save sharing consent", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/series/\d+/$"))
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    markers = page.locator("#passport-progress-map .leaflet-overlay-pane path")
    expect(markers).to_have_count(2)
    assert sorted(
        markers.evaluate_all("els => els.map(el => el.getAttribute('stroke-width'))")
    ) == ["1", "4"]
    assert "Private group visit note" not in page.locator(
        "#passport-progress"
    ).get_attribute("data-fields")
    page.locator("#edition-selector").select_option("all")
    page.locator("#edition-history").check()
    page.locator("#passport-frequency").check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    expect(
        page.get_by_text(
            "History scope: participating editions and earlier read-only editions."
        )
    ).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(passport_url.rstrip("/") + "/group")
        plain.locator("#share-history").uncheck()
        plain.get_by_role("button", name="Save sharing consent", exact=True).click()
        expect(
            plain.get_by_role("heading", name="Compare editions", exact=True)
        ).to_be_visible()
        expect(plain.locator("table").first).to_contain_text("EBNM")
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors


def test_invitation_mobile_login_join_and_no_js(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    title = "Invited friends " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-codes").fill("EBNM EBOS")
    page.get_by_role("button", name="Add codes", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text("EBOS")
    page.get_by_role("button", name="Review passport", exact=True).click()
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.get_by_role("link", name="Group visit sharing", exact=True).click()
    page.locator("#group-name").fill("Group organiser")
    page.locator("#share-visits").check()
    page.get_by_role("button", name="Save sharing consent", exact=True).click()
    page.goto(passport_url)
    page.get_by_role("link", name="Invite friends", exact=True).click()
    page.get_by_role("button", name="Create a new invitation", exact=True).click()
    expect(page.locator("#passport-invitation")).to_have_attribute(
        "data-oh-inited", "1"
    )
    invitation_url = page.locator("#passport-invite-url").input_value()
    page.get_by_role("link", name="Back to passport", exact=True).click()
    page.get_by_role("link", name="Invite friends", exact=True).click()
    expect(page.locator("#passport-invitation")).to_have_attribute(
        "data-oh-inited", "1"
    )
    browser = page.context.browser
    assert browser is not None
    for javascript, email, password in [
        (True, "maintenance@openhangar.dev", "openhangar-dev-4"),
        (False, "renter@openhangar.dev", "openhangar-dev-5"),
    ]:
        context = browser.new_context(
            java_script_enabled=javascript,
            viewport={"width": 390, "height": 844},
            service_workers="block",
        )
        _block_external_network(context)
        try:
            friend = context.new_page()
            response = friend.goto(invitation_url)
            assert (
                response is not None
                and response.headers["referrer-policy"] == "no-referrer"
            )
            expect(
                friend.get_by_role("heading", name=title, exact=True)
            ).to_be_visible()
            friend.locator("#join-name").fill(
                "Visiting friend" if javascript else "No-JS friend"
            )
            friend.locator("#join-sharing").check()
            friend.get_by_role(
                "button", name="Sign in and join this passport", exact=True
            ).click()
            expect(
                friend.get_by_text("Sign in to join " + title, exact=False)
            ).to_be_visible()
            friend.locator('input[name="email"]').fill(email)
            friend.locator('input[name="password"]').fill(password)
            friend.locator('button[type="submit"]').click()
            friend.wait_for_url(re.compile(r"/passports/\d+/"))
            expect(
                friend.get_by_role("heading", name=title, exact=True)
            ).to_be_visible()
            if javascript:
                expect(
                    friend.locator("#passport-progress-map .leaflet-map-pane")
                ).to_have_count(1)
                assert friend.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth + 1"
                )
            friend.locator("table tbody a").first.click()
            friend.locator("#visit-date").fill("2026-01-01")
            friend.get_by_role("button", name="Save visit", exact=True).click()
            expect(
                friend.get_by_role("button", name="Delete manual visit", exact=True)
            ).to_be_visible()
        finally:
            context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
