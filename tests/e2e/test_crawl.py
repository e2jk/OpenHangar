"""
Route crawl tests — driven by tests/e2e/routes.json.

Regenerate the inventory any time routes change:
    python scripts/generate_routes.py --db-url $DATABASE_URL

Two test classes:

TestGetCrawl   — visits every resolvable GET route while logged in; asserts
                 HTTP 200 and no Content-Security-Policy or JS console errors.
                 Each route is a separate parametrised dot in the test report.

TestAuthGuard  — sends every auth-required non-GET route without a session
                 cookie; asserts the server rejects the request (non-200).
                 Uses plain http.client — no browser needed.
"""

import http.client
import json
import re
import urllib.parse
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# ── Load route inventory ───────────────────────────────────────────────────────

_ROUTES_FILE = Path(__file__).parent / "routes.json"


def _load_routes() -> list[dict]:
    if not _ROUTES_FILE.exists():
        return []
    return json.loads(_ROUTES_FILE.read_text())["routes"]


_ALL_ROUTES = _load_routes()

# ── Skip lists ─────────────────────────────────────────────────────────────────

# GET routes skipped because they mutate session state or are impractical to crawl
_SKIP_GET_ENDPOINTS = {
    "auth.logout",           # clears the auth cookie — breaks subsequent tests
    "pilots.set_language",   # mutates the session locale
}

_SKIP_GET_RULES = {
    "/aircraft/<int:aircraft_id>/tracks/animation.gif",  # binary GIF, very slow
}

# ── Param → SEED key mapping ───────────────────────────────────────────────────
# Values that are strings are SEED dict keys; other types are used as literals.

_PARAM_MAP: dict[str, object] = {
    "aircraft_id": "ac_flt",
    "component_id": "component_id",
    "flight_id":    "fe_flt",
    "expense_id":   "expense_id",
    "snag_id":      "snag_id",
    "trigger_id":   "trigger_id",
    "res_id":       "res_id",
    "token_id":     "token_id",
    "photo_id":     "photo_id",
    "tenant_id":    "tenant_id",
    "user_id":      "user_id",
    "lang":         "fr",   # literal
    "code":         7700,   # literal
}


def _resolve_url(live_app, seed: dict, route: dict) -> str | None:
    """
    Build a URL for this route substituting real SEED IDs.
    Returns None if any required param is absent from the seed.
    """
    args = re.findall(r"<(?:\w+:)?(\w+)>", route["rule"])
    kwargs: dict = {}

    for arg in args:
        if arg in _PARAM_MAP:
            spec = _PARAM_MAP[arg]
            if isinstance(spec, str):           # SEED key
                v = seed.get(spec)
            else:                               # literal value
                v = spec
            if v is None:
                return None
            kwargs[arg] = v
        elif arg == "document_id":
            key = "document_id_pilot" if "/pilot/" in route["rule"] else "document_id_ac"
            v = seed.get(key)
            if v is None:
                return None
            kwargs[arg] = v
        elif arg == "entry_id":
            key = "wb_entry_id" if "/wb/" in route["rule"] else "pilot_entry_id"
            v = seed.get(key)
            if v is None:
                return None
            kwargs[arg] = v
        elif arg == "batch_id":
            return None     # no GPS/logbook import batch in E2E seed
        elif arg in ("token", "inv_id", "pending_id"):
            return None     # one-time tokens not queryable from seed
        elif arg == "filename":
            kwargs[arg] = "test.pdf"
        else:
            return None     # unknown param — skip rather than guess

    with live_app.test_request_context():
        from flask import url_for
        try:
            return url_for(route["endpoint"], **kwargs)
        except Exception:
            return None


# ── Parametrize lists (built at collection time) ───────────────────────────────

_GET_ROUTES = [
    r for r in _ALL_ROUTES
    if r["method"] == "GET"
    and r["endpoint"] not in _SKIP_GET_ENDPOINTS
    and r["rule"] not in _SKIP_GET_RULES
]

# Auth-required non-GET routes whose URL was resolvable at generate time
_AUTH_POST_ROUTES = [
    r for r in _ALL_ROUTES
    if r["method"] != "GET"
    and r["auth_required"]
    and r["url"] is not None
]


# ── GET crawl ──────────────────────────────────────────────────────────────────


class TestGetCrawl:
    """Every GET route must return HTTP 200 with no CSP or JS console errors."""

    @pytest.mark.parametrize("route", _GET_ROUTES, ids=lambda r: r["endpoint"])
    def test_returns_200_and_no_console_errors(
        self, logged_in_page, live_server_url, seed, live_app, route
    ):
        url = _resolve_url(live_app, seed, route)
        if url is None:
            pytest.skip(f"no seed data for params in {route['rule']}")

        console_errors: list[str] = []

        def _on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        logged_in_page.on("console", _on_console)
        try:
            resp = logged_in_page.goto(live_server_url + url)
            logged_in_page.wait_for_load_state("networkidle")
        finally:
            logged_in_page.remove_listener("console", _on_console)

        if resp is None:
            pytest.fail(f"no response received for {url}")

        assert resp.status == 200, f"expected 200, got {resp.status} for {url}"

        csp_errs = [m for m in console_errors if "Content-Security-Policy" in m]
        js_errs  = [m for m in console_errors if m not in csp_errs]

        if csp_errs:
            pytest.fail("CSP violation on {}:\n{}".format(url, "\n".join(csp_errs)))
        if js_errs:
            pytest.fail("JS console error on {}:\n{}".format(url, "\n".join(js_errs)))


# ── POST auth guard ────────────────────────────────────────────────────────────


class TestAuthGuard:
    """Auth-required non-GET endpoints must reject unauthenticated requests."""

    @pytest.mark.parametrize(
        "route",
        _AUTH_POST_ROUTES,
        ids=lambda r: f"{r['method']} {r['endpoint']}",
    )
    def test_unauthenticated_request_is_rejected(self, live_server_url, route):
        """
        Send a bare request with no session cookie and assert the server does
        not return HTTP 200.  Expected: 302 redirect to /login, 401, or 403.
        Uses http.client directly — no browser, no CSRF token.
        """
        parsed = urllib.parse.urlparse(live_server_url + route["url"])
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80)
        try:
            conn.request(
                route["method"],
                parsed.path,
                headers={"Content-Length": "0", "Content-Type": "application/x-www-form-urlencoded"},
            )
            resp = conn.getresponse()
            status = resp.status
        finally:
            conn.close()

        assert status != 200, (
            f"{route['method']} {route['url']} returned 200 to an unauthenticated request "
            f"— missing @login_required?"
        )
