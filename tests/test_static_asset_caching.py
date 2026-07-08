"""
Tests for static asset caching.

The global after_request hook stamps `Cache-Control: no-store, private` on
authenticated responses, which used to downgrade Flask's static files (served
with werkzeug's default `no-cache`) to fully uncacheable.  Static assets are
now exempted: they get a long `public, max-age, immutable` lifetime, made safe
by a `?v=<version>` cache-buster appended to every generated static URL.
"""

import os

from flask import url_for  # pyright: ignore[reportMissingImports]

from init import (  # pyright: ignore[reportMissingImports]
    _static_cache_version,
    _static_folder_mtime_token,
    create_app,
)

_STATIC_CC = "public, max-age=31536000, immutable"


class TestStaticCacheControl:
    def test_static_asset_cacheable_when_logged_in(self, client):
        """Static assets must not inherit the authenticated no-store stamp."""
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = client.get("/static/css/base.css")
        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == _STATIC_CC

    def test_static_asset_cacheable_when_anonymous(self, client):
        resp = client.get("/static/css/base.css")
        assert resp.status_code == 200
        assert resp.headers.get("Cache-Control") == _STATIC_CC

    def test_static_304_keeps_long_lifetime(self, client):
        """Conditional revalidation responses stay cacheable too."""
        first = client.get("/static/css/base.css")
        etag = first.headers.get("ETag")
        assert etag
        resp = client.get("/static/css/base.css", headers={"If-None-Match": etag})
        assert resp.status_code == 304
        assert resp.headers.get("Cache-Control") == _STATIC_CC

    def test_missing_static_file_not_marked_immutable(self, client):
        """A 404 under /static/ must never be cached for a year."""
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = client.get("/static/css/does-not-exist.css")
        assert resp.status_code == 404
        assert "immutable" not in (resp.headers.get("Cache-Control") or "")

    def test_authenticated_page_still_no_store(self, client):
        """The exemption is static-only — app responses keep no-store."""
        with client.session_transaction() as sess:
            sess["user_id"] = 1
        resp = client.get("/health")
        assert resp.headers.get("Cache-Control") == "no-store, private"


class TestStaticUrlVersioning:
    def test_url_for_static_appends_version(self, app):
        with app.test_request_context():
            url = url_for("static", filename="css/base.css")
        assert "?v=" in url

    def test_explicit_version_wins(self, app):
        with app.test_request_context():
            url = url_for("static", filename="css/base.css", v="custom")
        assert url.endswith("?v=custom")

    def test_rendered_page_uses_versioned_static_urls(self, client):
        html = client.get("/login", follow_redirects=True).get_data(as_text=True)
        assert "css/base.css?v=" in html

    def test_version_from_release_env(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_VERSION", "9.9.9-test")
        release_app = create_app()
        with release_app.test_request_context():
            url = url_for("static", filename="css/base.css")
        assert url.endswith("?v=9.9.9-test")

    def test_version_falls_back_to_mtime_in_development(self, monkeypatch, app):
        """OPENHANGAR_VERSION=development must not produce a constant token
        that would serve stale assets across dev code changes."""
        monkeypatch.setenv("OPENHANGAR_VERSION", "development")
        token = _static_cache_version(app.static_folder or "static")
        assert token.isdigit() and int(token) > 0

    def test_mtime_token_skips_vanished_files(self, tmp_path):
        """A broken symlink (file removed mid-walk) must not abort the scan."""
        real = tmp_path / "asset.css"
        real.write_text("body{}")
        os.symlink(tmp_path / "gone.css", tmp_path / "dead.css")
        token = _static_folder_mtime_token(str(tmp_path))
        assert token == str(int(real.stat().st_mtime))
