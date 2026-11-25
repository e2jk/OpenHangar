"""Real-browser builder, map reinitialisation and mobile layout."""

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from tests.e2e.conftest import _block_external_network, click_and_settle

pytestmark = [pytest.mark.e2e, pytest.mark.destructive]


@pytest.fixture
def passport_page(traced_browser: Browser, live_server_url: str) -> Iterator[Page]:
    browser = traced_browser
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.goto("/passports/")
    card = page.locator("article").filter(
        has=page.get_by_role("heading", name=title, exact=True)
    )
    expect(card.locator(".leaflet-overlay-pane path")).to_have_count(2)
    expect(card.get_by_text(re.compile(r"of 2 airfields visited .*%"))).to_be_visible()
    card.get_by_role("link", name=title, exact=True).click()
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    expect(page.locator("#passport-progress-map .leaflet-map-pane")).to_have_count(1)
    click_and_settle(page, page.locator("table tbody a").first)
    page.locator("#visit-date").fill("2025-01-01")
    page.locator("#visit-note").fill("Browser visit")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(
        page.locator("p.text-break").filter(has_text=re.compile(r"^Browser visit$"))
    ).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(
        page.locator("#passport-progress-map .leaflet-overlay-pane path")
    ).to_have_count(1)
    # Checklist view: equal weight however often a field was visited.
    radius = "([c, f]) => window.ohPassportMarkerRadius(c, f)"
    assert {page.evaluate(radius, [n, False]) for n in (0, 1, 7, 5000)} == {7}
    # Frequency view: zero < one < many, and a busy home base is capped.
    sizes = [page.evaluate(radius, [n, True]) for n in (0, 1, 2, 8, 5000)]
    assert sizes == sorted(sizes) and sizes[0] < sizes[1] < sizes[3]
    assert sizes[-1] == 18
    page.locator("#passport-frequency").check()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#passport-progress")).to_have_attribute("data-frequency", "1")
    page.locator("#passport-progress-map .leaflet-overlay-pane path").click(force=True)
    expect(page.locator(".leaflet-popup-content a")).to_be_visible()
    page.locator(".leaflet-popup-content a").click()
    expect(
        page.locator("p.text-break").filter(has_text=re.compile(r"^Browser visit$"))
    ).to_be_visible()
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    # The slider starts in minutes and uses a practical range in either mode.
    expect(page.locator("#discovery-slider")).to_have_attribute("max", "300")
    page.locator("#discovery-mode").select_option("distance")
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
    gpx_link = page.get_by_role("link", name="Download GPX waypoints", exact=True)
    expect(gpx_link).to_have_attribute("hx-boost", "false")
    discovery_url = page.url
    with page.expect_download() as downloaded:
        gpx_link.click()
    gpx_path = downloaded.value.path()
    assert gpx_path is not None and downloaded.value.suggested_filename.endswith(
        "-unvisited.gpx"
    )
    points = (
        ET.parse(gpx_path).getroot().findall("{http://www.topografix.com/GPX/1/1}wpt")
    )
    assert [
        point.findtext("{http://www.topografix.com/GPX/1/1}name") for point in points
    ] == [destination]
    assert page.url == discovery_url
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
        # Nothing to download: no waypoint links (an empty file is not offered).
        expect(
            plain.get_by_role("link", name="Download KML waypoints", exact=True)
        ).to_have_count(0)
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    source_url = page.url
    click_and_settle(page, page.locator("table tbody a").first)
    page.locator("#visit-date").fill("2025-01-01")
    page.locator("#visit-note").fill("Private edition evidence")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(
        page.locator("p.text-break").filter(
            has_text=re.compile(r"^Private edition evidence$")
        )
    ).to_be_visible()
    page.get_by_role("link", name=title, exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_role("link", name="Create next edition", exact=True).click()
    page.locator("#next-title").fill(next_title)
    page.locator("#next-start").fill("2027-05-01")
    page.locator("#next-end").fill("2027-08-31")
    page.get_by_role("button", name="Copy and review targets", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text("EBNM")
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
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
    click_and_settle(page, page.locator("table tbody a").first)
    page.locator("#visit-date").fill("2025-03-01")
    page.locator("#visit-note").fill("Visit without voucher use")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(
        page.locator("p.text-break").filter(
            has_text=re.compile(r"^Visit without voucher use$")
        )
    ).to_be_visible()
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    click_and_settle(page, page.locator("table tbody a").first)
    page.locator("#visit-date").fill("2001-01-01")
    page.locator("#visit-note").fill("Private group visit note")
    page.get_by_role("button", name="Save visit", exact=True).click()
    expect(
        page.locator("p.text-break").filter(
            has_text=re.compile(r"^Private group visit note$")
        )
    ).to_be_visible()
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
    # Frequency counts your own visits until another scope is chosen. Wait for
    # each navigation: the page before it already shows the same controls.
    page.wait_for_url(re.compile(r"frequency=1"))
    expect(page.locator("#passport-count")).to_have_value("you")
    expect(page.locator("main")).to_contain_text("Your recorded visits")
    page.locator("#passport-count").select_option("group")
    page.get_by_role("button", name="Apply", exact=True).click()
    page.wait_for_url(re.compile(r"count=group"))
    expect(page.locator("#passport-count")).to_have_value("group")
    expect(page.locator("main")).to_contain_text("Recorded visits by the whole group")
    comparisons = page.locator("#passport-edition-comparisons")
    expect(comparisons).to_contain_text("Participant progress")
    expect(comparisons).to_contain_text("Browser group pilot")
    expect(comparisons).to_contain_text("Visited by anyone")
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
    context = browser.new_context(
        java_script_enabled=False,
        service_workers="block",
        viewport={"width": 390, "height": 844},
    )
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
        plain.get_by_role("link", name=re.compile(r"^EBNM —")).click()
        expect(
            plain.get_by_text("Visible to members of this group", exact=True)
        ).to_be_visible()
        plain.locator("#comment-body").fill("Which restaurant do you recommend?")
        plain.locator("#comment-category").select_option("question")
        plain.get_by_role("button", name="Post comment", exact=True).click()
        expect(plain.locator(".passport-comment-body")).to_have_text(
            "Which restaurant do you recommend?"
        )
        plain.get_by_text("Edit comment", exact=True).click()
        plain.locator('textarea[id^="edit-comment-body-"]').fill(
            "A restaurant question before visiting"
        )
        plain.get_by_role("button", name="Save Changes", exact=True).click()
        expect(plain.locator(".passport-comment-body")).to_have_text(
            "A restaurant question before visiting"
        )
        assert (
            "Private group visit note"
            not in plain.locator("#passport-discussion").inner_text()
        )
        assert plain.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
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
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.get_by_role("link", name="Group visit sharing", exact=True).click()
    page.locator("#group-name").fill("Group organiser")
    page.locator("#share-visits").check()
    page.get_by_role("button", name="Save sharing consent", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/series/\d+/$"))
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
            if not javascript:
                expect(friend.locator("#passport-targets")).to_contain_text(
                    "Visiting friend"
                )
            friend.locator("#passport-targets tbody a").first.click()
            if not javascript:
                expect(
                    friend.get_by_role(
                        "heading", name="Friends who have been here", exact=True
                    )
                ).to_be_visible()
                expect(
                    friend.get_by_text("Visiting friend", exact=True)
                ).to_be_visible()
            friend.locator("#visit-date").fill("2026-01-01")
            friend.get_by_role("button", name="Save visit", exact=True).click()
            expect(
                friend.get_by_role("button", name="Delete manual visit", exact=True)
            ).to_be_visible()
            friend.goto(passport_url)
            friend.get_by_role("link", name="Group members", exact=True).click()
            expect(
                friend.get_by_role("heading", name="Group members", exact=True)
            ).to_be_visible()
            friend.get_by_role("button", name="Leave edition", exact=True).click()
            friend.wait_for_url(live_server_url + "/passports/")
            expect(
                friend.get_by_text("Groups and edition history", exact=True)
            ).to_be_visible()
            friend.goto(invitation_url)
            expect(friend.locator("#join-name")).to_have_value(
                "Visiting friend" if javascript else "No-JS friend"
            )
            friend.locator("#join-sharing").check()
            friend.get_by_role("button", name="Join this passport", exact=True).click()
            friend.wait_for_url(re.compile(r"/passports/\d+/"))
            friend.locator("#passport-targets tbody a").first.click()
            expect(
                friend.get_by_role("button", name="Delete manual visit", exact=True)
            ).to_be_visible()
            alias = "Visiting friend" if javascript else "No-JS friend"
            page.goto(passport_url)
            expect(page.locator("#passport-targets")).to_contain_text(alias)
            page.locator(
                "#passport-progress-map .leaflet-overlay-pane path"
            ).first.click()
            expect(page.locator(".leaflet-popup-content")).to_contain_text(alias)
            page.get_by_role("link", name="Group members", exact=True).click()
            page.locator("#ownership-target").select_option(label=alias)
            page.get_by_role("button", name="Offer group ownership", exact=True).click()
            expect(
                page.get_by_text("Ownership offered to " + alias + ".", exact=True)
            ).to_be_visible()
            friend.goto(passport_url)
            friend.get_by_role("link", name="Group members", exact=True).click()
            friend.get_by_role(
                "button", name="Accept group ownership", exact=True
            ).click()
            expect(
                friend.get_by_role("button", name="Offer group ownership", exact=True)
            ).to_be_visible()
            friend.locator("#ownership-target").select_option(label="Group organiser")
            friend.get_by_role(
                "button", name="Offer group ownership", exact=True
            ).click()
            expect(
                friend.get_by_text("Ownership offered to Group organiser.", exact=True)
            ).to_be_visible()
            page.reload()
            page.get_by_role(
                "button", name="Accept group ownership", exact=True
            ).click()
            expect(
                page.get_by_role("button", name="Offer group ownership", exact=True)
            ).to_be_visible()
        finally:
            context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")


def test_invited_friend_registration_without_javascript(
    passport_page: Page,
    live_server_url: str,
    live_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import email_service

    if live_app is None:
        pytest.skip("requires in-process verification mail capture")

    sent: list[str] = []

    def send(
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        locale: str = "en",
    ) -> None:
        sent.append(text_body)

    monkeypatch.setattr(email_service, "send_email", send)
    page = passport_page
    title = "Verified guest " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-codes").fill("EBNM")
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.get_by_role("link", name="Group visit sharing", exact=True).click()
    page.locator("#group-name").fill("Signup organiser")
    page.locator("#share-visits").check()
    page.get_by_role("button", name="Save sharing consent", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/series/\d+/$"))
    page.goto(passport_url)
    page.get_by_role("link", name="Invite friends", exact=True).click()
    page.get_by_role("button", name="Create a new invitation", exact=True).click()
    invitation_url = page.locator("#passport-invite-url").input_value()
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        service_workers="block",
    )
    _block_external_network(context)
    try:
        guest = context.new_page()
        guest.goto(invitation_url)
        guest.get_by_role(
            "link", name="Create an account and join this passport", exact=True
        ).click()
        guest.locator("#registration-name").fill("New basic friend")
        guest.locator("#registration-email").fill(
            "guest-" + uuid4().hex[:12] + "@example.test"
        )
        guest.locator("#registration-password").fill("Verified guest password 123!")
        guest.locator("#registration-confirm").fill("Verified guest password 123!")
        guest.locator("#registration-sharing").check()
        guest.get_by_role("button", name="Send verification code", exact=True).click()
        expect(
            guest.get_by_role("heading", name="Verify your email", exact=True)
        ).to_be_visible()
        assert len(sent) == 1
        match = re.search(r"\b[0-9]{6}\b", sent[0])
        assert match
        guest.locator("#registration-code").fill(match.group())
        guest.get_by_role("button", name="Verify and continue", exact=True).click()
        guest.wait_for_url(re.compile(r"/passports/\d+/"))
        expect(guest.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(guest.get_by_role("link", name="Aircraft", exact=True)).to_have_count(0)
        denied = guest.goto(live_server_url + "/aircraft/")
        assert denied is not None and denied.status == 403
        guest.goto(passport_url)
        guest.locator("table tbody a").first.click()
        guest.locator("#visit-date").fill("2026-01-01")
        guest.get_by_role("button", name="Save visit", exact=True).click()
        expect(
            guest.get_by_role("button", name="Delete manual visit", exact=True)
        ).to_be_visible()
        guest.get_by_text("Edit manual visit", exact=True).click()
        edit_form = guest.locator('form:has(input[name="action"][value="edit_visit"])')
        edit_form.locator('input[name="visit_date"]').fill("2026-01-02")
        edit_form.locator('textarea[name="note"]').fill("Corrected visit date")
        edit_form.get_by_role("button", name="Save Changes", exact=True).click()
        expect(
            guest.locator("p.text-break").filter(
                has_text=re.compile(r"^Corrected visit date$")
            )
        ).to_be_visible()
        guest.goto(passport_url)
        guest.get_by_role("link", name="Where next?", exact=True).click()
        expect(
            guest.get_by_role("heading", name="Where next?", exact=True)
        ).to_be_visible()
        expect(guest.locator("#discovery-aircraft")).to_have_count(0)
        guest.locator("#discovery-query").fill("EBBR")
        guest.get_by_role("button", name="Search airfields", exact=True).click()
        expect(guest.locator("#discovery-origin")).to_contain_text("EBBR")
        guest.locator("#discovery-origin").select_option(index=1)
        guest.get_by_role("button", name="Find destinations", exact=True).click()
        guest.wait_for_url(lambda url: "origin=" in url)
        expect(guest.locator("#discovery-origin")).not_to_have_value("")
        guest.goto(live_server_url + "/passports/")
        guest.get_by_role("link", name="Create passport", exact=True).click()
        guest.locator("#passport-title").fill("Guest personal tour " + title)
        guest.locator("#passport-codes").fill("EBNM")
        guest.get_by_role("button", name="Add codes", exact=True).click()
        guest.get_by_role("button", name="Review passport", exact=True).click()
        guest.get_by_role("button", name="Save passport", exact=True).click()
        guest.wait_for_url(re.compile(r"/passports/\d+/$"))
        expect(
            guest.get_by_role(
                "heading", name="Guest personal tour " + title, exact=True
            )
        ).to_be_visible()
        guest.get_by_role("link", name="Edit passport", exact=True).click()
        guest.get_by_role("button", name="Review passport", exact=True).click()
        guest.get_by_role("button", name="Save passport", exact=True).click()
        guest.wait_for_url(re.compile(r"/passports/\d+/$"))
        guest.get_by_role("link", name="Delete passport", exact=True).click()
        guest.get_by_role("button", name="Delete passport", exact=True).click()
        guest.wait_for_url(live_server_url + "/passports/")
        with guest.expect_download() as downloaded:
            guest.get_by_role(
                "link", name="Export my passport data", exact=True
            ).click()
        download = downloaded.value
        assert download.suggested_filename == "openhangar-passports.zip"
        archive_path = download.path()
        assert archive_path is not None
        with zipfile.ZipFile(archive_path) as archive:
            exported = json.loads(archive.read("passport-data.json"))
        assert exported["format"] == "openhangar-passport-data"
        assert any(
            visit["note"] == "Corrected visit date" for visit in exported["visits"]
        )
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")


def test_passport_data_download_stays_outside_htmx(
    passport_page: Page, live_server_url: str
) -> None:
    page = passport_page
    page.goto(live_server_url + "/passports/")
    link = page.get_by_role("link", name="Export my passport data", exact=True)
    expect(link).to_have_attribute("hx-boost", "false")
    with page.expect_download() as downloaded:
        link.click()
    archive_path = downloaded.value.path()
    assert archive_path is not None
    assert downloaded.value.suggested_filename == "openhangar-passports.zip"
    with zipfile.ZipFile(archive_path) as archive:
        exported = json.loads(archive.read("passport-data.json"))
    assert exported["format"] == "openhangar-passport-data"
    assert exported["version"] == 1 and isinstance(exported["comments"], list)
    assert page.url == live_server_url + "/passports/"


def test_private_passport_print_with_selected_photo_htmx_and_no_javascript(
    passport_page: Page, live_server_url: str, tmp_path: Path
) -> None:
    from tests.test_passport_stamps import image_bytes

    page = passport_page
    title = "Printable " + uuid4().hex[:8]
    page.goto(live_server_url + "/passports/new")
    page.locator("#passport-title").fill(title)
    page.locator("#passport-start").fill("2001-01-01")
    page.locator("#passport-end").fill("2001-12-31")
    page.locator("#passport-codes").fill("EBNM EBOS")
    page.get_by_role("button", name="Add codes", exact=True).click()
    expect(page.locator("#passport-builder table")).to_contain_text("EBOS")
    click_and_settle(
        page, page.get_by_role("button", name="Review passport", exact=True)
    )
    page.get_by_role("button", name="Save passport", exact=True).click()
    page.wait_for_url(re.compile(r"/passports/\d+/$"))
    passport_url = page.url
    page.locator("#passport-targets tbody a").first.click()
    page.locator("#visit-date").fill("2001-01-01")
    page.locator("#visit-note").fill("PRIVATE PRINT NOTE")
    page.get_by_role("button", name="Save visit", exact=True).click()
    # Wait for the saved visit's page: a file chosen in the form it replaces
    # would be lost, and the upload click would send nothing.
    expect(
        page.locator("p.text-break").filter(
            has_text=re.compile(r"^PRIVATE PRINT NOTE$")
        )
    ).to_be_visible()
    page.locator("#stamp-photo").set_input_files(
        {"name": "stamp.jpg", "mimeType": "image/jpeg", "buffer": image_bytes()}
    )
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and "multipart/form-data"
            in response.request.headers.get("content-type", "")
        )
    ) as uploaded:
        page.get_by_role("button", name="Upload photo", exact=True).click()
    assert uploaded.value.status == 302
    expect(page.get_by_role("button", name="Delete photo", exact=True)).to_be_visible()
    expect(page.get_by_alt_text("Private stamp photo", exact=True)).to_be_visible()
    page.goto(passport_url)
    page.get_by_role("link", name="Print passport", exact=True).click()
    expect(page.locator("#passport-print")).to_have_attribute("data-oh-inited", "1")
    expect(page.locator("#passport-print-document")).to_contain_text("1 / 2")
    assert "PRIVATE PRINT NOTE" not in page.content()
    expect(page.locator(".passport-print-photo")).to_have_count(0)
    page.locator('input[name="photo"]').check()
    page.get_by_role("button", name="Update print preview", exact=True).click()
    expect(page.locator(".passport-print-photo")).to_have_count(1)
    selected_url = page.url
    image = page.locator(".passport-print-photo")
    source = image.get_attribute("src")
    assert source is not None
    held: list[Route] = []
    page.route("**/photos/*?print-wait=*", lambda route: held.append(route))
    page.evaluate(
        "() => { window.__passportPrinted = 0; window.print = () => { window.__passportPrinted++; }; }"
    )
    for number, fail in [(1, False), (2, True)]:
        held.clear()
        with page.expect_request("**/photos/*?print-wait=" + str(number)):
            image.evaluate(
                "(img, src) => { img.src = src; }",
                source + "?print-wait=" + str(number),
            )
        page.locator("#passport-print-button").click()
        expect(page.locator("#passport-print-button")).to_be_disabled()
        assert page.evaluate("window.__passportPrinted") == number - 1
        assert held
        for route in held:
            route.abort() if fail else route.continue_()
        page.wait_for_function("(n) => window.__passportPrinted === n", arg=number)
        expect(page.locator("#passport-print-button")).to_be_enabled()
    image.evaluate("(img, src) => { img.src = src; }", source)
    page.wait_for_function(
        "() => document.querySelector('.passport-print-photo').naturalWidth > 0"
    )
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.set_viewport_size({"width": 900, "height": 1100})
    page.emulate_media(media="print")
    expect(page.locator("#passport-print-button")).to_be_hidden()
    assert (
        page.locator("body").evaluate("el => getComputedStyle(el).backgroundColor")
        == "rgb(255, 255, 255)"
    )
    pdf = page.pdf(
        path=tmp_path / "passport-print-preview.pdf",
        format="A4",
        print_background=True,
        margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
    )
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 1
    page.screenshot(path=tmp_path / "passport-print-preview.png", full_page=True)
    page.emulate_media(media="screen")
    page.get_by_role("link", name="Back", exact=True).click()
    page.get_by_role("link", name="Print passport", exact=True).click()
    expect(page.locator("#passport-print")).to_have_attribute("data-oh-inited", "1")
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(java_script_enabled=False, service_workers="block")
    context.add_cookies(page.context.cookies())
    try:
        plain = context.new_page()
        plain.goto(selected_url)
        expect(plain.locator(".passport-print-photo")).to_have_count(1)
        expect(plain.locator("#passport-print-button")).to_be_hidden()
        plain.locator('input[name="photo"]').uncheck()
        plain.get_by_role("button", name="Update print preview", exact=True).click()
        expect(plain.locator(".passport-print-photo")).to_have_count(0)
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
