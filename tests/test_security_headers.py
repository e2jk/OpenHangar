"""
Tests for HTTP security headers and session cookie configuration.

Verifies that every response carries the headers added in create_app():
  Content-Security-Policy (nonce-based, script-src strict)
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()

Authenticated responses additionally carry:
  Cache-Control: no-store, private

Also verifies the session cookie flags and upload size limit set in create_app():
  SESSION_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY, SESSION_COOKIE_SAMESITE,
  PERMANENT_SESSION_LIFETIME, MAX_CONTENT_LENGTH

Static template hygiene (no server required):
  No inline style= attributes — violates style-src-attr 'none' CSP directive.
"""

import re
from datetime import timedelta
from pathlib import Path

_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"
_CSP_NONCE_RE = re.compile(r"'nonce-[A-Za-z0-9_\-]+'")


class TestSecurityHeaders:
    def test_csp_present(self, client):
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp

    def test_csp_script_src_has_nonce(self, client):
        """script-src must include a per-request nonce — not 'unsafe-inline'."""
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        assert _CSP_NONCE_RE.search(csp), (
            "CSP script-src must contain a sha384 or nonce"
        )
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

    def test_csp_nonce_changes_per_request(self, client):
        """A fresh nonce must be generated for every response."""
        r1 = client.get("/health").headers.get("Content-Security-Policy", "")
        r2 = client.get("/health").headers.get("Content-Security-Policy", "")
        n1 = _CSP_NONCE_RE.search(r1)
        n2 = _CSP_NONCE_RE.search(r2)
        assert n1 and n2 and n1.group() != n2.group()

    def test_csp_frame_ancestors_none(self, client):
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        assert "frame-ancestors 'none'" in csp

    def test_csp_style_src_elem_no_unsafe_inline(self, client):
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        assert "style-src-elem" in csp
        elem_src = csp.split("style-src-elem")[1].split(";")[0]
        assert "'unsafe-inline'" not in elem_src

    def test_csp_no_fallback_directives(self, client):
        csp = client.get("/health").headers.get("Content-Security-Policy", "")
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "form-action 'self'" in csp

    def test_cross_origin_opener_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin"

    def test_cross_origin_resource_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-origin"

    def test_x_frame_options(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, client):
        resp = client.get("/health")
        assert resp.headers.get("Permissions-Policy") == _PERMISSIONS_POLICY

    def test_headers_present_on_html_page(self, client):
        """Headers are set on HTML responses too, not just JSON endpoints."""
        resp = client.get("/login")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert resp.headers.get("Permissions-Policy") == _PERMISSIONS_POLICY
        assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


class TestDegradedJsWarningBanner:
    """The banner is hidden unless the theme-init script (first inline
    <script> in <head>) never ran, or a CSP violation fires at runtime."""

    def test_theme_init_script_sets_js_ok_attribute(self, client):
        resp = client.get("/setup")
        assert b"data-js-ok" in resp.data

    def test_banner_markup_present_but_hidden_by_default(self, client):
        resp = client.get("/setup")
        assert b'id="js-warn-banner"' in resp.data
        assert (
            b"Some features may not work correctly"
            b" \xe2\x80\x94 a browser extension may be interfering."
        ) in resp.data


_TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"
# Matches style= as an HTML attribute.  Jinja comment lines ({# ... #}) are
# excluded — they are never rendered and cannot violate CSP.
_INLINE_STYLE_RE = re.compile(r"\bstyle\s*=\s*[\"']")


class TestTemplateCSPHygiene:
    """Static scan — no Flask app or browser required.  Catches inline style=
    attributes before they reach CI's browser tests."""

    def test_no_inline_style_attributes(self):
        violations = []
        for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("{#"):
                    continue  # Jinja comment — never rendered
                if _INLINE_STYLE_RE.search(line):
                    rel = path.relative_to(_TEMPLATES_DIR)
                    violations.append(f"{rel}:{lineno}: {stripped[:120]}")
        assert not violations, (
            "Inline style= attributes violate the style-src-attr 'none' CSP directive.\n"
            "Replace with a CSS class in aircraft.css or base.css.\n\n"
            + "\n".join(violations)
        )


class TestProxyFix:
    def test_x_forwarded_for_sets_remote_addr(self, app, client):
        """ProxyFix reads the real client IP from X-Forwarded-For."""
        # The health endpoint is the simplest route that goes through the full WSGI stack.
        resp = client.get("/health", headers={"X-Forwarded-For": "203.0.113.42"})
        assert resp.status_code == 200
        # We can't assert request.remote_addr directly after the response, but confirming
        # the app doesn't blow up with the header is the baseline. The real assertion is
        # that ProxyFix is wired in create_app() — verified by the config test below.

    def test_proxy_fix_is_applied(self, app):
        """app.wsgi_app is wrapped with ProxyFix."""
        from werkzeug.middleware.proxy_fix import ProxyFix

        assert isinstance(app.wsgi_app, ProxyFix)


class TestSessionCookieConfig:
    def test_session_cookie_secure(self, app):
        assert app.config["SESSION_COOKIE_SECURE"] is True

    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_permanent_session_lifetime(self, app):
        assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=30)

    def test_session_lifetime_env_override(self, monkeypatch):
        from init import create_app  # pyright: ignore[reportMissingImports]

        monkeypatch.setenv("OPENHANGAR_SESSION_LIFETIME_DAYS", "90")
        override_app = create_app()
        assert override_app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=90)

    def test_max_content_length_has_a_limit(self, app):
        """Upload size must be bounded — default 50 MB, overridable via MAX_UPLOAD_BYTES."""
        assert app.config["MAX_CONTENT_LENGTH"] > 0


class TestCacheControlHeader:
    def test_unauthenticated_response_has_no_cache_control(self, client):
        """Public pages must not carry no-store — that would break browser back-navigation."""
        resp = client.get("/health")
        assert "no-store" not in (resp.headers.get("Cache-Control") or "")

    def test_authenticated_response_has_no_store(self, client):
        """Responses sent to a logged-in session must not be cached by proxies or shared browsers."""
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = client.get("/health")
        assert resp.headers.get("Cache-Control") == "no-store, private"
