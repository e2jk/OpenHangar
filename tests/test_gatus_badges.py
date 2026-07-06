"""Tests for the Gatus monitoring badge proxy (config/routes.py)."""

import urllib.error
from unittest.mock import MagicMock, patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


def _setup_admin(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="admin@gatus.test",
            password_hash=_pw_hash.hash("pw"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id


def _login(app, client):
    uid = _setup_admin(app)
    with client.session_transaction() as sess:
        sess["user_id"] = uid


_VALID_URL = "https://uptime.example.com/endpoints/openhangar_openhangar-production"
_VALID_AUTH = "dXNlcjpwYXNzd29yZA=="


class TestParseGatusEnv:
    def test_returns_none_when_env_not_set(self, app):
        from config.routes import _parse_gatus_env  # pyright: ignore[reportMissingImports]

        with app.app_context():
            with patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("OPENHANGAR_GATUS_ENDPOINT_URL", None)
                result = _parse_gatus_env()
        assert result is None

    def test_returns_none_when_url_missing_endpoints_segment(self, app):
        from config.routes import _parse_gatus_env  # pyright: ignore[reportMissingImports]

        with app.app_context():
            with patch.dict(
                "os.environ",
                {
                    "OPENHANGAR_GATUS_ENDPOINT_URL": "https://uptime.example.com/openhangar"
                },
            ):
                result = _parse_gatus_env()
        assert result is None

    def test_returns_none_when_url_has_no_base_before_endpoints(self, app):
        from config.routes import _parse_gatus_env  # pyright: ignore[reportMissingImports]

        with app.app_context():
            with patch.dict(
                "os.environ",
                {
                    "OPENHANGAR_GATUS_ENDPOINT_URL": "/endpoints/openhangar_openhangar-production"
                },
            ):
                result = _parse_gatus_env()
        assert result is None

    def test_parses_valid_url_without_auth(self, app):
        from config.routes import _parse_gatus_env  # pyright: ignore[reportMissingImports]

        with app.app_context():
            with patch.dict(
                "os.environ", {"OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL}, clear=False
            ):
                import os

                os.environ.pop("OPENHANGAR_GATUS_AUTH_HEADER", None)
                result = _parse_gatus_env()
        assert result is not None
        base_url, endpoint_key, auth_header = result
        assert base_url == "https://uptime.example.com"
        assert endpoint_key == "openhangar_openhangar-production"
        assert auth_header is None

    def test_parses_valid_url_with_auth(self, app):
        from config.routes import _parse_gatus_env  # pyright: ignore[reportMissingImports]

        with app.app_context():
            with patch.dict(
                "os.environ",
                {
                    "OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL,
                    "OPENHANGAR_GATUS_AUTH_HEADER": _VALID_AUTH,
                },
            ):
                result = _parse_gatus_env()
        assert result is not None
        _, _, auth_header = result
        assert auth_header == _VALID_AUTH


class TestGatusBadgeRoute:
    def test_invalid_badge_path_returns_404(self, app, client):
        _login(app, client)
        resp = client.get("/config/gatus-badge/invalid/path.svg")
        assert resp.status_code == 404

    def test_gatus_not_configured_returns_404(self, app, client):
        _login(app, client)
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("OPENHANGAR_GATUS_ENDPOINT_URL", None)
            resp = client.get("/config/gatus-badge/uptimes/24h/badge.svg")
        assert resp.status_code == 404

    def test_successful_fetch_returns_svg(self, app, client):
        _login(app, client)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<svg/>"
        mock_resp.headers.get.return_value = "image/svg+xml"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.dict("os.environ", {"OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL}):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                resp = client.get("/config/gatus-badge/uptimes/24h/badge.svg")
        assert resp.status_code == 200
        assert resp.data == b"<svg/>"

    def test_auth_header_sent_when_configured(self, app, client):
        _login(app, client)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<svg/>"
        mock_resp.headers.get.return_value = "image/svg+xml"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        captured = []

        def capture_urlopen(req, timeout):
            captured.append(req)
            return mock_resp

        with patch.dict(
            "os.environ",
            {
                "OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL,
                "OPENHANGAR_GATUS_AUTH_HEADER": _VALID_AUTH,
            },
        ):
            with patch("urllib.request.urlopen", side_effect=capture_urlopen):
                client.get("/config/gatus-badge/uptimes/24h/badge.svg")

        assert len(captured) == 1
        assert captured[0].get_header("Authorization") == f"Basic {_VALID_AUTH}"

    def test_no_auth_header_when_not_configured(self, app, client):
        _login(app, client)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<svg/>"
        mock_resp.headers.get.return_value = "image/svg+xml"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        captured = []

        def capture_urlopen(req, timeout):
            captured.append(req)
            return mock_resp

        with patch.dict(
            "os.environ", {"OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL}, clear=False
        ):
            import os

            os.environ.pop("OPENHANGAR_GATUS_AUTH_HEADER", None)
            with patch("urllib.request.urlopen", side_effect=capture_urlopen):
                client.get("/config/gatus-badge/uptimes/24h/badge.svg")

        assert len(captured) == 1
        assert captured[0].get_header("Authorization") is None

    def test_url_error_returns_503(self, app, client):
        _login(app, client)
        with patch.dict("os.environ", {"OPENHANGAR_GATUS_ENDPOINT_URL": _VALID_URL}):
            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("unreachable"),
            ):
                resp = client.get("/config/gatus-badge/uptimes/24h/badge.svg")
        assert resp.status_code == 503
