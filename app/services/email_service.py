"""
Outbound email service.

Configuration is read entirely from environment variables so operators
manage it via their Docker Compose / .env file — no DB row needed.

Required env vars (if OPENHANGAR_SMTP_HOST is unset, all sends are skipped):
  OPENHANGAR_SMTP_HOST      — e.g. smtp.example.com
  OPENHANGAR_SMTP_PORT      — default 587
  OPENHANGAR_SMTP_USER      — SMTP login username
  OPENHANGAR_SMTP_PASSWORD  — SMTP login password
  OPENHANGAR_SMTP_USE_TLS   — "true" (default) uses STARTTLS; "false" for plain SMTP
  OPENHANGAR_SMTP_FROM_ADDRESS— e.g. no-reply@example.com
  OPENHANGAR_SMTP_FROM_NAME — display name, e.g. "OpenHangar"

Demo mode (OPENHANGAR_ENV=demo): all sends are silently skipped.
"""

import html as _html
import logging
import os
import smtplib
from datetime import datetime, timezone
from typing import Any
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


class EmailNotConfiguredError(Exception):
    """Raised when OPENHANGAR_SMTP_HOST is not set."""


class EmailSendError(Exception):
    """Raised when the SMTP transaction fails."""


def _smtp_settings() -> dict[str, Any]:
    return {
        "host": os.environ.get("OPENHANGAR_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("OPENHANGAR_SMTP_PORT", "587")),
        "user": os.environ.get("OPENHANGAR_SMTP_USER", "").strip(),
        "password": os.environ.get("OPENHANGAR_SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("OPENHANGAR_SMTP_USE_TLS", "true").lower()
        not in ("false", "0", "no"),
        "from_address": os.environ.get("OPENHANGAR_SMTP_FROM_ADDRESS", "").strip(),
        "from_name": os.environ.get("OPENHANGAR_SMTP_FROM_NAME", "OpenHangar").strip(),
    }


def get_smtp_status() -> dict[str, Any]:
    """
    Return a dict describing the current SMTP configuration for display in
    the Configuration UI.  Passwords are never included.
    Each value is the env var's value if explicitly set, or None if absent
    (so the UI can distinguish "not set" from a default).
    """

    def _env(key: str) -> str | None:
        v = os.environ.get(key, "").strip()
        return v or None

    host = _env("OPENHANGAR_SMTP_HOST")
    from_address = _env("OPENHANGAR_SMTP_FROM_ADDRESS")
    return {
        "host": host,
        "port": int(os.environ.get("OPENHANGAR_SMTP_PORT", "587")),
        "port_is_default": "OPENHANGAR_SMTP_PORT" not in os.environ,
        "user": _env("OPENHANGAR_SMTP_USER"),
        "password_set": bool(os.environ.get("OPENHANGAR_SMTP_PASSWORD", "").strip()),
        "use_tls": os.environ.get("OPENHANGAR_SMTP_USE_TLS", "true").lower()
        not in ("false", "0", "no"),
        "use_tls_is_default": "OPENHANGAR_SMTP_USE_TLS" not in os.environ,
        "from_address": from_address,
        "from_name": _env("OPENHANGAR_SMTP_FROM_NAME"),
        "configured": bool(host and from_address),
    }


def _record_health(success: bool) -> None:
    """Update email delivery health counters in AppSetting. Silently no-ops outside app context."""
    try:
        from flask import has_app_context  # pyright: ignore[reportMissingImports]

        if not has_app_context():
            return
        from models import AppSetting, db  # pyright: ignore[reportMissingImports]

        if success:
            now = datetime.now(timezone.utc).isoformat()
            for key, val in [
                ("email_last_success_at", now),
                ("email_consecutive_failures", "0"),
            ]:
                s = db.session.get(AppSetting, key)
                if s:
                    s.value = val
                else:
                    db.session.add(AppSetting(key=key, value=val))
        else:
            s = db.session.get(AppSetting, "email_consecutive_failures")
            count = (int(s.value) + 1) if s and s.value else 1
            if s:
                s.value = str(count)
            else:
                db.session.add(
                    AppSetting(key="email_consecutive_failures", value=str(count))
                )
        db.session.commit()
    except Exception as exc:
        log.debug("email health tracking failed (non-fatal): %s", exc)


def get_email_health() -> dict[str, Any]:
    """Return email delivery health dict. Must be called within an app context."""
    if not os.environ.get("OPENHANGAR_SMTP_HOST", "").strip():
        return {
            "status": "unconfigured",
            "consecutive_failures": 0,
            "last_success_at": None,
        }
    try:
        from models import AppSetting, db  # pyright: ignore[reportMissingImports]

        failures_row = db.session.get(AppSetting, "email_consecutive_failures")
        success_row = db.session.get(AppSetting, "email_last_success_at")
        consecutive_failures = (
            int(failures_row.value) if failures_row and failures_row.value else 0
        )
        last_success_at = success_row.value if success_row else None

        if consecutive_failures == 0:
            status = "ok"
        elif last_success_at:
            status = "degraded"
        else:
            status = "never_worked"

        return {
            "status": status,
            "consecutive_failures": consecutive_failures,
            "last_success_at": last_success_at,
        }
    except Exception:
        return {"status": "ok", "consecutive_failures": 0, "last_success_at": None}


_QUOTE_PLACEHOLDER = "<!-- QUOTE_PLACEHOLDER -->"


def send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    locale: str = "en",
) -> None:
    """
    Send an email.

    Raises EmailNotConfiguredError if SMTP_HOST is unset.
    Raises EmailSendError on SMTP failure.
    Silently does nothing in demo mode.

    A randomly chosen aviation quote (locale-aware) is appended to the plain-text
    body and injected into the HTML body at the <!-- QUOTE_PLACEHOLDER --> anchor.
    """
    if os.environ.get("OPENHANGAR_ENV") == "demo":
        return

    s = _smtp_settings()
    if not s["host"]:
        raise EmailNotConfiguredError("SMTP_HOST is not configured.")
    if not s["from_address"]:
        raise EmailNotConfiguredError("SMTP_FROM_ADDRESS is not configured.")

    from quotes import random_aviation_quote  # pyright: ignore[reportMissingImports]

    quote = random_aviation_quote(locale)
    text_body = text_body + f"\n\n—\n{quote}"
    if html_body and _QUOTE_PLACEHOLDER in html_body:
        quote_html = (
            f'<p style="font-style:italic;color:#9ca3af;'
            f'margin:12px 0 0;font-size:11px;">{_html.escape(quote)}</p>'
        )
        html_body = html_body.replace(_QUOTE_PLACEHOLDER, quote_html)

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
        conn: smtplib.SMTP
        if s["use_tls"] and s["port"] == 465:
            # Port 465 = implicit SSL (SMTPS) — must use SMTP_SSL, not STARTTLS
            conn = smtplib.SMTP_SSL(s["host"], s["port"], timeout=10)
        elif s["use_tls"]:
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
        _record_health(success=True)
    except smtplib.SMTPException as exc:
        _record_health(success=False)
        _safe_to = to.replace("\n", " ").replace("\r", " ")
        log.warning("SMTP error sending to %s: %s", _safe_to, str(exc).splitlines()[0])
        raise EmailSendError(str(exc)) from exc
    except OSError as exc:
        _record_health(success=False)
        _safe_to = to.replace("\n", " ").replace("\r", " ")
        log.warning(
            "OS error sending email to %s: %s", _safe_to, str(exc).splitlines()[0]
        )
        raise EmailSendError(str(exc)) from exc
