"""
Tests for vendor asset integrity (A08/CWE-353).

All frontend libraries are served locally from app/static/vendor/ — no CDN
dependency at runtime. These tests enforce that guarantee and catch regressions
where a CDN URL accidentally gets (re-)introduced into a template.

Hash verification of the local files themselves is handled at download time by
scripts/fetch_vendor_assets.py (SHA-384, verified on every run).
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"

# External URLs that are legitimately referenced as *data values* in JS strings
# (map tile sources), not as script/style/font src/href attributes.
_ALLOWED_DATA_DOMAINS = {
    "basemaps.cartocdn.com",
    "tile.openstreetmap.org",
    "api.tiles.openaip.net",
}


def _cdn_script_link_tags(html: str) -> list[tuple[str, str]]:
    """Return [(url, full_tag)] for <script src> and <link href> pointing to external URLs."""
    results = []
    for m in re.finditer(
        r'(<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*"(https?://[^"]+)"[^>]*(?:/>|>(?:</script>)?))',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        results.append((m.group(2), m.group(1)))
    return results


def _all_cdn_tags() -> list[tuple[Path, str, str]]:
    """Return [(template_path, url, tag)] for every external CDN tag across all templates."""
    results = []
    for template in TEMPLATES_DIR.rglob("*.html"):
        html = template.read_text(encoding="utf-8")
        for url, tag in _cdn_script_link_tags(html):
            results.append((template.relative_to(TEMPLATES_DIR), url, tag))
    return results


class TestNoCDNInTemplates:
    def test_no_external_script_or_link_tags(self):
        """No template may load a <script> or <link> from an external URL.

        All JS and CSS must be served from app/static/vendor/ (local). If a new
        library is added, run scripts/fetch_vendor_assets.py to vendor it first.
        """
        cdn_tags = _all_cdn_tags()
        if cdn_tags:
            details = "\n".join(f"  {tpl}: {url}" for tpl, url, _ in cdn_tags)
            raise AssertionError(
                f"External CDN <script>/<link> tags found — vendor the library "
                f"locally instead:\n{details}"
            )

    def test_no_versioned_vendor_paths(self):
        """Vendor paths in templates must be version-agnostic (vendor/bootstrap/css/...).

        Version numbers belong only in scripts/fetch_vendor_assets.py. A versioned
        path in a template (e.g. vendor/bootstrap/5.3.3/css/...) means the template
        needs a manual edit on every library upgrade.
        """
        versioned = re.compile(r"vendor/[^/]+/\d+\.\d+[\d.]*/")
        found = []
        for template in TEMPLATES_DIR.rglob("*.html"):
            html = template.read_text(encoding="utf-8")
            for m in versioned.finditer(html):
                found.append(f"  {template.relative_to(TEMPLATES_DIR)}: {m.group(0)}")
        assert not found, (
            "Versioned vendor paths found in templates — use version-agnostic paths:\n"
            + "\n".join(found)
        )
