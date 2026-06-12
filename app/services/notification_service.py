"""
Notification service — three-level preference lookup, email dispatch, daily checks.

Three-level lookup (highest wins):
  1. NotificationPreference  — per-user per-tenant override
  2. TenantNotificationDefault — per-tenant override of system defaults
  3. NotificationType.SYSTEM_DEFAULTS — coded constants, no DB row

All functions that touch the DB must be called within an app context.
"""

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

_REPO_URL = "https://github.com/e2jk/OpenHangar"


# ── Preference lookup ──────────────────────────────────────────────────────────


def get_effective_preference(
    user_id: int, tenant_id: int, notification_type: str
) -> dict[str, Any]:
    """Return {"enabled": bool, "threshold_days": int|None} for this user/tenant/type."""
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationPreference as NP,
        NotificationType,
        TenantNotificationDefault,
        db,
    )

    user_pref = (
        db.session.query(NP)
        .filter_by(
            user_id=user_id, tenant_id=tenant_id, notification_type=notification_type
        )
        .first()
    )
    if user_pref is not None:
        return {
            "enabled": user_pref.enabled,
            "threshold_days": user_pref.threshold_days,
        }

    tenant_def = (
        db.session.query(TenantNotificationDefault)
        .filter_by(tenant_id=tenant_id, notification_type=notification_type)
        .first()
    )
    if tenant_def is not None:
        return {
            "enabled": tenant_def.enabled,
            "threshold_days": tenant_def.threshold_days,
        }

    return dict(
        NotificationType.SYSTEM_DEFAULTS.get(
            notification_type, {"enabled": False, "threshold_days": None}
        )
    )


# ── Recipient resolution ───────────────────────────────────────────────────────


def _user_caps(role: Any, user: Any) -> set[str]:
    """Compute capability set for a user from their role + capability flags."""
    from models import Role  # pyright: ignore[reportMissingImports]

    caps: set[str] = set()
    if role in (Role.ADMIN, Role.OWNER):
        caps |= {"is_owner", "is_pilot", "is_maint"}
    if role in (Role.PILOT, Role.STUDENT) or getattr(user, "is_pilot", False):
        caps.add("is_pilot")
    if role == Role.MAINTENANCE or getattr(user, "is_maintenance", False):
        caps.add("is_maint")
    if role == Role.INSTRUCTOR:
        caps |= {"is_pilot", "is_maint"}
    return caps


def _find_recipients(
    notification_type: str, tenant_id: int, target_user_ids: list[int] | None = None
) -> list[Any]:
    """Return list of User objects that should receive this notification type."""
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationType,
        TenantUser,
        User,
        db,
    )

    required = set(NotificationType.REQUIRED_CAPS.get(notification_type, []))

    query = (
        db.session.query(User, TenantUser)
        .join(TenantUser, TenantUser.user_id == User.id)
        .filter(TenantUser.tenant_id == tenant_id, User.is_active.is_(True))
    )
    if target_user_ids is not None:
        query = query.filter(User.id.in_(target_user_ids))

    recipients = []
    for user, tu in query.all():
        caps = _user_caps(tu.role, user)
        if caps & required:
            recipients.append(user)
    return recipients


# ── Branding ──────────────────────────────────────────────────────────────────


def _tenant_display_name(profile: Any) -> str:
    if profile is None:
        return "OpenHangar"
    return (
        profile.club_name
        or profile.school_name
        or profile.organisation_name
        or "OpenHangar"
    )


def _build_subject(base: str, profile: Any) -> str:
    prefix = getattr(profile, "email_subject_prefix", None) if profile else None
    return f"[{prefix}] {base}" if prefix else base


# ── Template rendering ─────────────────────────────────────────────────────────


def _render_email(template_name: str, **ctx: Any) -> tuple[str, str]:
    """Return (text_body, html_body) for a notification email."""
    from flask import render_template  # pyright: ignore[reportMissingImports]

    ctx.setdefault("repo_url", _REPO_URL)
    body_html = render_template(f"email/notif/{template_name}", **ctx)
    html = render_template("email/base_email.html", body=body_html, **ctx)
    return ctx.get("text_body", ""), html


def _text_for(notification_type: str, context: dict[str, Any]) -> str:
    """Build a plain-text fallback body."""
    title = context.get("notification_title", notification_type)
    message = context.get("notification_message", "")
    lines = [title, "", message]
    if context.get("details"):
        for label, val in context["details"]:
            lines.append(f"{label}: {val}")
    if context.get("cta_url"):
        lines += ["", context["cta_url"]]
    return "\n".join(lines)


# ── Dispatch ──────────────────────────────────────────────────────────────────


def dispatch(
    notification_type: str,
    tenant_id: int,
    email_context: dict[str, Any],
    target_user_ids: list[int] | None = None,
) -> None:
    """
    Find all eligible recipients and send notification emails.

    Must be called within an app context.
    target_user_ids: if set, only notify these users (used for pilot-self events).
    """
    from models import TenantProfile  # pyright: ignore[reportMissingImports]
    from services.email_service import (  # pyright: ignore[reportMissingImports]
        EmailNotConfiguredError,
        EmailSendError,
        send_email,
    )

    profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
    base_subject = email_context.get("subject", notification_type)
    subject = _build_subject(base_subject, profile)

    recipients = _find_recipients(notification_type, tenant_id, target_user_ids)

    for user in recipients:
        pref = get_effective_preference(user.id, tenant_id, notification_type)
        if not pref["enabled"]:
            continue

        # Merge threshold from preference into context
        ctx = dict(email_context)
        ctx.setdefault("threshold_days", pref["threshold_days"])
        ctx["subject"] = subject
        ctx["recipient_name"] = user.display_name

        text_body = _text_for(notification_type, ctx)
        try:
            _text, html_body = _render_email("generic.html", text_body=text_body, **ctx)
            send_email(
                to=user.email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                locale=user.language or "en",
            )
        except EmailNotConfiguredError:
            return  # SMTP not configured — stop trying all recipients
        except EmailSendError as exc:
            log.warning("Notification email to %s failed: %s", user.email, exc)
        except Exception:
            log.exception("Unexpected error sending notification to %s", user.email)


# ── Daily expiry checks ────────────────────────────────────────────────────────


def run_daily_checks(app: Any) -> None:
    """Check all expiry-based notification types across all tenants. Runs in background thread."""
    with app.app_context():
        try:
            _check_maintenance(app)
            _check_insurance(app)
            _check_medical_and_sep(app)
            _check_documents(app)
            _check_airworthiness_reviews(app)
        except Exception:
            log.exception("Error in daily notification checks")


def _check_maintenance(app: Any) -> None:
    from models import Aircraft, Tenant  # pyright: ignore[reportMissingImports]
    from models import NotificationType as NT  # pyright: ignore[reportMissingImports]

    for tenant in Tenant.query.filter_by(is_active=True).all():
        aircraft_list = Aircraft.query.filter_by(tenant_id=tenant.id).all()
        for ac in aircraft_list:
            hobbs = ac.total_engine_hours
            for trigger in ac.maintenance_triggers:
                status = trigger.status(hobbs)
                if status == "overdue":
                    _dispatch_in_context(
                        NT.MAINTENANCE_OVERDUE,
                        tenant.id,
                        {
                            "subject": f"Maintenance overdue: {trigger.name} on {ac.registration}",
                            "notification_title": f"Maintenance overdue: {trigger.name}",
                            "notification_message": f"{trigger.name} on {ac.registration} is overdue.",
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Item", trigger.name),
                            ],
                        },
                    )
                elif status == "due_soon":
                    _dispatch_in_context(
                        NT.MAINTENANCE_DUE_SOON,
                        tenant.id,
                        {
                            "subject": f"Maintenance due soon: {trigger.name} on {ac.registration}",
                            "notification_title": f"Maintenance due soon: {trigger.name}",
                            "notification_message": f"{trigger.name} on {ac.registration} is coming due.",
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Item", trigger.name),
                            ],
                        },
                    )


def _check_insurance(app: Any) -> None:
    from models import Aircraft, NotificationType as NT, Tenant  # pyright: ignore[reportMissingImports]

    today = date.today()
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id).all():
            if ac.insurance_expiry is None:
                continue
            days_left = (ac.insurance_expiry - today).days
            # Use system default threshold; recipient-level override applied in dispatch()
            threshold = (
                NT.SYSTEM_DEFAULTS[NT.INSURANCE_EXPIRING]["threshold_days"] or 30
            )
            if 0 <= days_left <= threshold:
                _dispatch_in_context(
                    NT.INSURANCE_EXPIRING,
                    tenant.id,
                    {
                        "subject": f"Insurance expiring in {days_left} day(s): {ac.registration}",
                        "notification_title": f"Insurance expiring soon: {ac.registration}",
                        "notification_message": f"The insurance for {ac.registration} expires on {ac.insurance_expiry.isoformat()} ({days_left} day(s) remaining).",
                        "details": [
                            ("Aircraft", ac.registration),
                            ("Expires", ac.insurance_expiry.isoformat()),
                            ("Days left", str(days_left)),
                        ],
                    },
                )


def _check_medical_and_sep(app: Any) -> None:
    from models import NotificationType as NT, PilotProfile, TenantUser, User, db  # pyright: ignore[reportMissingImports]

    today = date.today()
    for profile in PilotProfile.query.all():
        user = db.session.get(User, profile.user_id)
        if user is None or not user.is_active:
            continue
        tu = TenantUser.query.filter_by(user_id=user.id).first()
        if tu is None:
            continue

        for notif_type, expiry, label in [
            (NT.MEDICAL_EXPIRING, profile.medical_expiry, "Medical certificate"),
            (NT.SEP_RATING_EXPIRING, profile.sep_expiry, "SEP rating"),
        ]:
            if expiry is None:
                continue
            days_left = (expiry - today).days
            threshold = NT.SYSTEM_DEFAULTS[notif_type]["threshold_days"] or 60
            if 0 <= days_left <= threshold:
                _dispatch_in_context(
                    notif_type,
                    tu.tenant_id,
                    {
                        "subject": f"{label} expiring in {days_left} day(s)",
                        "notification_title": f"{label} expiring soon",
                        "notification_message": f"Your {label.lower()} expires on {expiry.isoformat()} ({days_left} day(s) remaining).",
                        "details": [
                            ("Expires", expiry.isoformat()),
                            ("Days left", str(days_left)),
                        ],
                    },
                    target_user_ids=[user.id],
                )


def _check_documents(app: Any) -> None:
    from models import Aircraft, Document, NotificationType as NT, Tenant  # pyright: ignore[reportMissingImports]

    today = date.today()
    threshold = NT.SYSTEM_DEFAULTS[NT.DOCUMENT_EXPIRING]["threshold_days"] or 30
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id).all():
            for doc in Document.query.filter_by(aircraft_id=ac.id).all():
                if doc.valid_until is None:
                    continue
                days_left = (doc.valid_until - today).days
                if 0 <= days_left <= threshold:
                    title = doc.title or doc.original_filename
                    _dispatch_in_context(
                        NT.DOCUMENT_EXPIRING,
                        tenant.id,
                        {
                            "subject": f"Document expiring in {days_left} day(s): {title}",
                            "notification_title": f"Document expiring soon: {title}",
                            "notification_message": f"'{title}' on {ac.registration} expires on {doc.valid_until.isoformat()} ({days_left} day(s) remaining).",
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Document", title),
                                ("Expires", doc.valid_until.isoformat()),
                            ],
                        },
                    )


def _check_airworthiness_reviews(app: Any) -> None:
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        AirworthinessDocumentStatus,
        NotificationType as NT,
        Tenant,
    )

    today = date.today()
    threshold = NT.SYSTEM_DEFAULTS[NT.AIRWORTHINESS_REVIEW_DUE]["threshold_days"] or 30
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id).all():
            for status_row in AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac.id
            ).all():
                if status_row.next_review_date is None:
                    continue
                days_left = (status_row.next_review_date - today).days
                if 0 <= days_left <= threshold:
                    doc = status_row.document
                    ref = doc.reference if doc else "unknown"
                    _dispatch_in_context(
                        NT.AIRWORTHINESS_REVIEW_DUE,
                        tenant.id,
                        {
                            "subject": f"Airworthiness review due in {days_left} day(s): {ref} on {ac.registration}",
                            "notification_title": f"Airworthiness review due: {ref}",
                            "notification_message": f"Document {ref} on {ac.registration} requires review by {status_row.next_review_date.isoformat()} ({days_left} day(s)).",
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Document", ref),
                                ("Due", status_row.next_review_date.isoformat()),
                            ],
                        },
                    )


def _dispatch_in_context(
    notification_type: str,
    tenant_id: int,
    email_context: dict[str, Any],
    target_user_ids: list[int] | None = None,
) -> None:
    """Call dispatch() safely, logging any errors."""
    try:
        dispatch(notification_type, tenant_id, email_context, target_user_ids)
    except Exception:
        log.exception(
            "Error dispatching %s for tenant %d", notification_type, tenant_id
        )


# ── Welcome email ──────────────────────────────────────────────────────────────


def send_welcome_email_if_needed(app: Any) -> None:
    """Send one-time welcome email to the instance owner. Called at startup."""
    try:
        with app.app_context():
            import os
            from models import AppSetting, User, db  # pyright: ignore[reportMissingImports]
            from services.email_service import (  # pyright: ignore[reportMissingImports]
                send_email,
            )

            if db.session.get(AppSetting, "welcome_email_sent"):
                return
            if not os.environ.get("SMTP_HOST", "").strip():
                return

            owner = (
                User.query.filter_by(is_instance_admin=True).order_by(User.id).first()
            )
            if not owner:
                return

            subject = "Welcome to your OpenHangar instance"
            text_body = (
                f"Hello {owner.display_name},\n\n"
                "Welcome to OpenHangar! Your instance is set up and email delivery is working.\n\n"
                "You can configure notification preferences for all users under\n"
                "Configuration → Email Notifications.\n\n"
                "Fly safely!\n\nThe OpenHangar team"
            )
            from flask import render_template  # pyright: ignore[reportMissingImports]

            body_html = render_template(
                "email/notif/welcome.html",
                owner=owner,
                repo_url=_REPO_URL,
                subject=subject,
            )
            html_body = render_template(
                "email/base_email.html",
                body=body_html,
                subject=subject,
                repo_url=_REPO_URL,
            )

            send_email(
                to=owner.email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                locale=owner.language or "en",
            )

            db.session.add(AppSetting(key="welcome_email_sent", value="true"))
            db.session.commit()
            log.info("Welcome email sent to %s", owner.email)
    except Exception:
        log.exception("Failed to send welcome email (will not retry)")
