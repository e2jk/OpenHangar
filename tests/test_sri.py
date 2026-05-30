"""
Tests for Subresource Integrity (SRI) on external CDN resources (A08/CWE-353).

Scans ALL HTML templates and asserts that every external <script src> and
<link href> carries both an integrity= (SHA-384 or SHA-512) attribute and
crossorigin="anonymous". This prevents a compromised CDN from injecting
arbitrary code into any page — including standalone templates like
share/public.html and errors/500.html that don't extend base.html.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"

# Accept sha384 or sha512 — both are strong enough; cdnjs ships sha512 hashes.
_INTEGRITY = re.compile(
    r'\bintegrity\s*=\s*"sha(?:384|512)-[A-Za-z0-9+/=]+"', re.IGNORECASE
)
_CROSSORIGIN = re.compile(r'\bcrossorigin\s*=\s*"anonymous"', re.IGNORECASE)


def _external_tags(html: str) -> list[tuple[str, str]]:
    """Return [(url, full_tag_text)] for every external CDN resource tag."""
    results = []
    for m in re.finditer(
        r'(<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*"(https?://[^"]+)"[^>]*(?:/>|>(?:</script>)?))',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        results.append((m.group(2), m.group(1)))
    return results


def _all_template_tags() -> list[tuple[Path, str, str]]:
    """Return [(template_path, url, tag_text)] for every external CDN tag across all templates."""
    results = []
    for template in TEMPLATES_DIR.rglob("*.html"):
        html = template.read_text(encoding="utf-8")
        for url, tag in _external_tags(html):
            results.append((template.relative_to(TEMPLATES_DIR), url, tag))
    return results


class TestSRIOnExternalResources:
    def test_all_external_resources_have_integrity(self):
        """Every external CDN resource in any template must carry a sha384 integrity attribute."""
        all_tags = _all_template_tags()
        assert all_tags, "No external resource tags found — templates may have changed"
        missing = [
            f"{tpl}:{url}" for tpl, url, tag in all_tags if not _INTEGRITY.search(tag)
        ]
        assert not missing, (
            f"External resources missing integrity= attribute: {missing}"
        )

    def test_all_external_resources_have_crossorigin(self):
        """Every external CDN resource in any template must carry crossorigin=\"anonymous\"."""
        all_tags = _all_template_tags()
        missing = [
            f"{tpl}:{url}" for tpl, url, tag in all_tags if not _CROSSORIGIN.search(tag)
        ]
        assert not missing, (
            f'External resources missing crossorigin="anonymous": {missing}'
        )

    def test_integrity_uses_strong_hash(self):
        """SRI hashes must use SHA-384 or SHA-512 (not the weaker SHA-256 or SHA-1)."""
        all_tags = _all_template_tags()
        weak = [
            f"{tpl}:{url}"
            for tpl, url, tag in all_tags
            if re.search(r'\bintegrity\s*=\s*"sha(?:256|1)-', tag, re.IGNORECASE)
        ]
        assert not weak, f"Resources using weak hash algorithm (sha256/sha1): {weak}"

    def test_base_html_has_external_resources(self):
        """Sanity check: base.html must still reference CDN resources."""
        base_html = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
        assert _external_tags(base_html), (
            "base.html has no external CDN tags — template may have changed"
        )
