"""
Verify that every <a> link in app/templates/ that points to a non-page route
carries hx-boost="false".

Background
----------
base.html sets hx-boost="true" on <body>, so HTMX intercepts every internal
<a> click and performs a body swap instead of a full page load. This is correct
for routes that return a full HTML page, but silently breaks routes that:
  - return binary content (JPEG, PNG, GIF, ZIP)
  - return JSON (API endpoints)
  - perform session side-effects (logout, language switch)

Those links must carry hx-boost="false" to opt out of the body-swap intercept.
If the attribute is missing, the response is discarded inside HTMX's swap
machinery with no visible error — a silent regression.

Maintenance
-----------
When adding a new route whose response should NOT be processed as an hx-boost
body swap, add it to _NON_PAGE_ROUTES below. The test will then automatically
enforce that every template link to that route carries hx-boost="false".

Implementation note
-------------------
Templates are scanned as raw source (Jinja2 not rendered). The regex extracts
every <a …> opening tag (including multi-line tags) and checks whether:
  a) the tag references the endpoint via url_for('endpoint', …)  — OR —
  b) the tag's href contains a known static URL fragment for that endpoint.
Any matching tag that lacks hx-boost="false" is reported as a violation.
"""

import pathlib
import re

import pytest

_TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / "app" / "templates"

# ---------------------------------------------------------------------------
# Non-page routes: routes whose responses must never be processed as an
# hx-boost body swap. Each entry is (endpoint_name, url_fragment_or_None).
#
# endpoint_name  — matched against url_for('endpoint_name', …) in templates.
# url_fragment   — matched against the href literal for hardcoded links
#                  (None when the template always uses url_for).
# ---------------------------------------------------------------------------
_NON_PAGE_ROUTES = [
    # Session-destructive: a body-swap logout would clear cookies via XHR
    # but the page would not reload, leaving the user appearing still logged in.
    ("auth.logout", "/logout"),
    # Session-mutating: changes the locale stored in the session cookie.
    ("set_language", "/set-language/"),
    # Health probes: return plain text / JSON, not an HTML page.
    ("health", "/health"),
    ("health_ready", "/health/ready"),
    # Binary responses: HTMX would try to swap the binary stream as body HTML.
    ("aircraft.serve_photo", None),  # JPEG — linked via url_for only
    ("aircraft.flight_tracks_gif", "/tracks/animation.gif"),
    ("share.token_qr", None),  # PNG — linked via url_for only
    ("documents.download_all_documents", "/download-all"),
    ("pilots.pilot_tracks_gif", "/pilot/tracks/animation.gif"),
    ("flights.flight_track_image", "/track/image.png"),
    ("flights.flight_track_gif", "/track/animation.gif"),
    # JSON API: returns {"status": …}, not an HTML page.
    ("config.upgrade_status", "/upgrade-status"),
]

# Matches an entire <a …> opening tag, including multi-line ones.
# Stops at the first > that closes the tag (Jinja2 expressions inside
# attribute values do not contain unescaped >, so this is safe).
_TAG_RE = re.compile(r"<a\b[^>]*>", re.DOTALL | re.IGNORECASE)


def _tag_references_endpoint(tag: str, endpoint: str, fragment: str | None) -> bool:
    """Return True if this <a> tag links to the given non-page endpoint."""
    if f"url_for('{endpoint}'" in tag or f'url_for("{endpoint}"' in tag:
        return True
    if fragment and fragment in tag:
        return True
    return False


class TestHxBoostFalseOnNonPageLinks:
    """Every <a> link to a non-page route must carry hx-boost="false"."""

    @pytest.mark.parametrize(
        "endpoint,fragment",
        _NON_PAGE_ROUTES,
        ids=[ep for ep, _ in _NON_PAGE_ROUTES],
    )
    def test_non_page_link_has_hxboost_false(
        self, endpoint: str, fragment: str | None
    ) -> None:
        violations: list[str] = []

        for tmpl in sorted(_TEMPLATE_DIR.rglob("*.html")):
            content = tmpl.read_text(encoding="utf-8")
            rel = tmpl.relative_to(_TEMPLATE_DIR)

            for m in _TAG_RE.finditer(content):
                tag = m.group(0)
                if not _tag_references_endpoint(tag, endpoint, fragment):
                    continue
                if 'hx-boost="false"' not in tag:
                    line = content[: m.start()].count("\n") + 1
                    violations.append(f"  {rel}:{line}  {tag[:120].strip()}")

        assert not violations, (
            f'\nLinks to non-page route {endpoint!r} are missing hx-boost="false".\n'
            "Without it, hx-boost intercepts the click and tries to swap the\n"
            "response as body HTML, silently discarding binary/JSON/session responses.\n\n"
            'Add hx-boost="false" to each <a> tag below.\n'
            "If this is a new non-page route, also add it to _NON_PAGE_ROUTES in\n"
            "tests/test_htmx_boost_links.py so future template links are checked too.\n\n"
            + "\n".join(violations)
        )
