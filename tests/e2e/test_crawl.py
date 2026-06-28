"""
Route crawl tests — driven by tests/e2e/routes.json.

Regenerate the inventory any time routes change:
    python scripts/generate_routes.py --db-url $DATABASE_URL

Test classes (run in definition order):

TestGetCrawl        — visits every resolvable GET route while logged in; asserts
                      HTTP 200 and no Content-Security-Policy or JS console errors.
                      Each route is a separate parametrised dot in the test report.

TestAuthGuard       — sends every auth-required non-GET route without a session
                      cookie; asserts the server rejects the request (non-200).
                      Uses plain http.client — no browser needed.

TestKnownBehaviors  — dedicated assertions for endpoints with non-200 or binary
                      responses that cannot be covered by the generic crawl.

TestEndOfSession    — session-mutating actions (language change, logout) that must
                      run last so they do not affect earlier tests.
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

# GET routes excluded from the generic crawl.
# Each has a dedicated test elsewhere in this file, or is a known untestable case.
_SKIP_GET_ENDPOINTS = {
    "auth.logout",  # session-destructive — tested in TestEndOfSession
    "set_language",  # session-mutating — tested in TestEndOfSession
    "aircraft.serve_photo",  # binary JPEG — tested in TestKnownBehaviors
    "share.token_qr",  # binary PNG — tested in TestKnownBehaviors
    "not_yet_implemented",  # returns 501 by design — tested in TestKnownBehaviors
    "health_ready",  # returns 404 for non-loopback callers by design (loopback-only probe)
    "documents.download_all_documents",  # ZIP download — needs dedicated UI interaction test
    "pilots.pilot_tracks_gif",  # GIF download — needs dedicated UI interaction test
    "flights.flight_track_image",  # binary PNG — requires a GPS track on the seed flight
    "flights.flight_track_gif",    # binary GIF — requires a GPS track on the seed flight
    "config.upgrade_status",  # returns 404 when OPENHANGAR_UPGRADE_DIR is not set; covered by tests/test_config_upgrade.py
}

_SKIP_GET_RULES = {
    "/aircraft/<int:aircraft_id>/tracks/animation.gif",  # binary GIF, very slow
}

# ── Param → SEED key mapping ───────────────────────────────────────────────────
# String values are SEED dict keys; non-string values are used as literals.

_PARAM_MAP: dict[str, object] = {
    "aircraft_id": "ac_flt",
    "component_id": "component_id",
    "flight_id": "fe_flt",
    "expense_id": "expense_id",
    "snag_id": "snag_id",
    "trigger_id": "trigger_id",
    "res_id": "res_id",
    "token_id": "token_id",
    "photo_id": "photo_id",
    "tenant_id": "tenant_id",
    "user_id": "user_id",
    "code": 7700,  # literal squawk code for the emergency page
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
            if isinstance(spec, str):  # SEED key
                v = seed.get(spec)
            else:  # literal value
                v = spec
            if v is None:
                return None
            kwargs[arg] = v
        elif arg == "document_id":
            key = (
                "document_id_pilot" if "/pilot/" in route["rule"] else "document_id_ac"
            )
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
        elif arg == "token":
            token_key = {
                "share.public_view": "share_token",
                "auth.reset_password": "reset_token",
                "users.accept_invite": "invite_token",
            }.get(route["endpoint"])
            if token_key is None:
                return None
            v = seed.get(token_key)
            if v is None:
                return None
            kwargs[arg] = v
        elif arg in ("inv_id", "pending_id"):
            return None  # one-time tokens not queryable from seed
        elif arg == "filename":
            kwargs[arg] = "test.pdf"
        else:
            return None  # unknown param — skip rather than guess

    with live_app.test_request_context():
        from flask import url_for

        try:
            return url_for(route["endpoint"], **kwargs)
        except Exception:
            return None


# ── Parametrize lists (built at collection time) ───────────────────────────────

_GET_ROUTES = [
    r
    for r in _ALL_ROUTES
    if r["method"] == "GET"
    and r["endpoint"] not in _SKIP_GET_ENDPOINTS
    and r["rule"] not in _SKIP_GET_RULES
]

# Auth-required non-GET routes whose URL was resolvable at generate time
_AUTH_POST_ROUTES = [
    r
    for r in _ALL_ROUTES
    if r["method"] != "GET" and r["auth_required"] and r["url"] is not None
]


# ── GET crawl ──────────────────────────────────────────────────────────────────


class TestGetCrawl:
    """Every GET route must return HTTP 200 with no CSP or JS console errors."""

    @pytest.mark.parametrize("route", _GET_ROUTES, ids=lambda r: r["endpoint"])
    def test_returns_200_and_no_console_errors(
        self, logged_in_page, live_server_url, seed, live_app, route
    ):
        # Docker mode: live_app is None; use the pre-computed URL from routes.json
        # (generated by generate_routes.py against the same DB the container is using).
        url = (
            route.get("url")
            if live_app is None
            else _resolve_url(live_app, seed, route)
        )
        if url is None:
            pytest.skip(f"no resolvable URL for {route['rule']}")

        console_errors: list[str] = []
        server_errors: list[str] = []
        server_host = urllib.parse.urlparse(live_server_url).hostname

        def _on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        def _on_response(resp):
            # Track 4xx/5xx from our own server only — external tile/CDN failures
            # are environment noise and must not cause flaky test failures.
            # Photo img and upload URLs are excluded: their availability depends on
            # whether the dev seed file-copy succeeded in this CI run, and they are
            # covered by TestKnownBehaviors.test_serve_photo_returns_jpeg.
            if resp.status >= 400:
                parsed = urllib.parse.urlparse(resp.url)
                is_file_serving = "/photos/" in parsed.path or parsed.path.startswith(
                    "/uploads/"
                )
                if parsed.hostname == server_host and not is_file_serving:
                    server_errors.append(f"HTTP {resp.status} {resp.url}")

        logged_in_page.on("console", _on_console)
        logged_in_page.on("response", _on_response)
        try:
            resp = logged_in_page.goto(live_server_url + url)
            logged_in_page.wait_for_load_state("networkidle")
        finally:
            logged_in_page.remove_listener("console", _on_console)
            logged_in_page.remove_listener("response", _on_response)

        if resp is None:
            pytest.fail(f"no response received for {url}")

        assert resp.status == 200, f"expected 200, got {resp.status} for {url}"

        csp_errs = [m for m in console_errors if "Content-Security-Policy" in m]
        # Exclude "Failed to load resource" noise — those are tracked precisely via
        # the response listener above (local-server only).  ERR_NETWORK_CHANGED is a
        # transient OS/browser event (interface flap) unrelated to application bugs.
        js_errs = [
            m
            for m in console_errors
            if m not in csp_errs
            and "ERR_NETWORK_CHANGED" not in m
            and "Failed to load resource" not in m
        ]

        if csp_errs:
            pytest.fail("CSP violation on {}:\n{}".format(url, "\n".join(csp_errs)))
        if server_errors:
            pytest.fail(
                "Server error(s) on {}:\n{}".format(url, "\n".join(server_errors))
            )
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
                headers={
                    "Content-Length": "0",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp = conn.getresponse()
            status = resp.status
        finally:
            conn.close()

        assert status != 200, (
            f"{route['method']} {route['url']} returned 200 to an unauthenticated request "
            f"— missing @login_required?"
        )


# ── Known non-standard responses ──────────────────────────────────────────────


class TestKnownBehaviors:
    """Endpoints with non-200 or binary responses verified individually."""

    def test_not_yet_implemented_returns_501(self, logged_in_page, live_server_url):
        resp = logged_in_page.goto(live_server_url + "/not-yet-implemented")
        assert resp.status == 501

    def test_serve_photo_returns_jpeg(self, logged_in_page, live_server_url, seed):
        if seed["photo_id"] is None:
            pytest.skip(
                "no photo in seed DB (dev_seed_docs not copied or /data/uploads not writable)"
            )
        url = f"/aircraft/{seed['ac_flt']}/photos/{seed['photo_id']}/img"
        resp = logged_in_page.request.get(live_server_url + url)
        assert resp.status == 200
        assert resp.headers.get("content-type", "").startswith("image/")

    def test_token_qr_returns_png(self, logged_in_page, live_server_url, seed):
        url = f"/aircraft/{seed['ac_flt']}/share/{seed['token_id']}/qr"
        resp = logged_in_page.request.get(live_server_url + url)
        assert resp.status == 200
        assert resp.headers.get("content-type") == "image/png"


# ── Session-mutating actions — must run last ───────────────────────────────────


class TestEndOfSession:
    """Language change and logout run last; both mutate session state."""

    def test_set_language(self, logged_in_page, live_server_url):
        """Switching locale must redirect to home and persist the language preference."""
        logged_in_page.goto(live_server_url + "/set-language/fr")
        logged_in_page.wait_for_load_state("networkidle")
        assert "/login" not in logged_in_page.url, (
            "set-language redirected to login unexpectedly"
        )
        assert logged_in_page.locator("html").get_attribute("lang") == "fr"

        # Reset to English — set-language saves to the DB, not just the session, so
        # leaving it as 'fr' would contaminate all tests that run after this one.
        logged_in_page.goto(live_server_url + "/set-language/en")
        logged_in_page.wait_for_load_state("networkidle")
        assert logged_in_page.locator("html").get_attribute("lang") == "en"

    def test_logout(self, logged_in_page, live_server_url):
        """Logout must clear the session; subsequent requests redirect to /login."""
        logged_in_page.goto(live_server_url + "/logout")
        logged_in_page.wait_for_load_state("networkidle")
        assert "/logout" not in logged_in_page.url, "logout did not redirect away"

        # /aircraft/ requires auth — must now redirect to /login
        logged_in_page.goto(live_server_url + "/aircraft/")
        logged_in_page.wait_for_load_state("networkidle")
        assert "/login" in logged_in_page.url, "protected page accessible after logout"
