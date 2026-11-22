"""Collection creation, validation and settings work through boosted navigation."""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_passport_builder import passport_page

__all__ = ["passport_page"]
pytestmark = [pytest.mark.e2e, pytest.mark.destructive]


def test_collection_forms_survive_htmx_and_fit_a_phone(passport_page: Page) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Browser places " + uuid4().hex[:8]
    page.goto("/places/")
    page.get_by_role("link", name="New collection", exact=True).click()
    page.get_by_label("Collection name", exact=True).fill("   ")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/places/collections/new")
        )
    ) as invalid:
        page.get_by_role("button", name="Create collection", exact=True).click()
    assert invalid.value.status == 400
    expect(page.get_by_role("alert")).to_contain_text("Enter a collection name")
    page.get_by_label("Collection name", exact=True).fill(title)
    page.get_by_role("button", name="Create collection", exact=True).click()
    page.wait_for_url(re.compile(r"/places/collections/\d+/settings$"))
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    page.get_by_label("Collection name", exact=True).fill(title + " renamed")
    page.get_by_role("button", name="Rename collection", exact=True).click()
    expect(
        page.get_by_role("heading", name=title + " renamed", exact=True)
    ).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.get_by_label("I confirm the permanent deletion of this collection.").check()
    page.get_by_role("button", name="Delete collection", exact=True).click()
    page.wait_for_url(re.compile(r"/places/$"))
    expect(page.get_by_role("heading", name="Places", exact=True)).to_be_visible()
    assert errors == []


def test_place_pin_photo_map_and_edit_survive_boosted_navigation(
    passport_page: Page, tmp_path: Path
) -> None:
    from PIL import Image

    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Browser museum " + uuid4().hex[:8]
    photo = tmp_path / "museum.png"
    Image.new("RGB", (40, 30), color="green").save(photo)
    page.goto("/places/")
    page.get_by_role("link", name="Add place", exact=True).click()
    expect(page.locator("#place-editor")).to_have_attribute("data-oh-inited", "1")
    expect(page.locator("#place-editor-map .leaflet-map-pane")).to_have_count(1)
    page.get_by_label("Place name", exact=True).fill(title)
    page.get_by_label("Coordinates or map link", exact=True).fill("50°30'N 4°12'E")
    page.get_by_role("button", name="Use coordinates", exact=True).click()
    expect(page.get_by_label("Latitude", exact=True)).to_have_value("50.5000000")
    expect(page.get_by_label("Longitude", exact=True)).to_have_value("4.2000000")
    expect(page.locator("#place-editor-map .leaflet-marker-icon")).to_have_count(1)
    page.get_by_label("Place photo (optional)", exact=True).set_input_files(photo)
    page.get_by_label("Tags", exact=True).fill("Museum")
    page.get_by_role("button", name="Save place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\d+$"))
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(page.locator("#places-map")).to_have_attribute("data-oh-inited", "1")
    expect(page.locator("img.place-detail-photo")).to_be_visible()
    page.get_by_role("link", name="Edit place", exact=True).click()
    expect(page.locator("#place-editor")).to_have_attribute("data-oh-inited", "1")
    page.get_by_label("Place name", exact=True).fill(title + " edited")
    # A map click updates the coordinates and leaves one draggable pin.
    before = page.get_by_label("Latitude", exact=True).input_value()
    page.locator("#place-editor-map").click(position={"x": 80, "y": 100})
    assert page.get_by_label("Latitude", exact=True).input_value() != before
    expect(page.locator("#place-editor-map .leaflet-marker-icon")).to_have_count(1)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.get_by_role("button", name="Save place", exact=True).click()
    expect(
        page.get_by_role("heading", name=title + " edited", exact=True)
    ).to_be_visible()
    page.get_by_label(
        "I confirm the permanent deletion of this place and its visits."
    ).check()
    page.get_by_role("button", name="Permanently delete place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\?.*state=bin"))
    assert errors == []
