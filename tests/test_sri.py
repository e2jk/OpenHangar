"""
Tests for Subresource Integrity (SRI) on external CDN resources (A08/CWE-353).

Parses base.html and asserts that every external <script src> and <link href>
carries both an integrity= (SHA-384) attribute and crossorigin="anonymous".
This prevents a compromised CDN from injecting arbitrary code into the app.
"""

import re
from pathlib import Path

BASE_HTML = Path(__file__).parent.parent / "app" / "templates" / "base.html"

# Regex to find all <script src="..."> and <link href="..."> tags that reference
# an absolute external URL (http:// or https://).
_EXTERNAL_TAG = re.compile(
    r'<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*"(https?://[^"]+)"[^>]*>',
    re.IGNORECASE | re.DOTALL,
)
_INTEGRITY = re.compile(r'\bintegrity\s*=\s*"sha384-[A-Za-z0-9+/=]+"', re.IGNORECASE)
_CROSSORIGIN = re.compile(r'\bcrossorigin\s*=\s*"anonymous"', re.IGNORECASE)


def _external_tags(html: str) -> list[tuple[str, str]]:
    """Return [(url, full_tag_text)] for every external resource tag."""
    results = []
    for m in re.finditer(
        r'(<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*"(https?://[^"]+)"[^>]*(?:/>|>(?:</script>)?))',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        results.append((m.group(2), m.group(1)))
    return results


class TestSRIOnExternalResources:
    def _html(self) -> str:
        return BASE_HTML.read_text(encoding="utf-8")

    def test_all_external_resources_have_integrity(self):
        """Every external CDN resource must carry a sha384 integrity attribute."""
        html = self._html()
        tags = _external_tags(html)
        assert tags, "No external resource tags found — base.html may have changed"
        missing = [url for url, tag in tags if not _INTEGRITY.search(tag)]
        assert not missing, (
            f"External resources missing integrity= attribute: {missing}"
        )

    def test_all_external_resources_have_crossorigin(self):
        """Every external CDN resource must carry crossorigin=\"anonymous\"."""
        html = self._html()
        tags = _external_tags(html)
        missing = [url for url, tag in tags if not _CROSSORIGIN.search(tag)]
        assert not missing, (
            f"External resources missing crossorigin=\"anonymous\": {missing}"
        )

    def test_integrity_uses_sha384(self):
        """SRI hashes must use SHA-384 (stronger than SHA-256)."""
        html = self._html()
        tags = _external_tags(html)
        weak = [
            url
            for url, tag in tags
            if re.search(r'\bintegrity\s*=\s*"sha(?:256|1)-', tag, re.IGNORECASE)
        ]
        assert not weak, f"Resources using weak hash algorithm (sha256/sha1): {weak}"
