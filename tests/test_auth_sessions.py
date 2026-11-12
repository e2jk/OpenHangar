"""Actual-cookie regressions for session revocation and current principal checks."""

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from models import AuthSession, PasswordResetToken, Role, Tenant, TenantUser, User, db

from tests.auth_helpers import challenge_payload
from tests.test_auth_challenges import PASSWORD, begin, make_user, replay, saved_cookie


def logged_in(app):
    client = app.test_client()
    assert begin(client).status_code == 302
    assert client.get("/profile").status_code == 200
    return client


def test_logout_revokes_saved_cookie_without_revoking_other_browser(app):
    make_user(app, required=False)
    client = logged_in(app)
    other = logged_in(app)
    cookie = saved_cookie(client)
    client.get("/logout")
    assert replay(app, cookie).get("/profile").status_code == 302
    assert other.get("/profile").status_code == 200


def test_password_change_keeps_current_browser_and_revokes_others(app):
    make_user(app, required=False)
    client = logged_in(app)
    other = logged_in(app)
    cookie = saved_cookie(client)
    response = client.post(
        "/profile",
        data={
            "action": "change_password",
            "current_password": PASSWORD,
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        },
    )
    assert response.status_code == 302
    assert client.get("/profile").status_code == 200
    assert other.get("/profile").status_code == 302
    assert replay(app, cookie).get("/profile").status_code == 302


@pytest.mark.parametrize(
    "change",
    ["user", "tenant", "membership", "deleted", "expired", "token", "identity"],
)
def test_invalid_logins_cannot_read_or_write_even_after_reactivation(app, change):
    uid, tid = make_user(app, required=False)
    client = logged_in(app)
    cookie = saved_cookie(client)
    with app.app_context():
        user = db.session.get(User, uid)
        tenant = db.session.get(Tenant, tid)
        if change == "user":
            user.is_active = False
            db.session.commit()
            user.is_active = True
        elif change == "tenant":
            tenant.is_active = False
            db.session.commit()
            tenant.is_active = True
        elif change == "membership":
            db.session.delete(db.session.get(TenantUser, (uid, tid)))
            db.session.commit()
            db.session.add(TenantUser(user_id=uid, tenant_id=tid, role=Role.ADMIN))
        elif change == "deleted":
            db.session.delete(user)
        elif change == "expired":
            AuthSession.query.update({"expires_at": 0})
        elif change == "token":
            AuthSession.query.delete()
        else:
            # Use a legitimate different account rather than an impossible FK.
            other = User(email="other@example.com", password_hash="unused")
            db.session.add(other)
            db.session.flush()
            AuthSession.query.update({"user_id": other.id})
        db.session.commit()
    response = replay(app, cookie).get("/pilot/logbook")
    assert response.status_code == 302
    assert replay(app, cookie).get("/api/offline/csrf").status_code == 401
    response = replay(app, cookie).post(
        "/profile", data={"action": "update_name", "name": "Unauthorized"}
    )
    assert response.status_code == 302
    with app.app_context():
        user = db.session.get(User, uid)
        assert user is None or user.name != "Unauthorized"


def test_tenant_inactive_now_is_denied_and_instance_admin_keeps_recovery_access(app):
    uid, tid = make_user(app, required=False)
    client = logged_in(app)
    with app.app_context():
        db.session.get(Tenant, tid).is_active = False
        db.session.commit()
    assert client.get("/profile").status_code == 302
    with app.app_context():
        user = db.session.get(User, uid)
        user.is_instance_admin = True
        db.session.commit()
    admin = logged_in(app)
    with app.app_context():
        tenant = db.session.get(Tenant, tid)
        tenant.is_active = True
        db.session.commit()
        tenant.is_active = False
        db.session.commit()
    assert admin.get("/config/tenants").status_code == 200


def test_legacy_cookie_is_not_upgraded_to_a_valid_login(app):
    uid, _ = make_user(app, required=False)
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": uid})
    assert replay(app, cookie).get("/profile").status_code == 302
    with app.app_context():
        assert AuthSession.query.count() == 0


def test_password_reset_revokes_sessions_and_pending_challenges(app):
    uid, _ = make_user(app, required=False)
    client = logged_in(app)
    with app.app_context():
        reset = PasswordResetToken(
            user_id=uid,
            generated_by_user_id=uid,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.session.add(reset)
        db.session.commit()
        token = reset.token
    response = app.test_client().post(
        f"/reset-password/{token}",
        data={
            "new_password": "ResetPassword123!",
            "confirm_password": "ResetPassword123!",
        },
    )
    assert response.status_code == 302
    assert client.get("/profile").status_code == 302


def test_totp_enrolment_and_disable_revoke_other_sessions(app):
    make_user(app, required=False)
    client = logged_in(app)
    other = logged_in(app)
    client.post("/profile", data={"action": "setup_totp"})
    secret = challenge_payload(client, "profile_challenge")["secret"]
    client.post(
        "/profile",
        data={"action": "confirm_totp", "totp_code": pyotp.TOTP(secret).now()},
    )
    assert client.get("/profile").status_code == 200
    assert other.get("/profile").status_code == 302
    old_cookie = saved_cookie(client)
    client.post(
        "/profile", data={"action": "disable_totp", "current_password": PASSWORD}
    )
    assert client.get("/profile").status_code == 200
    assert replay(app, old_cookie).get("/profile").status_code == 302


def test_role_downgrade_is_observed_without_relogin(app):
    uid, tid = make_user(app, required=False)
    client = logged_in(app)
    with app.app_context():
        db.session.get(TenantUser, (uid, tid)).role = Role.VIEWER
        db.session.commit()
    assert client.get("/config/users/").status_code == 403


@pytest.mark.parametrize(
    "blueprint",
    [
        "aircraft",
        "documents",
        "expenses",
        "flights",
        "maintenance",
        "offline",
        "refuels",
        "reports",
        "snags",
    ],
)
def test_ownership_helpers_still_fail_closed_if_membership_disappears_midrequest(
    app, blueprint
):
    """Keep a second guard if deletion races the central request validation."""
    import importlib

    from flask import session
    from werkzeug.exceptions import Forbidden

    module = importlib.import_module(f"{blueprint}.routes")
    with app.test_request_context():
        session["user_id"] = 99999
        with pytest.raises(Forbidden):
            module._tenant_id()


def test_user_disappearing_after_validation_is_handled_by_profile_and_preferences(app):
    from flask import session
    from werkzeug.exceptions import Forbidden

    with app.test_request_context():
        session["user_id"] = 99999
        response = app.view_functions["auth.profile"].__wrapped__()
        assert response.location.endswith("/logout")
        with pytest.raises(Forbidden):
            app.view_functions["config.notification_preferences"].__wrapped__()


@pytest.mark.parametrize(
    "endpoint,argument",
    [("set_language", {"lang": "fr"}), ("set_theme", {"theme": "dark"})],
)
def test_preference_switch_race_keeps_public_session_fallback(app, endpoint, argument):
    from flask import session

    with app.test_request_context():
        session["user_id"] = 99999
        response = app.view_functions[endpoint](**argument)
        assert response.status_code == 302
        key = "language" if endpoint == "set_language" else "theme"
        assert session[key] == next(iter(argument.values()))
