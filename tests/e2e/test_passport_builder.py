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
        page.get_by_role("link", name="Delete passport", exact=True).click()
        page.get_by_role("button", name="Delete passport", exact=True).click()
        page.wait_for_url(live_server_url + "/passports/")
    finally:
        context.close()
