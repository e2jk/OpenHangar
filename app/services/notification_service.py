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


def _render_email(
    template_name: str, locale: str = "en", **ctx: Any
) -> tuple[str, str]:
    """Return (text_body, html_body) for a notification email."""
    import os
    from flask import render_template  # pyright: ignore[reportMissingImports]
    from flask_babel import force_locale  # pyright: ignore[reportMissingImports]

    ctx.setdefault("repo_url", _REPO_URL)
    ctx.setdefault(
        "instance_url", os.environ.get("OPENHANGAR_INSTANCE_URL", "").strip() or None
    )
    with force_locale(locale):
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
    from flask_babel import force_locale  # pyright: ignore[reportMissingImports]
    from models import TenantProfile  # pyright: ignore[reportMissingImports]
    from services.email_service import (  # pyright: ignore[reportMissingImports]
        EmailNotConfiguredError,
        EmailSendError,
        send_email,
    )

    profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
    recipients = _find_recipients(notification_type, tenant_id, target_user_ids)

    for user in recipients:
        pref = get_effective_preference(user.id, tenant_id, notification_type)
        if not pref["enabled"]:
            continue

        locale = user.language or "en"
        with force_locale(locale):
            if "subject_key" in email_context:
                base_subject = str(email_context["subject_key"]) % email_context.get(
                    "subject_args", {}
                )
            else:
                base_subject = email_context.get("subject", notification_type)
            subject = _build_subject(base_subject, profile)
            if "notification_title_key" in email_context:
                notif_title = str(
                    email_context["notification_title_key"]
                ) % email_context.get("notification_title_args", {})
            else:
                notif_title = email_context.get("notification_title", notification_type)
            if "notification_message_key" in email_context:
                notif_message = str(
                    email_context["notification_message_key"]
                ) % email_context.get("notification_message_args", {})
            else:
                notif_message = email_context.get("notification_message", "")

        ctx = dict(email_context)
        ctx.setdefault("threshold_days", pref["threshold_days"])
        ctx["subject"] = subject
        ctx["notification_title"] = notif_title
        ctx["notification_message"] = notif_message
        ctx["recipient_name"] = user.display_name

        text_body = _text_for(notification_type, ctx)
        try:
            _text, html_body = _render_email(
                "generic.html", locale=locale, text_body=text_body, **ctx
            )
            send_email(
                to=user.email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                locale=locale,
            )
        except EmailNotConfiguredError:
            return  # SMTP not configured — stop trying all recipients
        except EmailSendError as exc:
            log.warning("Notification email to %s failed: %s", user.email, exc)
        except Exception:
            log.exception("Unexpected error sending notification to %s", user.email)


# ── Daily expiry checks ────────────────────────────────────────────────────────


def run_daily_checks(app: Any) -> None:
    """Check all expiry-based notification types across all tenants. Runs in background thread.

    Guarded by an advisory lock (see services.advisory_lock) so that only one
    gunicorn worker runs the checks per scheduled tick — without it, each of
    the four production workers would send its own copy of every alert email.
    """
    from models import db  # pyright: ignore[reportMissingImports]
    from services.advisory_lock import advisory_lock_scope  # pyright: ignore[reportMissingImports]

    with app.app_context():
        try:
            with advisory_lock_scope(db, 7283910457) as acquired:
                if not acquired:
                    log.info(
                        "Daily notification checks: another worker holds the lock — skipping"
                    )
                    return
                _check_maintenance(app)
                _check_insurance(app)
                _check_medical_and_sep(app)
                _check_documents(app)
                _check_airworthiness_reviews(app)
        except Exception:
            log.exception("Error in daily notification checks")


def _check_maintenance(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
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
                            "subject_key": _l(
                                "Maintenance overdue: %(name)s on %(reg)s"
                            ),
                            "subject_args": {
                                "name": trigger.name,
                                "reg": ac.registration,
                            },
                            "notification_title_key": _l(
                                "Maintenance overdue: %(name)s"
                            ),
                            "notification_title_args": {"name": trigger.name},
                            "notification_message_key": _l(
                                "%(name)s on %(reg)s is overdue."
                            ),
                            "notification_message_args": {
                                "name": trigger.name,
                                "reg": ac.registration,
                            },
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
                            "subject_key": _l(
                                "Maintenance due soon: %(name)s on %(reg)s"
                            ),
                            "subject_args": {
                                "name": trigger.name,
                                "reg": ac.registration,
                            },
                            "notification_title_key": _l(
                                "Maintenance due soon: %(name)s"
                            ),
                            "notification_title_args": {"name": trigger.name},
                            "notification_message_key": _l(
                                "%(name)s on %(reg)s is coming due."
                            ),
                            "notification_message_args": {
                                "name": trigger.name,
                                "reg": ac.registration,
                            },
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Item", trigger.name),
                            ],
                        },
                    )


def _check_insurance(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
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
                        "subject_key": _l(
                            "Insurance expiring in %(days)s day(s): %(reg)s"
                        ),
                        "subject_args": {"days": days_left, "reg": ac.registration},
                        "notification_title_key": _l(
                            "Insurance expiring soon: %(reg)s"
                        ),
                        "notification_title_args": {"reg": ac.registration},
                        "notification_message_key": _l(
                            "The insurance for %(reg)s expires on %(date)s (%(days)s day(s) remaining)."
                        ),
                        "notification_message_args": {
                            "reg": ac.registration,
                            "date": ac.insurance_expiry.isoformat(),
                            "days": days_left,
                        },
                        "details": [
                            ("Aircraft", ac.registration),
                            ("Expires", ac.insurance_expiry.isoformat()),
                            ("Days left", str(days_left)),
                        ],
                    },
                )


def _check_medical_and_sep(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
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
                        "subject_key": _l("%(label)s expiring in %(days)s day(s)"),
                        "subject_args": {"label": label, "days": days_left},
                        "notification_title_key": _l("%(label)s expiring soon"),
                        "notification_title_args": {"label": label},
                        "notification_message_key": _l(
                            "Your %(label_lower)s expires on %(date)s (%(days)s day(s) remaining)."
                        ),
                        "notification_message_args": {
                            "label_lower": label.lower(),
                            "date": expiry.isoformat(),
                            "days": days_left,
                        },
                        "details": [
                            ("Expires", expiry.isoformat()),
                            ("Days left", str(days_left)),
                        ],
                    },
                    target_user_ids=[user.id],
                )


def _check_documents(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
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
                            "subject_key": _l(
                                "Document expiring in %(days)s day(s): %(title)s"
                            ),
                            "subject_args": {"days": days_left, "title": title},
                            "notification_title_key": _l(
                                "Document expiring soon: %(title)s"
                            ),
                            "notification_title_args": {"title": title},
                            "notification_message_key": _l(
                                "'%(title)s' on %(reg)s expires on %(date)s (%(days)s day(s) remaining)."
                            ),
                            "notification_message_args": {
                                "title": title,
                                "reg": ac.registration,
                                "date": doc.valid_until.isoformat(),
                                "days": days_left,
                            },
                            "details": [
                                ("Aircraft", ac.registration),
                                ("Document", title),
                                ("Expires", doc.valid_until.isoformat()),
                            ],
                        },
                    )


def _check_airworthiness_reviews(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
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
                            "subject_key": _l(
                                "Airworthiness review due in %(days)s day(s): %(ref)s on %(reg)s"
                            ),
                            "subject_args": {
                                "days": days_left,
                                "ref": ref,
                                "reg": ac.registration,
                            },
                            "notification_title_key": _l(
                                "Airworthiness review due: %(ref)s"
                            ),
                            "notification_title_args": {"ref": ref},
                            "notification_message_key": _l(
                                "Document %(ref)s on %(reg)s requires review by %(date)s (%(days)s day(s))."
                            ),
                            "notification_message_args": {
                                "ref": ref,
                                "reg": ac.registration,
                                "date": status_row.next_review_date.isoformat(),
                                "days": days_left,
                            },
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
    except Exception as exc:
        log.error(
            "Error dispatching notification for tenant %d: %s",
            tenant_id,
            type(exc).__name__,
        )


# ── Welcome email ──────────────────────────────────────────────────────────────


def _try_welcome_lock(db: Any) -> bool:
    """Return False if another gunicorn worker already holds the startup lock."""
    if db.engine.dialect.name != "postgresql":
        return True
    from sqlalchemy import text as _text  # pyright: ignore[reportMissingImports]

    return bool(
        db.session.execute(
            _text("SELECT pg_try_advisory_xact_lock(7283910456)")
        ).scalar()
    )


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
            if not os.environ.get("OPENHANGAR_SMTP_HOST", "").strip():
                return

            # Guard against all gunicorn workers racing at startup.
            if not _try_welcome_lock(db):
                return

            # Re-check after acquiring the lock: another worker may have
            # finished sending while we were waiting to acquire it.
            db.session.expire_all()
            if db.session.get(AppSetting, "welcome_email_sent"):
                return

            owner = (
                User.query.filter_by(is_instance_admin=True).order_by(User.id).first()
            )
            if not owner:
                return

            from flask import render_template  # pyright: ignore[reportMissingImports]
            from flask_babel import force_locale, gettext  # pyright: ignore[reportMissingImports]

            locale = owner.language or "en"
            instance_url = os.environ.get("OPENHANGAR_INSTANCE_URL", "").strip() or None
            with force_locale(locale):
                subject = gettext("Welcome to your OpenHangar instance")
                greeting = gettext("Hello %(name)s,") % {"name": owner.display_name}
                body_text = gettext(
                    "Welcome to OpenHangar! Your instance is set up and email"
                    " delivery is working.\n\n"
                    "You can configure notification preferences for all users"
                    " under Configuration → Email Notifications.\n\n"
                    "Fly safely!\n\nThe OpenHangar team"
                )
                text_body = greeting + "\n\n" + body_text
                body_html = render_template(
                    "email/notif/welcome.html",
                    owner=owner,
                    repo_url=_REPO_URL,
                    subject=subject,
                    instance_url=instance_url,
                )
                html_body = render_template(
                    "email/base_email.html",
                    body=body_html,
                    subject=subject,
                    repo_url=_REPO_URL,
                    instance_url=instance_url,
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
    except Exception as exc:
        log.error("Failed to send welcome email (will not retry): %s", exc)
