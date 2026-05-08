"""
Outbound email service.

Configuration is read entirely from environment variables so operators
manage it via their Docker Compose / .env file — no DB row needed.

Required env vars (if SMTP_HOST is unset, all sends are skipped):
  SMTP_HOST          — e.g. smtp.example.com
  SMTP_PORT          — default 587
  SMTP_USER          — SMTP login username
  SMTP_PASSWORD      — SMTP login password
  SMTP_USE_TLS       — "true" (default) uses STARTTLS; "false" for plain SMTP
  SMTP_FROM_ADDRESS  — e.g. no-reply@example.com
  SMTP_FROM_NAME     — display name, e.g. "OpenHangar"

Demo mode (FLASK_ENV=demo): all sends are silently skipped.
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class EmailNotConfiguredError(Exception):
    """Raised when SMTP_HOST is not set."""


class EmailSendError(Exception):
    """Raised when the SMTP transaction fails."""


def _smtp_settings() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no"),
        "from_address": os.environ.get("SMTP_FROM_ADDRESS", "").strip(),
        "from_name": os.environ.get("SMTP_FROM_NAME", "OpenHangar").strip(),
    }


def get_smtp_status() -> dict:
    """
    Return a dict describing the current SMTP configuration for display in
    the Configuration UI.  Passwords are never included.
    Each value is the env var's value if explicitly set, or None if absent
    (so the UI can distinguish "not set" from a default).
    """
    def _env(key: str) -> str | None:
        v = os.environ.get(key, "").strip()
        return v or None

    host = _env("SMTP_HOST")
    from_address = _env("SMTP_FROM_ADDRESS")
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "port_is_default": "SMTP_PORT" not in os.environ,
        "user": _env("SMTP_USER"),
        "password_set": bool(os.environ.get("SMTP_PASSWORD", "").strip()),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() not in ("false", "0", "no"),
        "use_tls_is_default": "SMTP_USE_TLS" not in os.environ,
        "from_address": from_address,
        "from_name": _env("SMTP_FROM_NAME"),
        "configured": bool(host and from_address),
    }


def send_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    """
    Send an email.

    Raises EmailNotConfiguredError if SMTP_HOST is unset.
    Raises EmailSendError on SMTP failure.
    Silently does nothing in demo mode.
    """
    if os.environ.get("FLASK_ENV") == "demo":
        return

    s = _smtp_settings()
    if not s["host"]:
        raise EmailNotConfiguredError("SMTP_HOST is not configured.")
    if not s["from_address"]:
        raise EmailNotConfiguredError("SMTP_FROM_ADDRESS is not configured.")

    from_header = (
        f"{s['from_name']} <{s['from_address']}>"
        if s["from_name"]
        else s["from_address"]
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if s["use_tls"]:
            conn = smtplib.SMTP(s["host"], s["port"], timeout=10)
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
        else:
            conn = smtplib.SMTP(s["host"], s["port"], timeout=10)

        if s["user"]:
            conn.login(s["user"], s["password"])

        conn.sendmail(s["from_address"], [to], msg.as_bytes())
        conn.quit()
    except smtplib.SMTPException as exc:
        raise EmailSendError(str(exc)) from exc
    except OSError as exc:
        raise EmailSendError(str(exc)) from exc
