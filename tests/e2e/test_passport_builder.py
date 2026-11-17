"""Real-browser builder, map reinitialisation and mobile layout."""

import re
from collections.abc import Iterator
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
    page.locator("#discovery-slider").press("Home")
    expect(page.locator("#discovery-limit")).to_have_value("1")
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
