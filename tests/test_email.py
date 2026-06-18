"""
Tests for Email Infrastructure.
Covers email_service.py, the Configuration page email section, and aviation quotes.
"""

import email as _email_lib
import os

import bcrypt  # pyright: ignore[reportMissingImports]
from unittest.mock import MagicMock, patch

import pytest  # pyright: ignore[reportMissingImports]

from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]
from quotes import (  # pyright: ignore[reportMissingImports]
    random_aviation_quote,
    _QUOTES_EN,
    _QUOTES_FR,
    _QUOTES_NL,
)


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
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
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
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        monkeypatch.delenv("OPENHANGAR_SMTP_FROM_ADDRESS", raising=False)
        from services.email_service import EmailNotConfiguredError, send_email  # pyright: ignore[reportMissingImports]

        with pytest.raises(EmailNotConfiguredError):
            send_email("to@example.com", "Subject", "Body")

    def test_raises_not_configured_when_no_from_address(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.delenv("OPENHANGAR_SMTP_FROM_ADDRESS", raising=False)
        from services.email_service import EmailNotConfiguredError, send_email  # pyright: ignore[reportMissingImports]

        with pytest.raises(EmailNotConfiguredError):
            send_email("to@example.com", "Subject", "Body")

    def test_sends_with_starttls(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "587")
        monkeypatch.setenv("OPENHANGAR_SMTP_USER", "user@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PASSWORD", "secret")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "true")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_NAME", "OpenHangar")
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

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

    def test_sends_with_smtp_ssl_on_port_465(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "465")
        monkeypatch.setenv("OPENHANGAR_SMTP_USER", "user@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PASSWORD", "secret")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "true")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        mock_smtp_ssl = MagicMock()
        mock_instance = MagicMock()
        mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_smtp_ssl.return_value = mock_instance

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP_SSL", mock_smtp_ssl):
            with patch.object(email_service.smtplib, "SMTP") as mock_smtp:
                email_service.send_email("to@example.com", "Hello", "Body text")

        mock_smtp_ssl.assert_called_once_with("mail.example.com", 465, timeout=10)
        mock_smtp.assert_not_called()
        mock_instance.starttls.assert_not_called()
        mock_instance.login.assert_called_once_with("user@example.com", "secret")
        mock_instance.sendmail.assert_called_once()

    def test_sends_without_tls(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PORT", "25")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp_instance = MagicMock()
        mock_smtp.return_value = mock_smtp_instance

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email("to@example.com", "Hello", "Body text")

        mock_smtp_instance.starttls.assert_not_called()
        mock_smtp_instance.login.assert_not_called()

    def test_skips_send_in_demo_mode(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "demo")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")

        mock_smtp = MagicMock()
        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            email_service.send_email("to@example.com", "Hello", "Body")

        mock_smtp.assert_not_called()

    def test_raises_send_error_on_smtp_exception(self, monkeypatch):
        import smtplib

        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp.return_value.sendmail.side_effect = smtplib.SMTPException(
            "Connection refused"
        )

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            with pytest.raises(email_service.EmailSendError):
                email_service.send_email("to@example.com", "Hello", "Body")

    def test_raises_send_error_on_os_error(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp.side_effect = OSError("Network unreachable")

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            with pytest.raises(email_service.EmailSendError):
                email_service.send_email("to@example.com", "Hello", "Body")

    def test_os_error_during_sendmail_logs_and_raises(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        mock_smtp = MagicMock()
        mock_smtp.return_value.sendmail.side_effect = OSError(
            "Connection reset by peer"
        )

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            with pytest.raises(email_service.EmailSendError):
                email_service.send_email("to@example.com", "Hello", "Body")

    def test_get_smtp_status_unconfigured(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        monkeypatch.delenv("OPENHANGAR_SMTP_FROM_ADDRESS", raising=False)
        monkeypatch.delenv("OPENHANGAR_SMTP_PASSWORD", raising=False)
        from services.email_service import get_smtp_status  # pyright: ignore[reportMissingImports]

        status = get_smtp_status()
        assert status["configured"] is False
        assert status["host"] is None
        assert status["password_set"] is False

    def test_get_smtp_status_configured(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PASSWORD", "secret")
        from services.email_service import get_smtp_status  # pyright: ignore[reportMissingImports]

        status = get_smtp_status()
        assert status["configured"] is True
        assert status["host"] == "smtp.example.com"
        assert status["password_set"] is True

    def test_sends_html_body_when_provided(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

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
        assert b"OPENHANGAR_SMTP_HOST" in resp.data

    def test_config_page_shows_not_configured(self, app, client, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        monkeypatch.delenv("OPENHANGAR_SMTP_FROM_ADDRESS", raising=False)
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert b"Not configured" in resp.data

    def test_config_page_shows_configured(self, app, client, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/config/")
        assert b"Configured" in resp.data
        assert b"smtp.example.com" in resp.data
        assert b"Send test email" in resp.data

    def test_config_page_masks_password(self, app, client, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PASSWORD", "supersecret")
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
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

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
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        _create_user_and_tenant(app)
        _login(app, client)

        resp = client.post("/config/email/test", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not configured" in resp.data.lower()

    def test_test_email_smtp_error(self, app, client, monkeypatch):
        import smtplib

        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ENV", raising=False)

        _create_user_and_tenant(app)
        _login(app, client)

        mock_smtp = MagicMock()
        mock_smtp.return_value.sendmail.side_effect = smtplib.SMTPException(
            "auth failed"
        )

        from services import email_service  # pyright: ignore[reportMissingImports]

        with patch.object(email_service.smtplib, "SMTP", mock_smtp):
            resp = client.post("/config/email/test", follow_redirects=True)

        assert resp.status_code == 200
        assert b"send failed" in resp.data.lower()


# ── Aviation quotes unit tests ────────────────────────────────────────────────


class TestRandomAviationQuote:
    def test_returns_string(self):
        result = random_aviation_quote("en")
        assert isinstance(result, str)
        assert len(result) > 10

    def test_contains_attribution_separator(self):
        result = random_aviation_quote("en")
        assert " — " in result

    def test_en_locale_uses_english_pool_only(self):
        fr_texts = {t for t, _ in _QUOTES_FR}
        nl_texts = {t for t, _ in _QUOTES_NL}
        for _ in range(50):
            result = random_aviation_quote("en")
            for fr_text in fr_texts:
                assert fr_text not in result
            for nl_text in nl_texts:
                assert nl_text not in result

    def test_fr_locale_may_return_french_quote(self):
        assert len(_QUOTES_FR) > 0
        fr_texts = {t for t, _ in _QUOTES_FR}
        found_fr = False
        for _ in range(200):
            result = random_aviation_quote("fr")
            for fr_text in fr_texts:
                if fr_text in result:
                    found_fr = True
                    break
            if found_fr:
                break
        assert found_fr, "French locale never returned a French quote in 200 tries"

    def test_nl_locale_may_return_dutch_quote(self):
        assert len(_QUOTES_NL) > 0
        nl_texts = {t for t, _ in _QUOTES_NL}
        found_nl = False
        for _ in range(200):
            result = random_aviation_quote("nl")
            for nl_text in nl_texts:
                if nl_text in result:
                    found_nl = True
                    break
            if found_nl:
                break
        assert found_nl, "Dutch locale never returned a Dutch quote in 200 tries"

    def test_unknown_locale_falls_back_to_english(self):
        fr_texts = {t for t, _ in _QUOTES_FR}
        nl_texts = {t for t, _ in _QUOTES_NL}
        for _ in range(50):
            result = random_aviation_quote("xx")
            for text in fr_texts | nl_texts:
                assert text not in result

    def test_none_locale_falls_back_to_english(self):
        result = random_aviation_quote(None)  # type: ignore[arg-type]
        assert isinstance(result, str)
        assert " — " in result

    def test_fr_locale_does_not_include_dutch(self):
        nl_texts = {t for t, _ in _QUOTES_NL}
        for _ in range(50):
            result = random_aviation_quote("fr")
            for nl_text in nl_texts:
                assert nl_text not in result

    def test_nl_locale_does_not_include_french(self):
        fr_texts = {t for t, _ in _QUOTES_FR}
        for _ in range(50):
            result = random_aviation_quote("nl")
            for fr_text in fr_texts:
                assert fr_text not in result

    def test_pool_sizes(self):
        assert len(_QUOTES_EN) >= 10
        assert len(_QUOTES_FR) >= 4
        assert len(_QUOTES_NL) >= 3

    def test_no_duplicate_quote_texts(self):
        all_texts = [t for t, _ in _QUOTES_EN + _QUOTES_FR + _QUOTES_NL]
        assert len(all_texts) == len(set(all_texts)), "Duplicate quote texts found"


# ── send_email quote injection tests ─────────────────────────────────────────


class TestSendEmailQuoteInjection:
    _SMTP_ENV = {
        "OPENHANGAR_SMTP_HOST": "smtp.example.com",
        "OPENHANGAR_SMTP_PORT": "587",
        "OPENHANGAR_SMTP_USER": "user",
        "OPENHANGAR_SMTP_PASSWORD": "pw",
        "OPENHANGAR_SMTP_FROM_ADDRESS": "noreply@example.com",
        "OPENHANGAR_SMTP_FROM_NAME": "Test",
        "OPENHANGAR_SMTP_USE_TLS": "false",
        "OPENHANGAR_ENV": "test",
    }
    _FIXED_QUOTE = "“Fly safely.” — Test Pilot"

    def _get_parts(self, html_body=None, locale="en"):
        """Send an email and return (text_body_str, html_body_str | None)."""
        from services.email_service import send_email  # pyright: ignore[reportMissingImports]

        with (
            patch.dict(os.environ, self._SMTP_ENV, clear=True),
            patch("smtplib.SMTP") as mock_smtp,
            patch("services.email_service._record_health"),
            patch("quotes.random_aviation_quote", return_value=self._FIXED_QUOTE),
        ):
            mock_conn = MagicMock()
            mock_smtp.return_value = mock_conn
            send_email(
                to="pilot@example.com",
                subject="Test",
                text_body="Hello pilot.",
                html_body=html_body,
                locale=locale,
            )
            raw_bytes: bytes = mock_conn.sendmail.call_args[0][2]

        msg = _email_lib.message_from_bytes(raw_bytes)
        text_part = None
        html_part = None
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                text_part = part.get_payload(decode=True).decode("utf-8")
            elif ct == "text/html":
                html_part = part.get_payload(decode=True).decode("utf-8")
        return text_part, html_part

    def test_quote_appended_to_text_body(self):
        text, _ = self._get_parts()
        assert self._FIXED_QUOTE in text

    def test_quote_separator_in_text_body(self):
        text, _ = self._get_parts()
        assert "\n\n—\n" in text

    def test_original_text_preserved(self):
        text, _ = self._get_parts()
        assert text.startswith("Hello pilot.")

    def test_quote_injected_into_html_placeholder(self):
        html_with_placeholder = (
            "<html><body><div class='footer'><!-- QUOTE_PLACEHOLDER -->"
            "Footer text.</div></body></html>"
        )
        _, html = self._get_parts(html_body=html_with_placeholder)
        assert html is not None
        assert "Fly safely" in html
        assert "QUOTE_PLACEHOLDER" not in html

    def test_html_placeholder_replaced_with_styled_tag(self):
        html_with_placeholder = (
            "<html><body><div><!-- QUOTE_PLACEHOLDER -->text</div></body></html>"
        )
        _, html = self._get_parts(html_body=html_with_placeholder)
        assert html is not None
        assert "<p style=" in html
        assert "font-style:italic" in html

    def test_html_without_placeholder_unchanged(self):
        html_no_placeholder = "<html><body><p>content</p></body></html>"
        text, html = self._get_parts(html_body=html_no_placeholder)
        assert self._FIXED_QUOTE in text
        assert html is not None
        assert "QUOTE_PLACEHOLDER" not in html
        assert "<p>content</p>" in html

    def test_locale_passed_to_random_aviation_quote(self):
        from services.email_service import send_email  # pyright: ignore[reportMissingImports]

        with (
            patch.dict(os.environ, self._SMTP_ENV, clear=True),
            patch("smtplib.SMTP") as mock_smtp,
            patch("services.email_service._record_health"),
            patch("quotes.random_aviation_quote", return_value="x — y") as mock_quote,
        ):
            mock_smtp.return_value = MagicMock()
            send_email(to="pilot@example.com", subject="S", text_body="B", locale="fr")
        mock_quote.assert_called_once_with("fr")

    def test_default_locale_is_en(self):
        from services.email_service import send_email  # pyright: ignore[reportMissingImports]

        with (
            patch.dict(os.environ, self._SMTP_ENV, clear=True),
            patch("smtplib.SMTP") as mock_smtp,
            patch("services.email_service._record_health"),
            patch("quotes.random_aviation_quote", return_value="x — y") as mock_quote,
        ):
            mock_smtp.return_value = MagicMock()
            send_email(to="pilot@example.com", subject="S", text_body="B")
        mock_quote.assert_called_once_with("en")
