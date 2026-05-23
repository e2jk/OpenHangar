#!/usr/bin/env python3
"""
Take documentation screenshots of the OpenHangar UI.

Reads docs/screenshots/manifest.yml, drives a headless Chromium browser via
Playwright, and writes PNGs into docs/screenshots/.

Requirements (see scripts/requirements-screenshots.txt):
    pip install -r scripts/requirements-screenshots.txt
    playwright install chromium

Usage:
    python scripts/take_screenshots.py
    python scripts/take_screenshots.py --base-url http://localhost:5000
    python scripts/take_screenshots.py --id dashboard --id landing  # specific shots only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pyotp
    import yaml
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
except ImportError as e:
    print(f"Missing dependency: {e}")
    print(
        "Run:  pip install -r requirements-screenshots.txt && playwright install chromium"
    )
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "docs" / "screenshots" / "manifest.yml"
OUTPUT_DIR = REPO_ROOT / "docs" / "screenshots"


# ── Auth ──────────────────────────────────────────────────────────────────────


def _login(page: Page, base_url: str, creds: dict) -> None:
    """Complete the two-step login (credentials → TOTP)."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    page.fill('input[name="email"]', creds["email"])
    page.fill('input[name="password"]', creds["password"])
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")

    # If TOTP step appears, fill in the current code
    if "step=totp" in page.url or page.locator('input[name="totp_code"]').count():
        totp_code = pyotp.TOTP(creds["totp_secret"]).now()
        page.fill('input[name="totp_code"]', totp_code)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")


# ── ID resolution helpers ─────────────────────────────────────────────────────


def _resolve_aircraft_id(page: Page, base_url: str, registration: str) -> str:
    """Return the DB ID for an aircraft with the given registration."""
    page.goto(f"{base_url}/aircraft/")
    page.wait_for_load_state("networkidle")

    aircraft_id: str | None = page.evaluate(
        """(reg) => {
            const links = document.querySelectorAll('a[href^="/aircraft/"]');
            for (const link of links) {
                const href = link.getAttribute('href');
                if (!/^\\/aircraft\\/\\d+$/.test(href)) continue;
                const section = link.closest('tr, .card, li, .aircraft-row')
                                || link.parentElement;
                if (section && section.textContent.includes(reg)) {
                    return href.match(/\\/aircraft\\/(\\d+)$/)[1];
                }
            }
            return null;
        }""",
        registration,
    )

    if not aircraft_id:
        raise ValueError(
            f"Could not find aircraft with registration {registration!r} on /aircraft/"
        )
    return aircraft_id


# ── Screenshot ────────────────────────────────────────────────────────────────


def _take(
    context: BrowserContext,
    base_url: str,
    entry: dict,
    id_cache: dict[str, str],
) -> None:
    page = context.new_page()

    # Resolve URL
    if "url" in entry:
        url = base_url + entry["url"]
    elif "url_template" in entry:
        reg = entry.get("resolve_aircraft")
        if not reg:
            raise ValueError(
                f"url_template requires resolve_aircraft in entry {entry['id']!r}"
            )
        if reg not in id_cache:
            id_cache[reg] = _resolve_aircraft_id(page, base_url, reg)
        url = base_url + entry["url_template"].format(aircraft_id=id_cache[reg])
    else:
        raise ValueError(f"Entry {entry['id']!r} has no 'url' or 'url_template'")

    page.goto(url)
    page.wait_for_load_state("networkidle")

    # Optional: wait for an additional selector to be visible before shooting
    if "wait_for" in entry:
        page.wait_for_selector(entry["wait_for"])

    out_path = OUTPUT_DIR / entry["output"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out_path), full_page=entry.get("full_page", False))
    print(f"  ✓  {entry['id']:30s}  →  docs/screenshots/{entry['output']}")

    page.close()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url", default=None, help="Override base URL from manifest"
    )
    parser.add_argument(
        "--id",
        dest="ids",
        action="append",
        metavar="ID",
        help="Only take the screenshot(s) with this id (repeatable)",
    )
    parser.add_argument(
        "--ignore-https-errors",
        action="store_true",
        help="Ignore TLS certificate errors (useful for self-signed dev certs)",
    )
    args = parser.parse_args()

    manifest = yaml.safe_load(MANIFEST_PATH.read_text())
    base_url = (
        args.base_url or manifest.get("base_url", "http://localhost:5000")
    ).rstrip("/")
    auth_configs: dict = manifest.get("auth", {})
    entries: list[dict] = manifest.get("screenshots", [])

    if args.ids:
        entries = [e for e in entries if e["id"] in args.ids]
        if not entries:
            print(f"No screenshots found with id(s): {args.ids}")
            sys.exit(1)

    print(f"Taking {len(entries)} screenshot(s) against {base_url}")

    # Group entries by auth profile so we reuse the same browser context
    # (avoids re-logging in for every screenshot)
    by_auth: dict[str, list[dict]] = {}
    for entry in entries:
        key = entry.get("auth", "none")
        by_auth.setdefault(key, []).append(entry)

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch()
        id_cache: dict[str, str] = {}

        for auth_name, group in by_auth.items():
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=args.ignore_https_errors,
            )

            # Pre-login once for the whole group
            if auth_name != "none":
                if auth_name not in auth_configs:
                    raise ValueError(
                        f"Auth profile {auth_name!r} not defined in manifest"
                    )
                setup_page = context.new_page()
                _login(setup_page, base_url, auth_configs[auth_name])
                setup_page.close()

            for entry in group:
                _take(context, base_url, entry, id_cache)

            context.close()

        browser.close()

    print(f"\nDone. {len(entries)} screenshot(s) written to docs/screenshots/")


if __name__ == "__main__":
    main()
