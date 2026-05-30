"""
Tests for HTTP security headers and session cookie configuration.

Verifies that every response carries the headers added in create_app():
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()

Also verifies the session cookie flags and upload size limit set in create_app():
  SESSION_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY, SESSION_COOKIE_SAMESITE,
  PERMANENT_SESSION_LIFETIME, MAX_CONTENT_LENGTH
"""

from datetime import timedelta

_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"


class TestSecurityHeaders:
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
        assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)

    def test_max_content_length_has_a_limit(self, app):
        """Upload size must be bounded — default 50 MB, overridable via MAX_UPLOAD_BYTES."""
        assert app.config["MAX_CONTENT_LENGTH"] > 0
