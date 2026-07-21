"""Tests for app/security_alerts.py — SecurityAlertHandler."""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest  # pyright: ignore[reportMissingImports]

from security_alerts import (  # pyright: ignore[reportMissingImports]
    SecurityAlertHandler,
    attach_to_logger,
    _ESCALATED,
    _DEBOUNCE_SECONDS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_record(message: str, level: int = logging.WARNING) -> logging.LogRecord:
    record = logging.LogRecord(
        name="openhangar.auth",
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    return record


def _fresh_handler() -> SecurityAlertHandler:
    h = SecurityAlertHandler()
    h._debounce.clear()
    return h


# ── Event filtering ───────────────────────────────────────────────────────────


class TestEventFiltering:
    def test_escalated_event_dispatches(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("[SECURITY] auth.login.account_locked email=x ip=1.2.3.4"))
        assert dispatched == ["auth.login.account_locked"]

    def test_all_escalated_events_dispatch(self, monkeypatch):
        for event in _ESCALATED:
            h = _fresh_handler()
            dispatched = []
            monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))
            h.emit(_make_record(f"[SECURITY] {event} some=detail"))
            assert dispatched == [event], f"{event} did not dispatch"

    def test_totp_disabled_is_escalated(self, monkeypatch):
        # Disabling 2FA is an account-takeover signal (N-14 scenario) and must
        # fire a real-time alert.
        assert "auth.totp.disabled" in _ESCALATED
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("[SECURITY] auth.totp.disabled user_id=7 ip=1.2.3.4"))
        assert dispatched == ["auth.totp.disabled"]

    def test_non_escalated_security_event_does_not_dispatch(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("[SECURITY] auth.credentials.failed email=x ip=1.2.3.4"))
        assert dispatched == []

    def test_non_security_record_does_not_dispatch(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("Normal log line without security tag"))
        assert dispatched == []

    def test_below_warning_does_not_dispatch(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        record = _make_record(
            "[SECURITY] auth.login.account_locked email=x", level=logging.DEBUG
        )
        # Use handle() rather than emit() — level filtering happens in handle()
        h.handle(record)
        assert dispatched == []

    def test_security_tag_embedded_in_word_does_not_dispatch(self, monkeypatch):
        # "[SECURITY]" appears in raw but not as a standalone token
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("PREFIX[SECURITY] auth.login.account_locked"))
        assert dispatched == []

    def test_emit_exception_calls_handle_error(self, monkeypatch):
        h = _fresh_handler()

        def bad_dispatch(et, d):
            raise RuntimeError("boom")

        monkeypatch.setattr(h, "_dispatch", bad_dispatch)
        with patch.object(h, "handleError") as mock_handle_error:
            h.emit(_make_record("[SECURITY] auth.login.account_locked email=x"))
        mock_handle_error.assert_called_once()

    def test_missing_event_type_after_security_tag_does_not_dispatch(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("[SECURITY]"))
        assert dispatched == []


# ── Debounce ──────────────────────────────────────────────────────────────────


class TestDebounce:
    def test_second_identical_event_within_window_suppressed(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        msg = "[SECURITY] auth.login.account_locked email=x ip=1.2.3.4"
        h.emit(_make_record(msg))
        h.emit(_make_record(msg))
        assert len(dispatched) == 1

    def test_event_dispatches_again_after_debounce_window(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        msg = "[SECURITY] auth.login.account_locked email=x ip=1.2.3.4"
        h.emit(_make_record(msg))

        # Simulate debounce window expiry
        h._debounce["auth.login.account_locked"] = (
            time.monotonic() - _DEBOUNCE_SECONDS - 1
        )
        h.emit(_make_record(msg))
        assert len(dispatched) == 2

    def test_different_event_types_debounced_independently(self, monkeypatch):
        h = _fresh_handler()
        dispatched = []
        monkeypatch.setattr(h, "_dispatch", lambda et, d: dispatched.append(et))

        h.emit(_make_record("[SECURITY] auth.login.account_locked email=x ip=1"))
        h.emit(_make_record("[SECURITY] auth.totp.replay user_id=1 ip=2"))
        assert len(dispatched) == 2


# ── Delivery channels ─────────────────────────────────────────────────────────


class TestNtfyChannel:
    def test_ntfy_posts_when_url_set(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", "https://ntfy.sh/test")
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN_FILE", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            h._dispatch("auth.login.account_locked", "detail text")

        mock_open.assert_called_once()
        sent_req = mock_open.call_args[0][0]
        assert "Authorization" not in sent_req.headers

    def test_ntfy_skipped_when_url_absent(self, monkeypatch):
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN_FILE", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen") as mock_open:
            h._dispatch("auth.login.account_locked", "detail")
        mock_open.assert_not_called()

    def test_ntfy_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", "https://ntfy.sh/test")
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN_FILE", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            h._dispatch("auth.login.account_locked", "detail")  # must not raise

    def test_ntfy_sends_bearer_token_when_set(self, monkeypatch):
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_NTFY_TOPIC_URL", "https://ntfy.example.test/topic"
        )
        monkeypatch.setenv("OPENHANGAR_ALERT_NTFY_TOKEN", "tk_abc123")
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN_FILE", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            h._dispatch("auth.login.account_locked", "detail text")

        sent_req = mock_open.call_args[0][0]
        assert sent_req.headers["Authorization"] == "Bearer tk_abc123"

    def test_ntfy_token_from_file(self, monkeypatch, tmp_path):
        token_file = tmp_path / "ntfy_token.txt"
        token_file.write_text("tk_from_file\n")
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_NTFY_TOPIC_URL", "https://ntfy.example.test/topic"
        )
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOKEN", raising=False)
        monkeypatch.setenv("OPENHANGAR_ALERT_NTFY_TOKEN_FILE", str(token_file))
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            h._dispatch("auth.login.account_locked", "detail text")

        sent_req = mock_open.call_args[0][0]
        assert sent_req.headers["Authorization"] == "Bearer tk_from_file"


class TestEmailChannel:
    def test_email_sends_when_configured(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        mock_smtp = MagicMock()
        mock_smtp.return_value = MagicMock()
        import smtplib

        with patch.object(smtplib, "SMTP", mock_smtp):
            h._dispatch("auth.login.account_locked", "detail")

        mock_smtp.assert_called_once()
        mock_smtp.return_value.sendmail.assert_called_once()

    def test_email_skipped_when_smtp_host_absent(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        import smtplib

        with patch.object(smtplib, "SMTP") as mock_smtp:
            h._dispatch("auth.login.account_locked", "detail")
        mock_smtp.assert_not_called()

    def test_email_with_tls_calls_starttls(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "true")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        mock_conn = MagicMock()
        import smtplib

        with patch.object(smtplib, "SMTP", return_value=mock_conn):
            h._dispatch("auth.login.account_locked", "detail")

        mock_conn.starttls.assert_called_once()

    def test_email_with_user_calls_login(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.setenv("OPENHANGAR_SMTP_USER", "user@smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_PASSWORD", "secret")
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        mock_conn = MagicMock()
        import smtplib

        with patch.object(smtplib, "SMTP", return_value=mock_conn):
            h._dispatch("auth.login.account_locked", "detail")

        mock_conn.login.assert_called_once_with("user@smtp.example.com", "secret")

    def test_email_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_FROM_ADDRESS", "no-reply@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_USE_TLS", "false")
        monkeypatch.delenv("OPENHANGAR_SMTP_USER", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_WEBHOOK_URL", raising=False)

        h = _fresh_handler()
        import smtplib

        with patch.object(smtplib, "SMTP", side_effect=OSError("refused")):
            h._dispatch("auth.login.account_locked", "detail")  # must not raise


class TestWebhookChannel:
    def test_webhook_posts_when_url_set(self, monkeypatch):
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_WEBHOOK_URL", "https://hooks.example.com/x"
        )
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            h._dispatch("users.role.changed", "detail")

        mock_open.assert_called_once()

    def test_webhook_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_WEBHOOK_URL", "https://hooks.example.com/x"
        )
        monkeypatch.delenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", raising=False)
        monkeypatch.delenv("OPENHANGAR_ALERT_EMAIL_TO", raising=False)

        h = _fresh_handler()
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            h._dispatch("users.role.changed", "detail")  # must not raise


# ── attach_to_logger idempotency ──────────────────────────────────────────────


class TestAttachToLogger:
    def test_handler_added_to_openhangar_logger(self):
        logger = logging.getLogger("openhangar")
        before = [h for h in logger.handlers if isinstance(h, SecurityAlertHandler)]
        attach_to_logger()
        after = [h for h in logger.handlers if isinstance(h, SecurityAlertHandler)]
        assert len(after) >= 1
        # Clean up any added during this test
        for h in after[len(before) :]:
            logger.removeHandler(h)

    def test_attach_is_idempotent(self):
        logger = logging.getLogger("openhangar")
        # Remove existing handlers to start clean
        for h in list(logger.handlers):
            if isinstance(h, SecurityAlertHandler):
                logger.removeHandler(h)

        attach_to_logger()
        attach_to_logger()
        count = sum(1 for h in logger.handlers if isinstance(h, SecurityAlertHandler))
        assert count == 1
        # Clean up
        for h in list(logger.handlers):
            if isinstance(h, SecurityAlertHandler):
                logger.removeHandler(h)


# ── _validate_config integration ──────────────────────────────────────────────


class TestValidateConfig:
    def _validate(self, app):
        from init import _validate_config  # pyright: ignore[reportMissingImports]

        _validate_config(app)

    def test_valid_ntfy_url_passes(self, app, monkeypatch):
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_NTFY_TOPIC_URL", "https://ntfy.sh/my-topic"
        )
        self._validate(app)  # must not raise

    def test_invalid_ntfy_url_raises(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_NTFY_TOPIC_URL", "ntfy.sh/my-topic")
        with pytest.raises(RuntimeError, match="OPENHANGAR_ALERT_NTFY_TOPIC_URL"):
            self._validate(app)

    def test_valid_alert_email_with_smtp_passes(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        self._validate(app)  # must not raise

    def test_alert_email_without_smtp_raises(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "admin@example.com")
        monkeypatch.delenv("OPENHANGAR_SMTP_HOST", raising=False)
        with pytest.raises(RuntimeError, match="OPENHANGAR_SMTP_HOST"):
            self._validate(app)

    def test_invalid_alert_email_raises(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_EMAIL_TO", "not-an-email")
        monkeypatch.setenv("OPENHANGAR_SMTP_HOST", "smtp.example.com")
        with pytest.raises(RuntimeError, match="OPENHANGAR_ALERT_EMAIL_TO"):
            self._validate(app)

    def test_valid_webhook_url_passes(self, app, monkeypatch):
        monkeypatch.setenv(
            "OPENHANGAR_ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/x"
        )
        self._validate(app)  # must not raise

    def test_invalid_webhook_url_raises(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ALERT_WEBHOOK_URL", "slack.com/webhook")
        with pytest.raises(RuntimeError, match="OPENHANGAR_ALERT_WEBHOOK_URL"):
            self._validate(app)
