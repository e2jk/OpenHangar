"""Opt-in, edition-specific expiry alerts for accepted participants of any role."""

import os
from datetime import date

from flask import current_app
from flask_babel import lazy_gettext as _l
from models import (
    NotificationType,
    Passport,
    PassportParticipant,
    TenantUser,
    User,
    db,
)
from services.notification_service import dispatch, get_effective_preference


def has_participation(tenant_id: int, user_id: int) -> bool:
    return (
        db.session.query(PassportParticipant.id)
        .join(Passport, Passport.id == PassportParticipant.passport_id)
        .filter(
            Passport.tenant_id == tenant_id,
            PassportParticipant.user_id == user_id,
            PassportParticipant.is_active.is_(True),
            PassportParticipant.blocked.is_(False),
            PassportParticipant.accepted_at.is_not(None),
        )
        .first()
        is not None
    )


def check_expiry() -> None:
    today = date.today()
    candidates = (
        db.session.query(Passport, PassportParticipant.user_id)
        .join(PassportParticipant, PassportParticipant.passport_id == Passport.id)
        .join(User, User.id == PassportParticipant.user_id)
        .join(
            TenantUser,
            (TenantUser.user_id == User.id)
            & (TenantUser.tenant_id == Passport.tenant_id),
        )
        .filter(
            Passport.archived_at.is_(None),
            Passport.end_date >= today,
            db.or_(Passport.start_date.is_(None), Passport.start_date <= today),
            PassportParticipant.is_active.is_(True),
            PassportParticipant.blocked.is_(False),
            PassportParticipant.accepted_at.is_not(None),
            User.is_active.is_(True),
        )
        .all()
    )
    for passport, user_id in candidates:
        pref = get_effective_preference(
            user_id, passport.tenant_id, NotificationType.PASSPORT_EXPIRING
        )
        days_left = (passport.end_date - today).days
        threshold = pref["threshold_days"] or 30
        if not pref["enabled"] or days_left > threshold:
            continue
        path = current_app.url_map.bind("localhost").build(
            "passports.detail", {"passport_id": passport.id}
        )
        base = os.environ.get("OPENHANGAR_INSTANCE_URL", "").strip().rstrip("/")
        dispatch(
            NotificationType.PASSPORT_EXPIRING,
            passport.tenant_id,
            {
                "subject_key": _l("Passport edition expiring: %(title)s"),
                "subject_args": {"title": passport.title},
                "notification_title_key": _l("Passport edition expiring"),
                "notification_message_key": _l(
                    "Your passport %(title)s ends on %(date)s. Open the edition to see your remaining airfields and vouchers."
                ),
                "notification_message_args": {
                    "title": passport.title,
                    "date": passport.end_date.isoformat(),
                },
                "cta_url": base + path,
                "cta_label": _l("Open passport"),
                "expiry_value": passport.end_date.isoformat(),
            },
            target_user_ids=[user_id],
            subject_ref=f"passport:{passport.id}",
        )
