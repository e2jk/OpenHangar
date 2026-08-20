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
    )
    from models import (
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
    if context.get("snooze_url"):
        lines += ["", context["snooze_url"]]
    return "\n".join(lines)


# ── Send-log dedup + per-instance snooze ────────────────────────────────────────


def _already_sent_today(
    user_id: int, tenant_id: int, notification_type: str, subject_ref: str
) -> bool:
    from models import NotificationSendLog  # pyright: ignore[reportMissingImports]

    return (
        NotificationSendLog.query.filter_by(
            user_id=user_id,
            tenant_id=tenant_id,
            notification_type=notification_type,
            subject_ref=subject_ref,
            sent_date=date.today(),
        ).first()
        is not None
    )


def _record_sent(
    user_id: int, tenant_id: int, notification_type: str, subject_ref: str
) -> None:
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationSendLog,
        db,
    )

    try:
        db.session.add(
            NotificationSendLog(
                user_id=user_id,
                tenant_id=tenant_id,
                notification_type=notification_type,
                subject_ref=subject_ref,
                sent_date=date.today(),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Failed to record notification send log")


def _resolve_snooze(
    user_id: int,
    tenant_id: int,
    notification_type: str,
    subject_ref: str,
    expiry_value: str,
    label: str,
) -> tuple[bool, str]:
    """Get-or-create the NotificationSnooze row for this (user, instance),
    refresh it with the live deadline value, and return
    (suppressed, snooze_url). suppressed is True only when the user has
    already confirmed a snooze for exactly this deadline value -- if the
    deadline has since moved (e.g. a renewed document was uploaded), any
    prior confirmation is stale and is cleared here instead."""
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationSnooze,
        db,
    )

    row = NotificationSnooze.query.filter_by(
        user_id=user_id,
        tenant_id=tenant_id,
        notification_type=notification_type,
        subject_ref=subject_ref,
    ).first()

    if row is not None and row.snoozed_value == expiry_value:
        return True, _snooze_url(row.token)

    if row is None:
        row = NotificationSnooze(
            user_id=user_id,
            tenant_id=tenant_id,
            notification_type=notification_type,
            subject_ref=subject_ref,
            label=label,
            current_value=expiry_value,
        )
        db.session.add(row)
    else:
        row.snoozed_value = None
        row.snoozed_at = None
        row.current_value = expiry_value
        row.label = label
    db.session.commit()
    return False, _snooze_url(row.token)


def _snooze_url(token: str) -> str:
    """Build an absolute snooze link without relying on url_for()'s
    request-context binding -- run_daily_checks() runs in a background
    thread with only an app context, no active request, so url_for()'s
    usual host-from-request lookup isn't available. Mirrors the
    OPENHANGAR_INSTANCE_URL pattern already used for `instance_url` above."""
    import os

    from flask import current_app  # pyright: ignore[reportMissingImports]

    path = current_app.url_map.bind("localhost").build(
        "notifications.snooze", {"token": token}
    )
    instance_url = os.environ.get("OPENHANGAR_INSTANCE_URL", "").strip()
    return f"{instance_url.rstrip('/')}{path}" if instance_url else path


# ── Dispatch ──────────────────────────────────────────────────────────────────


def dispatch(
    notification_type: str,
    tenant_id: int,
    email_context: dict[str, Any],
    target_user_ids: list[int] | None = None,
    subject_ref: str | None = None,
) -> None:
    """
    Find all eligible recipients and send notification emails.

    Must be called within an app context.
    target_user_ids: if set, only notify these users (used for pilot-self events).
    subject_ref: stable id of the specific thing this notification is about
    (e.g. "aircraft:12"), passed by the daily checks in run_daily_checks().
    When set, enables two things: (1) a per-user-per-day send log so a
    second run on the same day (e.g. after a restart) does not resend, and
    (2) when email_context also carries "expiry_value" (an ISO date string
    snapshot of the live deadline), a one-click snooze link that suppresses
    future emails for this exact deadline value until it changes. Callers
    that don't pass subject_ref (event-driven notifications like a snag
    being reported) keep today's always-send behaviour -- they're one-shot
    events, not daily re-evaluations.
    """
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        force_locale,
        gettext,
    )
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
            greeting = gettext("Hello %(name)s,") % {"name": user.display_name}

        snooze_url = None
        if subject_ref is not None:
            expiry_value = email_context.get("expiry_value")
            if expiry_value is not None:
                suppressed, snooze_url = _resolve_snooze(
                    user.id,
                    tenant_id,
                    notification_type,
                    subject_ref,
                    expiry_value,
                    notif_title,
                )
                if suppressed:
                    continue
            if _already_sent_today(user.id, tenant_id, notification_type, subject_ref):
                continue

        ctx = dict(email_context)
        ctx.setdefault("threshold_days", pref["threshold_days"])
        # generic.html treats these as optional ({% if %} guards), but under
        # Jinja's StrictUndefined (active whenever TESTING or a development
        # environment is detected at create_app() time) an absent key raises
        # instead of evaluating falsy -- most _check_* callers never set
        # them, so without a default here every such notification email
        # silently fails to send in a strict-undefined environment.
        ctx.setdefault("cta_url", None)
        ctx.setdefault("cta_label", None)
        ctx.setdefault("details", None)
        ctx["snooze_url"] = snooze_url
        ctx["subject"] = subject
        ctx["notification_title"] = notif_title
        ctx["notification_message"] = notif_message
        ctx["recipient_name"] = user.display_name
        ctx["greeting"] = greeting

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
            if subject_ref is not None:
                _record_sent(user.id, tenant_id, notification_type, subject_ref)
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

    from services.advisory_lock import (
        advisory_lock_scope,  # pyright: ignore[reportMissingImports]
    )

    with app.app_context():
        try:
            with advisory_lock_scope(db, 7283910457) as acquired:
                if not acquired:
                    log.info(
                        "Daily notification checks: another worker holds the lock — skipping"
                    )
                    return
                from services.co_owner_billing import (  # pyright: ignore[reportMissingImports]
                    run_co_owner_billing_pass_all,
                )
                from services.recurring_expense_service import (  # pyright: ignore[reportMissingImports]
                    materialize_recurring_expenses,
                )

                materialize_recurring_expenses()
                run_co_owner_billing_pass_all()
                _check_maintenance(app)
                _check_insurance(app)
                _check_arc(app)
                _check_medical_and_sep(app)
                _check_documents(app)
                _check_airworthiness_reviews(app)
                _check_renter_authorizations(app)
                _check_personal_minimums_recency(app)
        except Exception:
            log.exception("Error in daily notification checks")


def _check_maintenance(app: Any) -> None:
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
    from models import Aircraft, Tenant  # pyright: ignore[reportMissingImports]
    from models import NotificationType as NT  # pyright: ignore[reportMissingImports]

    for tenant in Tenant.query.filter_by(is_active=True).all():
        aircraft_list = Aircraft.query.filter_by(
            tenant_id=tenant.id, archived_at=None
        ).all()
        hobbs_by_id = Aircraft.engine_hours_by_id([ac.id for ac in aircraft_list])
        landings_by_id = Aircraft.landings_by_id([ac.id for ac in aircraft_list])
        flight_hours_by_id = Aircraft.flight_hours_by_id(
            [ac.id for ac in aircraft_list]
        )
        for ac in aircraft_list:
            hobbs = hobbs_by_id[ac.id]
            landings = landings_by_id[ac.id]
            flight_hours = flight_hours_by_id[ac.id]
            for trigger in ac.maintenance_triggers:
                status = trigger.status(
                    current_engine_hours=hobbs,
                    current_landings=landings,
                    current_flight_hours=flight_hours,
                )
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
                                (_l("Aircraft"), ac.registration),
                                (_l("Item"), trigger.name),
                            ],
                        },
                        subject_ref=f"trigger:{trigger.id}",
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
                                (_l("Aircraft"), ac.registration),
                                (_l("Item"), trigger.name),
                            ],
                        },
                        subject_ref=f"trigger:{trigger.id}",
                    )


def _check_insurance(app: Any) -> None:
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import Aircraft, Tenant  # pyright: ignore[reportMissingImports]
    from models import NotificationType as NT

    today = date.today()
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id, archived_at=None).all():
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
                        "subject_key": _ln(
                            "Insurance expiring in one day: %(reg)s",
                            "Insurance expiring in %(days)s days: %(reg)s",
                            days_left,
                            days=days_left,
                            reg=ac.registration,
                        ),
                        "subject_args": {},
                        "notification_title_key": _l(
                            "Insurance expiring soon: %(reg)s"
                        ),
                        "notification_title_args": {"reg": ac.registration},
                        "notification_message_key": _ln(
                            "The insurance for %(reg)s expires on %(date)s (one day remaining).",
                            "The insurance for %(reg)s expires on %(date)s (%(days)s days remaining).",
                            days_left,
                            reg=ac.registration,
                            date=ac.insurance_expiry.isoformat(),
                            days=days_left,
                        ),
                        "notification_message_args": {},
                        "details": [
                            (_l("Aircraft"), ac.registration),
                            (_l("Expires"), ac.insurance_expiry.isoformat()),
                            (_l("Days left"), str(days_left)),
                        ],
                        "expiry_value": ac.insurance_expiry.isoformat(),
                    },
                    subject_ref=f"aircraft:{ac.id}",
                )


def _check_arc(app: Any) -> None:
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import Aircraft, Tenant  # pyright: ignore[reportMissingImports]
    from models import NotificationType as NT

    today = date.today()
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id, archived_at=None).all():
            if ac.arc_expiry is None:
                continue
            days_left = (ac.arc_expiry - today).days
            # Use system default threshold; recipient-level override applied in dispatch()
            threshold = NT.SYSTEM_DEFAULTS[NT.ARC_EXPIRY]["threshold_days"] or 60
            if 0 <= days_left <= threshold:
                _dispatch_in_context(
                    NT.ARC_EXPIRY,
                    tenant.id,
                    {
                        "subject_key": _ln(
                            "ARC expiring in one day: %(reg)s",
                            "ARC expiring in %(days)s days: %(reg)s",
                            days_left,
                            days=days_left,
                            reg=ac.registration,
                        ),
                        "subject_args": {},
                        "notification_title_key": _l("ARC expiring soon: %(reg)s"),
                        "notification_title_args": {"reg": ac.registration},
                        "notification_message_key": _ln(
                            "The ARC for %(reg)s expires on %(date)s (one day remaining).",
                            "The ARC for %(reg)s expires on %(date)s (%(days)s days remaining).",
                            days_left,
                            reg=ac.registration,
                            date=ac.arc_expiry.isoformat(),
                            days=days_left,
                        ),
                        "notification_message_args": {},
                        "details": [
                            (_l("Aircraft"), ac.registration),
                            (_l("Expires"), ac.arc_expiry.isoformat()),
                            (_l("Days left"), str(days_left)),
                        ],
                        "expiry_value": ac.arc_expiry.isoformat(),
                    },
                    subject_ref=f"aircraft:{ac.id}",
                )


def _check_medical_and_sep(app: Any) -> None:
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import NotificationType as NT  # pyright: ignore[reportMissingImports]
    from models import PilotProfile, TenantUser, User, db

    today = date.today()
    for profile in PilotProfile.query.all():
        user = db.session.get(User, profile.user_id)
        if user is None or not user.is_active:
            continue
        tu = TenantUser.query.filter_by(user_id=user.id).first()
        if tu is None:
            continue

        # Two translatable forms per item (not a mechanical .lower() of the
        # capitalized one) -- lowercasing a translated string isn't a safe
        # general i18n operation (case rules and even correct wording can
        # differ by language), so each form is its own msgid.
        for notif_type, expiry, label, label_lower in [
            (
                NT.MEDICAL_EXPIRING,
                profile.medical_expiry,
                _l("Medical certificate"),
                _l("medical certificate"),
            ),
            (
                NT.SEP_RATING_EXPIRING,
                profile.sep_expiry,
                _l("SEP rating"),
                _l("SEP rating"),
            ),
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
                        "subject_key": _ln(
                            "%(label)s expiring in one day",
                            "%(label)s expiring in %(days)s days",
                            days_left,
                            label=label,
                            days=days_left,
                        ),
                        "subject_args": {},
                        "notification_title_key": _l("%(label)s expiring soon"),
                        "notification_title_args": {"label": label},
                        "notification_message_key": _ln(
                            "Your %(label_lower)s expires on %(date)s (one day remaining).",
                            "Your %(label_lower)s expires on %(date)s (%(days)s days remaining).",
                            days_left,
                            label_lower=label_lower,
                            date=expiry.isoformat(),
                            days=days_left,
                        ),
                        "notification_message_args": {},
                        "details": [
                            (_l("Expires"), expiry.isoformat()),
                            (_l("Days left"), str(days_left)),
                        ],
                        "expiry_value": expiry.isoformat(),
                    },
                    target_user_ids=[user.id],
                    subject_ref=f"pilot:{profile.id}",
                )


def _check_documents(app: Any) -> None:
    from documents.routes import (  # pyright: ignore[reportMissingImports]
        _EXPIRY_DRIVING_DOC_TYPES,
    )
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        Document,
        Tenant,
    )
    from models import NotificationType as NT

    today = date.today()
    threshold = NT.SYSTEM_DEFAULTS[NT.DOCUMENT_EXPIRING]["threshold_days"] or 30
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id, archived_at=None).all():
            for doc in Document.query.filter_by(aircraft_id=ac.id).all():
                if doc.valid_until is None:
                    continue
                # ARC/Insurance documents already drive their own dedicated
                # check (_check_arc / _check_insurance) off the synced
                # Aircraft.arc_expiry/insurance_expiry cache fields -- skip
                # them here to avoid sending two separate emails for the
                # same underlying deadline.
                if doc.doc_type in _EXPIRY_DRIVING_DOC_TYPES:
                    continue
                days_left = (doc.valid_until - today).days
                if 0 <= days_left <= threshold:
                    title = doc.title or doc.original_filename
                    _dispatch_in_context(
                        NT.DOCUMENT_EXPIRING,
                        tenant.id,
                        {
                            "subject_key": _ln(
                                "Document expiring in one day: %(title)s",
                                "Document expiring in %(days)s days: %(title)s",
                                days_left,
                                days=days_left,
                                title=title,
                            ),
                            "subject_args": {},
                            "notification_title_key": _l(
                                "Document expiring soon: %(title)s"
                            ),
                            "notification_title_args": {"title": title},
                            "notification_message_key": _ln(
                                "'%(title)s' on %(reg)s expires on %(date)s (one day remaining).",
                                "'%(title)s' on %(reg)s expires on %(date)s (%(days)s days remaining).",
                                days_left,
                                title=title,
                                reg=ac.registration,
                                date=doc.valid_until.isoformat(),
                                days=days_left,
                            ),
                            "notification_message_args": {},
                            "details": [
                                (_l("Aircraft"), ac.registration),
                                (_l("Document"), title),
                                (_l("Expires"), doc.valid_until.isoformat()),
                            ],
                            "expiry_value": doc.valid_until.isoformat(),
                        },
                        subject_ref=f"document:{doc.id}",
                    )


def _check_airworthiness_reviews(app: Any) -> None:
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import (  # pyright: ignore[reportMissingImports]
        Aircraft,
        AirworthinessDocumentStatus,
        Tenant,
    )
    from models import (
        NotificationType as NT,
    )

    today = date.today()
    threshold = NT.SYSTEM_DEFAULTS[NT.AIRWORTHINESS_REVIEW_DUE]["threshold_days"] or 30
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id, archived_at=None).all():
            for status_row in AirworthinessDocumentStatus.query.filter_by(
                aircraft_id=ac.id
            ).all():
                if status_row.next_review_date is None:
                    continue
                days_left = (status_row.next_review_date - today).days
                if 0 <= days_left <= threshold:
                    doc = status_row.document
                    ref = doc.reference if doc else _l("unknown")
                    _dispatch_in_context(
                        NT.AIRWORTHINESS_REVIEW_DUE,
                        tenant.id,
                        {
                            "subject_key": _ln(
                                "Airworthiness review due in one day: %(ref)s on %(reg)s",
                                "Airworthiness review due in %(days)s days: %(ref)s on %(reg)s",
                                days_left,
                                days=days_left,
                                ref=ref,
                                reg=ac.registration,
                            ),
                            "subject_args": {},
                            "notification_title_key": _l(
                                "Airworthiness review due: %(ref)s"
                            ),
                            "notification_title_args": {"ref": ref},
                            "notification_message_key": _ln(
                                "Document %(ref)s on %(reg)s requires review by %(date)s (one day).",
                                "Document %(ref)s on %(reg)s requires review by %(date)s (%(days)s days).",
                                days_left,
                                ref=ref,
                                reg=ac.registration,
                                date=status_row.next_review_date.isoformat(),
                                days=days_left,
                            ),
                            "notification_message_args": {},
                            "details": [
                                (_l("Aircraft"), ac.registration),
                                (_l("Document"), ref),
                                (_l("Due"), status_row.next_review_date.isoformat()),
                            ],
                            "expiry_value": status_row.next_review_date.isoformat(),
                        },
                        subject_ref=f"airworthiness_status:{status_row.id}",
                    )


def _check_renter_authorizations(app: Any) -> None:
    """One digest notification per tenant listing every renter authorization
    whose expires_on or medical_valid_until falls within the threshold —
    not one email per authorization (has_content guard: nothing to report
    means no dispatch call at all)."""
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationType as NT,
    )
    from models import (
        RenterAuthorization,
        Tenant,
    )

    today = date.today()
    threshold = (
        NT.SYSTEM_DEFAULTS[NT.RENTER_AUTHORIZATION_EXPIRY]["threshold_days"] or 30
    )
    for tenant in Tenant.query.filter_by(is_active=True).all():
        rows: list[tuple[Any, str, date]] = []
        for auth in RenterAuthorization.query.filter_by(
            tenant_id=tenant.id, revoked_at=None
        ).all():
            for label, expiry in (
                ("authorization", auth.expires_on),
                ("medical", auth.medical_valid_until),
            ):
                if expiry is None:
                    continue
                days_left = (expiry - today).days
                if 0 <= days_left <= threshold:
                    rows.append((auth, label, expiry))

        if not rows:  # has_content guard — nothing soon-expiring, no email
            continue

        # Internal loop markers ("authorization"/"medical") are looked up
        # against a translated-label map rather than displayed directly --
        # embedding a lazy string in an f-string would force it to resolve
        # immediately in whatever locale is active at check-time, not the
        # eventual recipient's, so the translated value is built with _l()
        # instead and stays lazy until Jinja renders it per-recipient.
        _label_text = {"authorization": _l("authorization"), "medical": _l("medical")}
        details = []
        for auth, label, expiry in rows:
            renter_name = (
                auth.renter_user.display_name if auth.renter_user else _l("unknown")
            )
            details.append(
                (
                    renter_name,
                    _l(
                        "%(label)s expires %(date)s",
                        label=_label_text[label],
                        date=expiry.isoformat(),
                    ),
                )
            )

        # Two independent countable quantities in one sentence (item count and
        # day count) — ngettext only picks one plural form per call, so each
        # is pluralized separately (as its own fully-resolved lazy fragment)
        # and dropped into an outer, non-plural template.
        item_count_phrase = _ln(
            "One renter authorization",
            "%(n)s renter authorizations",
            len(rows),
            n=len(rows),
        )
        day_count_phrase = _ln(
            "one day",
            "%(threshold)s days",
            threshold,
            threshold=threshold,
        )
        _dispatch_in_context(
            NT.RENTER_AUTHORIZATION_EXPIRY,
            tenant.id,
            {
                "subject_key": _ln(
                    "One renter authorization expiring soon",
                    "%(n)s renter authorizations expiring soon",
                    len(rows),
                    n=len(rows),
                ),
                "subject_args": {},
                "notification_title_key": _l("Renter authorizations expiring soon"),
                "notification_title_args": {},
                "notification_message_key": _l(
                    "%(items)s or medical validity dates expire within %(days)s."
                ),
                "notification_message_args": {
                    "items": item_count_phrase,
                    "days": day_count_phrase,
                },
                "details": details,
            },
            subject_ref=f"tenant:{tenant.id}",
        )


def _check_personal_minimums_recency(app: Any) -> None:
    """One notification per pilot listing every personal-minimums recency
    item they have exceeded (has_content guard: no breaches, no dispatch).
    Only pilots with an active revision and at least one tagged, breached
    item are considered."""
    from flask_babel import (  # pyright: ignore[reportMissingImports]
        lazy_gettext as _l,
    )
    from flask_babel import (
        lazy_ngettext as _ln,
    )
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationType as NT,
    )
    from models import (
        PersonalMinimumsRevision,
        PersonalMinimumsStatus,
        TenantUser,
        User,
        db,
    )
    from pilots.personal_minimums import (
        recency_breaches,  # pyright: ignore[reportMissingImports]
    )

    for revision in PersonalMinimumsRevision.query.filter_by(
        status=PersonalMinimumsStatus.ACTIVE
    ).all():
        user = db.session.get(User, revision.user_id)
        if user is None or not user.is_active:
            continue
        tu = TenantUser.query.filter_by(user_id=user.id).first()
        if tu is None:
            continue

        breaches = recency_breaches(revision, user.id)
        if not breaches:  # has_content guard
            continue

        # Same phrasing (and translations) as the identical breach data
        # shown in app/templates/pilots/logbook.html.
        details = []
        for b in breaches:
            if b["days_since"] is None:
                days_txt = _l(
                    "no matching flight on record yet (comfort zone: %(threshold)s days).",
                    threshold=b["threshold"],
                )
            else:
                days_txt = _l(
                    "%(days)s days since your last matching flight (comfort zone: %(threshold)s days).",
                    days=b["days_since"],
                    threshold=b["threshold"],
                )
            details.append((b["item"].label, days_txt))

        _dispatch_in_context(
            NT.PERSONAL_MINIMUMS_RECENCY,
            tu.tenant_id,
            {
                "subject_key": _ln(
                    "Personal minimums: one recency threshold exceeded",
                    "Personal minimums: %(n)s recency thresholds exceeded",
                    len(breaches),
                    n=len(breaches),
                ),
                "subject_args": {},
                "notification_title_key": _l("Personal minimums recency reminder"),
                "notification_title_args": {},
                "notification_message_key": _ln(
                    "You have exceeded one recency threshold in your "
                    "personal minimums.",
                    "You have exceeded %(n)s recency thresholds in your "
                    "personal minimums.",
                    len(breaches),
                    n=len(breaches),
                ),
                "notification_message_args": {},
                "details": details,
            },
            target_user_ids=[user.id],
            subject_ref=f"user:{user.id}",
        )


def _dispatch_in_context(
    notification_type: str,
    tenant_id: int,
    email_context: dict[str, Any],
    target_user_ids: list[int] | None = None,
    subject_ref: str | None = None,
) -> None:
    """Call dispatch() safely, logging any errors."""
    try:
        dispatch(
            notification_type,
            tenant_id,
            email_context,
            target_user_ids,
            subject_ref=subject_ref,
        )
    except Exception as exc:  # noqa: BLE001 -- caller name says it: dispatch safely, never raise
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

            from models import (  # pyright: ignore[reportMissingImports]
                AppSetting,
                User,
                db,
            )

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
            from flask_babel import (  # pyright: ignore[reportMissingImports]
                force_locale,
                gettext,
            )

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
    except Exception as exc:  # noqa: BLE001 -- best-effort, explicitly will not retry
        log.error("Failed to send welcome email (will not retry): %s", exc)
