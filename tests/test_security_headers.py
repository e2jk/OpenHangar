"""
Tests for HTTP security headers and session cookie configuration.

Verifies that every response carries the three headers added in create_app():
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin

Also verifies the four session cookie flags set explicitly in create_app():
  SESSION_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY, SESSION_COOKIE_SAMESITE, PERMANENT_SESSION_LIFETIME
"""

from datetime import timedelta


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

    def test_headers_present_on_html_page(self, client):
        """Headers are set on HTML responses too, not just JSON endpoints."""
        resp = client.get("/login")
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


class TestSessionCookieConfig:
    def test_session_cookie_secure(self, app):
        assert app.config["SESSION_COOKIE_SECURE"] is True

    def test_session_cookie_httponly(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, app):
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_permanent_session_lifetime(self, app):
        assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)
