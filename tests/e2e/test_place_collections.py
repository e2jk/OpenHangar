"""Collection creation, validation and settings work through boosted navigation."""

import re
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
