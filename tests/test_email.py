"""
Tests for Phase 14: Email Infrastructure.
Covers email_service.py and the Configuration page email section.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from unittest.mock import MagicMock, patch

import pytest  # pyright: ignore[reportMissingImports]

from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


# ── email_service unit tests ──────────────────────────────────────────────────

class TestEmailService:
    def test_raises_not_configured_when_no_host(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_ADDRESS", raising=False)
        from services.email_service import EmailNotConfiguredError, send_email  # pyright: ignore[reportMissingImports]
        with pytest.raises(EmailNotConfiguredError):
            send_email("to@example.com", "Subject", "Body")

    def test_raises_not_configured_when_no_from_address(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.delenv("SMTP_FROM_ADDRESS", raising=False)
        from services.email_service import EmailNotConfiguredError, send_email  # pyright: ignore[reportMissingImports]
        with pytest.raises(EmailNotConfiguredError):
            send_email("to@example.com", "Subject", "Body")

    def test_sends_with_starttls(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_USE_TLS", "true")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_FROM_NAME", "OpenHangar")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email("to@example.com", "Hello", "Body text")

        mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=10)
        mock_smtp_instance.starttls.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("user@example.com", "secret")
        mock_smtp_instance.sendmail.assert_called_once()
        mock_smtp_instance.quit.assert_called_once()

    def test_sends_without_tls(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "25")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email("to@example.com", "Hello", "Body text")

        mock_smtp_instance.starttls.assert_not_called()
        mock_smtp_instance.login.assert_not_called()

    def test_skips_send_in_demo_mode(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "demo")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")

        mock_smtp = MagicMock()
        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email("to@example.com", "Hello", "Body")

        mock_smtp.assert_not_called()

    def test_raises_send_error_on_smtp_exception(self, monkeypatch):
        import smtplib
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp.return_value.sendmail.side_effect = smtplib.SMTPException("Connection refused")

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            with pytest.raises(email_service.EmailSendError):
                email_service.send_email("to@example.com", "Hello", "Body")

    def test_raises_send_error_on_os_error(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp.side_effect = OSError("Network unreachable")

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            with pytest.raises(email_service.EmailSendError):
                email_service.send_email("to@example.com", "Hello", "Body")

    def test_get_smtp_status_unconfigured(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_ADDRESS", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        from services.email_service import get_smtp_status  # pyright: ignore[reportMissingImports]
        status = get_smtp_status()
        assert status["configured"] is False
        assert status["host"] is None
        assert status["password_set"] is False

    def test_get_smtp_status_configured(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        from services.email_service import get_smtp_status  # pyright: ignore[reportMissingImports]
        status = get_smtp_status()
        assert status["configured"] is True
        assert status["host"] == "smtp.example.com"
        assert status["password_set"] is True

    def test_sends_html_body_when_provided(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_instance = MagicMock()
        mock_smtp.return_value = mock_instance

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email(
                "to@example.com", "Hello", "Plain text", html_body="<p>HTML</p>"
            )

        # sendmail called means the html_body branch was reached and the message sent
        assert mock_instance.sendmail.called
        raw = mock_instance.sendmail.call_args[0][2]
        assert b"text/html" in raw


# ── Configuration page email section ─────────────────────────────────────────

class TestConfigEmailSection:
    def test_config_page_shows_email_section(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert resp.status_code == 200
        assert b"Email" in resp.data
        assert b"SMTP_HOST" in resp.data

    def test_config_page_shows_not_configured(self, app, client, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_ADDRESS", raising=False)
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert b"Not configured" in resp.data

    def test_config_page_shows_configured(self, app, client, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert b"Configured" in resp.data
        assert b"smtp.example.com" in resp.data
        assert b"Send test email" in resp.data

    def test_config_page_masks_password(self, app, client, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "supersecret")
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert b"supersecret" not in resp.data
        assert b"Set" in resp.data


# ── Test-email endpoint ───────────────────────────────────────────────────────

class TestSendTestEmail:
    def test_test_email_not_logged_in(self, app, client):
        resp = client.post("/config/email/test")
        assert resp.status_code == 403

    def test_test_email_stale_user_id_returns_403(self, app, client):
        with client.session_transaction() as sess:
            sess["user_id"] = 99999  # non-existent user
        resp = client.post("/config/email/test")
        assert resp.status_code == 403

    def test_test_email_success(self, app, client, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        _create_user_and_tenant(app)
        _login(app, client)

        mock_smtp = MagicMock()
        mock_smtp.return_value = MagicMock()

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            resp = client.post("/config/email/test", follow_redirects=True)

        assert resp.status_code == 200
        assert b"Test email sent" in resp.data

    def test_test_email_not_configured(self, app, client, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        _create_user_and_tenant(app)
        _login(app, client)

        resp = client.post("/config/email/test", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not configured" in resp.data.lower()

    def test_test_email_smtp_error(self, app, client, monkeypatch):
        import smtplib
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        _create_user_and_tenant(app)
        _login(app, client)

        mock_smtp = MagicMock()
        mock_smtp.return_value.sendmail.side_effect = smtplib.SMTPException("auth failed")

        from services import email_service  # pyright: ignore[reportMissingImports]
        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            resp = client.post("/config/email/test", follow_redirects=True)

        assert resp.status_code == 200
        assert b"send failed" in resp.data.lower()
