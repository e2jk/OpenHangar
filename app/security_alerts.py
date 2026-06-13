"""Real-time alerting for escalated [SECURITY] log events.

Attaches a SecurityAlertHandler to the 'openhangar' logger.  When a WARNING+
record whose message contains '[SECURITY]' matches one of the escalated event
types, it fires up to three delivery channels — ntfy, email, webhook — each
gated by its own env var.  Channels that are not configured are silently skipped.
Delivery failures are logged and never re-raised; alerting must not break the app.

Env vars (all optional):
    OPENHANGAR_ALERT_NTFY_TOPIC_URL  — ntfy topic URL (hosted or self-hosted)
    OPENHANGAR_ALERT_EMAIL_TO        — recipient address for alert emails
    OPENHANGAR_ALERT_WEBHOOK_URL     — generic HTTP POST endpoint (Slack, etc.)

Email alerts reuse the existing OPENHANGAR_SMTP_* env vars.
"""

import json
import logging
import os
import smtplib
import threading
import time
import urllib.error
import urllib.request
from email.mime.text import MIMEText

_ESCALATED: frozenset[str] = frozenset(
    {
        "auth.login.account_locked",
        "auth.login.account_blocked",
        "auth.totp.replay",
        "users.role.changed",
        "users.access.revoked",
    }
)

_DEBOUNCE_SECONDS = 60

_log = logging.getLogger(__name__)

_DEFAULT_FORMATTER = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")


class SecurityAlertHandler(logging.Handler):
    """Logging handler that fires real-time alerts for escalated security events."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.setFormatter(_DEFAULT_FORMATTER)
        self._debounce: dict[str, float] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < self.level:
            return
        try:
            raw = record.getMessage()
            if "[SECURITY]" not in raw:
                return

            parts = raw.split()
            try:
                sec_idx = parts.index("[SECURITY]")
            except ValueError:
                return
            if sec_idx + 1 >= len(parts):
                return

            event_type = parts[sec_idx + 1]
            if event_type not in _ESCALATED:
                return

            now = time.monotonic()
            with self._lock:
                if now - self._debounce.get(event_type, 0.0) < _DEBOUNCE_SECONDS:
                    return
                self._debounce[event_type] = now

            body = self.format(record)
            self._dispatch(event_type, body)
        except Exception:
            self.handleError(record)

    def _dispatch(self, event_type: str, detail: str) -> None:
        ntfy_url = os.environ.get("OPENHANGAR_ALERT_NTFY_TOPIC_URL", "").strip()
        alert_email = os.environ.get("OPENHANGAR_ALERT_EMAIL_TO", "").strip()
        webhook_url = os.environ.get("OPENHANGAR_ALERT_WEBHOOK_URL", "").strip()

        if ntfy_url:
            self._send_ntfy(ntfy_url, event_type, detail)
        if alert_email:
            self._send_email(alert_email, event_type, detail)
        if webhook_url:
            self._send_webhook(webhook_url, event_type, detail)

    def _send_ntfy(self, url: str, event_type: str, detail: str) -> None:
        try:
            req = urllib.request.Request(
                url,
                data=detail.encode("utf-8"),
                headers={
                    "Title": f"OpenHangar security alert: {event_type}",
                    "Priority": "high",
                    "Tags": "warning,lock",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as exc:
            _log.error("Security alert: ntfy delivery failed: %s", exc)

    def _send_email(self, to: str, event_type: str, detail: str) -> None:
        try:
            host = os.environ.get("OPENHANGAR_SMTP_HOST", "").strip()
            from_addr = os.environ.get("OPENHANGAR_SMTP_FROM_ADDRESS", "").strip()
            if not host or not from_addr:
                _log.error(
                    "Security alert: email delivery skipped — "
                    "OPENHANGAR_SMTP_HOST or OPENHANGAR_SMTP_FROM_ADDRESS not configured"
                )
                return

            port = int(os.environ.get("OPENHANGAR_SMTP_PORT", "587"))
            user = os.environ.get("OPENHANGAR_SMTP_USER", "").strip()
            password = os.environ.get("OPENHANGAR_SMTP_PASSWORD", "")
            use_tls = os.environ.get("OPENHANGAR_SMTP_USE_TLS", "true").lower() not in (
                "false",
                "0",
                "no",
            )

            msg = MIMEText(detail, "plain", "utf-8")
            msg["Subject"] = f"[OpenHangar] Security alert: {event_type}"
            msg["From"] = from_addr
            msg["To"] = to

            conn = smtplib.SMTP(host, port, timeout=10)
            if use_tls:
                conn.ehlo()
                conn.starttls()
                conn.ehlo()
            if user:
                conn.login(user, password)
            conn.sendmail(from_addr, [to], msg.as_bytes())
            conn.quit()
        except Exception as exc:
            _log.error("Security alert: email delivery failed: %s", exc)

    def _send_webhook(self, url: str, event_type: str, detail: str) -> None:
        try:
            payload = json.dumps({"event": event_type, "detail": detail}).encode(
                "utf-8"
            )
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as exc:
            _log.error("Security alert: webhook delivery failed: %s", exc)


def attach_to_logger() -> None:
    """Attach the SecurityAlertHandler to the openhangar logger.  Idempotent."""
    logger = logging.getLogger("openhangar")
    if not any(isinstance(h, SecurityAlertHandler) for h in logger.handlers):
        logger.addHandler(SecurityAlertHandler())
