"""Internet publication remains separate from group admission and private evidence."""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _block_external_network
from tests.e2e.test_passport_builder import passport_page

pytestmark = [pytest.mark.e2e, pytest.mark.destructive]

__all__ = ["passport_page", "pytestmark"]


def test_public_passport_map_print_withdrawal_and_revoke(
    passport_page: Page,
    live_server_url: str,
    tmp_path: Path,
) -> None:
    page = passport_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    title = "Public passport " + uuid4().hex[:8]
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
    page.locator("#passport-targets tbody a").first.click()
    page.locator("#visit-date").fill("2001-04-17")
    page.locator("#visit-note").fill("PRIVATE PUBLICATION TEST NOTE")
    page.get_by_role("button", name="Save visit", exact=True).click()
    page.goto(passport_url)
    page.get_by_role("link", name="Public sharing", exact=True).click()
    expect(page.locator("#passport-public-settings")).to_have_attribute(
        "data-oh-inited", "1"
    )
    page.get_by_role("button", name="Create or replace public link", exact=True).click()
    public_url = page.locator("#passport-public-url").input_value()
    expect(page.locator("#passport-public-settings")).to_have_attribute(
        "data-oh-inited", "1"
    )
    page.evaluate("""() => {
      window.__publicCopied = '';
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        writeText: value => { window.__publicCopied = value; return Promise.resolve(); }
      }});
    }""")
    page.locator("[data-public-copy]").click()
    expect(page.locator("[data-public-copy]")).to_have_text("Copied")
    assert page.evaluate("window.__publicCopied") == public_url
    page.evaluate("""() => {
      window.__publicCopied = '';
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
      document.execCommand = command => {
        if (command === 'copy') window.__publicCopied = document.getElementById('passport-public-url').value;
        return true;
      };
    }""")
    page.locator("[data-public-copy]").click()
    assert page.evaluate("window.__publicCopied") == public_url
    page.locator("#publish-visits").check()
    page.locator("#publish-frequency").check()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/public-sharing")
        )
    ) as saved:
        page.get_by_role("button", name="Save publication choices", exact=True).click()
    assert saved.value.status == 302
    browser = page.context.browser
    assert browser is not None
    context = browser.new_context(service_workers="block")
    _block_external_network(context)
    try:
        public = context.new_page()
        public.on("pageerror", lambda error: errors.append(str(error)))
        public.goto(public_url)
        expect(public.locator("#passport-public-map .leaflet-map-pane")).to_have_count(
            1
        )
        expect(public.locator("#passport-public-airfields")).to_contain_text("2001-04")
        assert "2001-04-17" not in public.content()
        assert "PRIVATE PUBLICATION TEST NOTE" not in public.content()
        public.locator("[data-public-frequency]").check()
        public.set_viewport_size({"width": 390, "height": 844})
        assert public.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        public.get_by_role("link", name="Print passport", exact=True).click()
        expect(public.locator("#passport-print")).to_have_attribute(
            "data-oh-inited", "1"
        )
        public.set_viewport_size({"width": 900, "height": 1100})
        public.emulate_media(media="print")
        expect(public.locator("#passport-print-button")).to_be_hidden()
        expect(public.locator("#passport-public-airfields")).to_be_visible()
        public.pdf(
            path=tmp_path / "public-passport.pdf",
            format="A4",
            prefer_css_page_size=True,
        )
        public.screenshot(
            path=tmp_path / "public-passport-print-preview.png", full_page=True
        )
        public.emulate_media(media="screen")
        plain_context = browser.new_context(
            java_script_enabled=False, service_workers="block"
        )
        _block_external_network(plain_context)
        try:
            plain = plain_context.new_page()
            plain.goto(public_url + "/print")
            expect(plain.locator("#passport-public-airfields")).to_contain_text(
                "2001-04"
            )
            plain.locator("#public-print-photos").check()
            plain.get_by_role("button", name="Update preview", exact=True).click()
            expect(plain.locator("#public-print-photos")).to_be_checked()
        finally:
            plain_context.close()
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/public-sharing")
            )
        ) as withdrawn:
            page.get_by_role(
                "button", name="Withdraw all my public information", exact=True
            ).click()
        assert withdrawn.value.status == 302
        expect(page.locator("#publish-visits")).not_to_be_checked()
        public.goto(public_url)
        expect(public.locator("#passport-public-airfields")).not_to_contain_text(
            "2001-04"
        )
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/public-sharing")
            )
        ) as revoked:
            page.get_by_role("button", name="Revoke public link", exact=True).click()
        assert revoked.value.status == 302
        expect(page.locator("#passport-public-url")).to_have_count(0)
        response = public.goto(public_url)
        assert response is not None and response.status == 404
        assert "no-store" in response.headers["cache-control"]
    finally:
        context.close()
    page.goto(passport_url)
    page.get_by_role("link", name="Delete passport", exact=True).click()
    page.get_by_role("button", name="Delete passport", exact=True).click()
    page.wait_for_url(live_server_url + "/passports/")
    assert not errors, errors
