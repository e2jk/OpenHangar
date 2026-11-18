"""Edition expiry alerts honour participation, preferences, dates and deduplication."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    NotificationPreference,
    Passport,
    PassportParticipant,
    TenantUser,
    User,
    db,
)
from models import (
    NotificationType as NT,
)
from passports.reminders import check_expiry, has_participation
from services.notification_service import _find_recipients

from tests.test_passport_builder import signin
from tests.test_passport_management import owner

__all__ = ["owner"]


def enable(user_id: int, tenant_id: int, threshold: int | None = 30) -> None:
    db.session.add(
        NotificationPreference(
            user_id=user_id,
            tenant_id=tenant_id,
            notification_type=NT.PASSPORT_EXPIRING,
            enabled=True,
            threshold_days=threshold,
        )
    )
    db.session.commit()


def test_opt_in_thresholds_and_safe_edition_link(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.end_date = date.today() + timedelta(days=45)
        db.session.commit()
        with patch("passports.reminders.dispatch") as send:
            check_expiry()
            send.assert_not_called()
            enable(owner["user"], owner["tenant"], 60)
            check_expiry()
            kind, tenant, context = send.call_args.args
            assert kind == NT.PASSPORT_EXPIRING and tenant == owner["tenant"]
            assert context["cta_url"] == f"/passports/{p.id}/"
            assert context["expiry_value"] == p.end_date.isoformat()
            assert send.call_args.kwargs["target_user_ids"] == [owner["user"]]
            send.reset_mock()
            pref = NotificationPreference.query.one()
            pref.threshold_days = None
            db.session.commit()
            check_expiry()
            send.assert_not_called()
            p.end_date = date.today()
            db.session.commit()
            with patch.dict(
                "os.environ", {"OPENHANGAR_INSTANCE_URL": "https://example.test/"}
            ):
                check_expiry()
            assert send.call_args.args[2]["cta_url"].startswith(
                "https://example.test/passports/"
            )


@pytest.mark.parametrize(
    "condition",
    [
        "archived",
        "future",
        "expired",
        "undated",
        "inactive",
        "unaccepted",
        "disabled_user",
        "left_tenant",
    ],
)
def test_no_alert_without_live_edition_participation(
    app: Flask, owner: dict[str, int], condition: str
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.end_date = date.today() + timedelta(days=5)
        member = PassportParticipant.query.filter_by(passport_id=p.id).one()
        user = db.session.get(User, owner["user"])
        if condition == "archived":
            p.archived_at = datetime.now(UTC)
        elif condition == "future":
            p.start_date = date.today() + timedelta(days=1)
        elif condition == "expired":
            p.end_date = date.today() - timedelta(days=1)
        elif condition == "undated":
            p.end_date = None
        elif condition == "inactive":
            member.is_active = False
        elif condition == "unaccepted":
            member.accepted_at = None
        elif condition == "disabled_user":
            user.is_active = False
        else:
            db.session.delete(TenantUser.query.filter_by(user_id=user.id).one())
        enable(user.id, owner["tenant"])
        with patch("passports.reminders.dispatch") as send:
            check_expiry()
            send.assert_not_called()


def test_viewer_participant_capability_and_no_automatic_sharing(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        other = owner["other"]
        assert not has_participation(owner["tenant"], other)
        assert [
            u.id for u in _find_recipients(NT.PASSPORT_EXPIRING, owner["tenant"])
        ] == [owner["user"]]
    signin(client, other)
    assert b"enabled_passport_expiring" not in client.get("/config/notifications/").data
    with app.app_context():
        db.session.add(
            PassportParticipant(
                passport_id=owner["passport"],
                user_id=other,
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
        assert has_participation(owner["tenant"], other)
        assert [
            u.id
            for u in _find_recipients(NT.PASSPORT_EXPIRING, owner["tenant"], [other])
        ] == [other]
    page = client.get("/config/notifications/")
    assert b"enabled_passport_expiring" in page.data
    assert b"Off by default" in page.data
    client.post(
        "/config/notifications/",
        data={"enabled_passport_expiring": "on", "threshold_passport_expiring": "7"},
    )
    with app.app_context():
        pref = NotificationPreference.query.filter_by(
            user_id=other, notification_type=NT.PASSPORT_EXPIRING
        ).one()
        assert pref.enabled and pref.threshold_days == 7


def test_real_delivery_is_deduplicated_and_revoked(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.end_date = date.today() + timedelta(days=2)
        enable(owner["user"], owner["tenant"])
        with patch("services.email_service.send_email") as send:
            check_expiry()
            check_expiry()
            assert send.call_count == 1
            assert f"/passports/{p.id}/" in send.call_args.kwargs["text_body"]
            PassportParticipant.query.filter_by(
                passport_id=p.id
            ).one().is_active = False
            db.session.commit()
            check_expiry()
            assert send.call_count == 1


@pytest.mark.parametrize(
    "days,label", [(-1, b"Expired"), (0, b"Not expired"), (10, b"Not expired")]
)
def test_expiry_status_and_preferences_link(
    app: Flask, client: FlaskClient, owner: dict[str, int], days: int, label: bytes
) -> None:
    with app.app_context():
        db.session.get(Passport, owner["passport"]).end_date = date.today() + timedelta(
            days=days
        )
        db.session.commit()
    signin(client, owner["user"])
    page = client.get(f"/passports/{owner['passport']}/")
    assert page.status_code == 200
    assert label in page.data and b"Manage expiry reminders" in page.data
