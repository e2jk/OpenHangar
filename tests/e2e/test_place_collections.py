"""Collection creation, validation and settings work through boosted navigation."""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e.test_passport_builder import passport_page

__all__ = ["passport_page", "pytestmark"]
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
    page.get_by_label("Place name (optional)", exact=True).fill(title)
    page.locator("#place-address-query").fill("50°30'N 4°12'E")
    page.get_by_role("button", name="Search", exact=True).click()
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
    page.get_by_role("link", name="Add place", exact=True).click()
    expect(page.get_by_label("Place name (optional)", exact=True)).to_have_value("")
    page.get_by_role("link", name="Cancel", exact=True).click()
    page.get_by_role("link", name=title, exact=True).click()
    page.get_by_role("link", name="Edit place", exact=True).click()
    expect(page.locator("#place-editor")).to_have_attribute("data-oh-inited", "1")
    page.get_by_label("Place name (optional)", exact=True).fill(title + " edited")
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


def test_address_search_is_submit_only_and_selection_preserves_a_title(
    passport_page: Page,
) -> None:
    page = passport_page
    requests: list[str] = []

    def search_response(route: Route) -> None:
        requests.append(route.request.post_data or "")
        route.fulfill(
            json={
                "results": [
                    {
                        "name": "Museum <example>",
                        "address": "Museum street",
                        "latitude": 50.5,
                        "longitude": 4.2,
                    },
                    {
                        "name": "Second museum",
                        "address": "Other street",
                        "latitude": 50.6,
                        "longitude": 4.3,
                    },
                ]
            }
        )

    page.route("**/places/geocode", search_response)
    page.goto("/places/new")
    page.get_by_label("Place name, address or coordinates", exact=True).fill("Museum")
    assert requests == []
    page.get_by_role("button", name="Search", exact=True).click()
    page.get_by_role(
        "button", name="Museum <example> — Museum street", exact=True
    ).click()
    expect(page.get_by_label("Place name (optional)", exact=True)).to_have_value(
        "Museum <example>"
    )
    expect(page.get_by_label("Address (optional)", exact=True)).to_have_value(
        "Museum street"
    )
    expect(page.get_by_label("Latitude", exact=True)).to_have_value("50.5000000")
    second = page.get_by_role("button", name="Second museum — Other street")
    second.click()
    expect(second).to_have_attribute("aria-pressed", "true")
    expect(
        page.get_by_role("button", name="Museum <example> — Museum street")
    ).to_have_attribute("aria-pressed", "false")
    expect(page.get_by_label("Place name (optional)", exact=True)).to_have_value(
        "Second museum"
    )
    expect(page.get_by_label("Latitude", exact=True)).to_have_value("50.6000000")
    assert len(requests) == 1
    page.get_by_label("Place name (optional)", exact=True).fill("My own title")
    page.get_by_label("Place name, address or coordinates", exact=True).press("Enter")
    page.get_by_role(
        "button", name="Museum <example> — Museum street", exact=True
    ).click()
    expect(page.get_by_label("Place name (optional)", exact=True)).to_have_value(
        "My own title"
    )
    page.unroute("**/places/geocode")
    page.route("**/places/geocode", lambda route: route.fulfill(json={"results": []}))
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.locator("#place-address-error")).to_contain_text(
        "No matching places found"
    )
    page.unroute("**/places/geocode")
    page.route(
        "**/places/geocode",
        lambda route: route.fulfill(status=429, json={"error": "Search is busy"}),
    )
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.locator("#place-address-error")).to_contain_text("Search is busy")
    expect(page.locator("#place-editor-map .leaflet-marker-icon")).to_have_count(1)


def test_save_a_link_without_locating_it(passport_page: Page) -> None:
    page = passport_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/places/new?external_url=https%3A%2F%2Fexample.org%2Fidea")
    expect(page.get_by_label("Link (optional)", exact=True)).to_have_value(
        "https://example.org/idea"
    )
    expect(page.get_by_label("Place name (optional)", exact=True)).to_have_value("")
    expect(page.get_by_label("Latitude", exact=True)).to_have_value("")
    button = page.get_by_role("button", name="Save place", exact=True)
    expect(button).to_be_in_viewport()
    button.click()
    page.wait_for_url(re.compile(r"/places/\d+$"))
    expect(page.get_by_role("heading", name="example.org", exact=True)).to_be_visible()
    expect(page.get_by_text("Location still to add", exact=True)).to_be_visible()
    expect(page.locator("#places-map")).to_have_count(0)
    page.get_by_role("link", name="Edit place", exact=True).click()
    page.get_by_label("Place name (optional)", exact=True).fill("An idea to finish")
    page.get_by_role("button", name="Save place", exact=True).click()
    expect(
        page.get_by_role("heading", name="An idea to finish", exact=True)
    ).to_be_visible()
    page.get_by_label(
        "I confirm the permanent deletion of this place and its visits."
    ).check()
    page.get_by_role("button", name="Permanently delete place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\?collection=\d+&state=bin$"))


def test_link_only_idea_can_gain_an_airfield_location_and_own_visit(
    passport_page: Page,
) -> None:
    import json

    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 390, "height": 844})
    title = "Airfield-linked museum " + uuid4().hex[:8]
    page.goto("/places/new?external_url=https%3A%2F%2Fexample.org%2Ftrip")
    page.get_by_role("button", name="Save place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\d+$"))
    detail = page.url
    expect(page.get_by_text("No linked airfield", exact=True)).to_be_visible()
    page.get_by_role("link", name="Manage airfield links", exact=True).click()
    page.get_by_label("Airfield name or code", exact=True).fill("EBNM")
    page.get_by_role("button", name="Search", exact=True).click()
    row = page.locator("#airfield-candidates > li").filter(has_text="EBNM")
    row.get_by_role("button", name="Link airfield", exact=True).click()
    page.get_by_label("How to get there", exact=True).fill("Taxi then walk")
    page.get_by_label("Preferred", exact=True).check()
    page.get_by_role("button", name="Save airfield link", exact=True).click()
    expect(page.get_by_label("How to get there", exact=True)).to_have_text(
        "Taxi then walk"
    )
    page.get_by_role("link", name="Back to place", exact=True).click()
    expect(page.get_by_text("Taxi then walk", exact=True)).to_be_visible()
    expect(page.locator("#places-map")).to_have_attribute("data-oh-inited", "1")
    assert json.loads(page.locator("#places-map").get_attribute("data-links")) == []
    page.get_by_role("link", name="Edit place", exact=True).click()
    page.get_by_label("Place name (optional)", exact=True).fill(title)
    page.locator("#place-address-query").fill("50.49, 4.77")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_label("Latitude", exact=True)).to_have_value("50.4900000")
    page.get_by_role("button", name="Save place", exact=True).click()
    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    assert len(json.loads(page.locator("#places-map").get_attribute("data-links"))) == 1
    page.get_by_role("button", name="I was here", exact=True).click()
    expect(page.locator("#place-visits")).to_contain_text("Last visited")
    assert json.loads(page.locator("#places-map").get_attribute("data-points"))[0][
        "visited"
    ]
    page.locator("#place-visits summary").click()
    page.get_by_role("button", name="Remove this visit", exact=True).click()
    expect(page.locator("#place-visits")).to_contain_text(
        "Nobody has recorded a visit yet"
    )
    assert not json.loads(page.locator("#places-map").get_attribute("data-points"))[0][
        "visited"
    ]
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.goto("/places/")
    page.get_by_label("Airfield links", exact=True).select_option("linked")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    page.wait_for_url(re.compile(r"[?&]linked=linked(?:&|$)"))
    expect(page.get_by_role("link", name=title, exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Download GPX", exact=True)).to_have_attribute(
        "hx-boost", "false"
    )
    page.get_by_label("Airfield links", exact=True).select_option("unlinked")
    page.get_by_role("button", name="Apply filters", exact=True).click()
    page.wait_for_url(re.compile(r"[?&]linked=unlinked(?:&|$)"))
    expect(page.get_by_role("link", name=title, exact=True)).to_have_count(0)
    page.goto(detail)
    page.get_by_label(
        "I confirm the permanent deletion of this place and its visits."
    ).check()
    page.get_by_role("button", name="Permanently delete place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\?collection=\d+&state=bin$"))
    assert errors == []


def test_add_place_can_link_an_airfield_from_the_collapsed_picker(
    passport_page: Page,
) -> None:
    page = passport_page
    page.goto("/places/new")
    picker = page.locator("#place-new-airfield")
    query = picker.get_by_label("Airfield name or code", exact=True)
    expect(query).to_be_hidden()
    picker.locator("summary").click()
    query.fill("EBNM")
    query.press("Enter")
    choice = picker.get_by_label("Airfield", exact=True)
    expect(choice.get_by_role("option", name=re.compile("EBNM"))).to_have_count(1)
    key = choice.get_by_role("option", name=re.compile("EBNM")).get_attribute("value")
    assert key is not None
    choice.select_option(key)
    page.get_by_label("Place name (optional)", exact=True).fill(
        "Airfield place " + uuid4().hex[:8]
    )
    page.get_by_role("button", name="Save place", exact=True).click()
    page.wait_for_url(re.compile(r"/places/\d+$"))
    expect(page.locator("#place-airfields")).to_contain_text("EBNM")


def test_bulk_move_keeps_a_group_of_places_together(passport_page: Page) -> None:
    page = passport_page
    suffix = uuid4().hex[:8]
    destination = f"Bulk destination {suffix}"
    page.goto("/places/collections/new")
    page.get_by_label("Collection name", exact=True).fill(destination)
    page.get_by_role("button", name="Create collection", exact=True).click()
    page.wait_for_url(re.compile(r"/places/collections/\d+/settings$"))
    collection_id = page.url.split("/")[-2]

    names = [f"First place {suffix}", f"Second place {suffix}"]
    for name in names:
        page.goto("/places/new")
        page.get_by_label("Place name (optional)", exact=True).fill(name)
        page.locator("#place-address-query").fill("50.49, 4.77")
        page.get_by_role("button", name="Search", exact=True).click()
        expect(page.get_by_label("Latitude", exact=True)).to_have_value("50.4900000")
        page.get_by_role("button", name="Save place", exact=True).click()
        page.wait_for_url(re.compile(r"/places/\d+$"))

    page.goto("/places/")
    first = page.get_by_label(f"Select {names[0]}", exact=True)
    expect(first).to_be_hidden()
    page.locator("#places-bulk-move summary").click()
    first.check()
    page.get_by_label(f"Select {names[1]}", exact=True).check()
    page.get_by_label("Destination collection", exact=True).select_option(collection_id)
    page.get_by_role("button", name="Move", exact=True).click()
    page.wait_for_url(re.compile(rf"/places/\?collection={collection_id}$"))
    for name in names:
        expect(page.get_by_role("link", name=name, exact=True)).to_be_visible()


def test_place_offers_actions_for_past_landings(passport_page: Page) -> None:
    page = passport_page
    page.goto("/places/")
    page.get_by_role("link", name="Pairi Daiza", exact=True).click()
    panel = page.locator("#place-visit-suggestions")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("You landed at EBCI")
    assert panel.get_by_role("button", name="Record a visit").count() > 3

    place_url = page.url
    dismiss = panel.locator('form[action$="/dismiss"]').first
    dismissed_action = dismiss.get_attribute("action")
    assert dismissed_action is not None
    dismiss.get_by_role("button", name="Dismiss", exact=True).click()
    expect(panel.locator(f'form[action="{dismissed_action}"]')).to_have_count(0)
    assert page.url == place_url

    record = panel.locator('form:not([action$="/dismiss"])').first
    action = record.get_attribute("action")
    assert action is not None
    record.get_by_role("button", name="Record a visit", exact=True).click()
    expect(panel.locator(f'form[action="{action}"]')).to_have_count(0)
    expect(
        page.locator("#place-visits").get_by_role("link", name="Flight")
    ).to_be_visible()
    assert page.url == place_url
