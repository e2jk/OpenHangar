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
    client.post("/profile", data={"action": "setup_totp", "current_password": PASSWORD})
    secret = challenge_payload(client, "profile_challenge")["secret"]
    client.post(
        "/profile",
        data={"action": "confirm_totp", "totp_code": pyotp.TOTP(secret).now()},
    )
    assert client.get("/profile").status_code == 200
    assert other.get("/profile").status_code == 302
    old_cookie = saved_cookie(client)
    client.post(
        "/profile",
        data={
            "action": "disable_totp",
            "current_password": PASSWORD,
            "current_totp_code": pyotp.TOTP(secret).now(),
        },
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


def _rehash_on_next_login(monkeypatch):
    calls = []

    def needs_rehash(_hash):
        calls.append(_hash)
        return len(calls) == 1

    monkeypatch.setattr("pw_hash.needs_rehash", needs_rehash)
    return calls


def test_login_rehash_keeps_other_logins_and_auth_version(app, monkeypatch):
    uid, _ = make_user(app, required=False)
    other = logged_in(app)
    with app.app_context():
        before = db.session.get(User, uid)
        old_hash, old_version = before.password_hash, before.auth_version
    calls = _rehash_on_next_login(monkeypatch)
    client = logged_in(app)
    assert calls, "the login must have checked for a re-hash"
    with app.app_context():
        after = db.session.get(User, uid)
        assert after.password_hash != old_hash
        assert after.auth_version == old_version
    assert other.get("/profile").status_code == 200
    assert client.get("/profile").status_code == 200


def test_password_change_after_rehash_still_revokes_other_logins(app, monkeypatch):
    make_user(app, required=False)
    other = logged_in(app)
    _rehash_on_next_login(monkeypatch)
    client = logged_in(app)
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


def test_rehash_flag_does_not_cover_other_security_changes(app):
    from models import SAME_PASSWORD_REHASH

    uid, _ = make_user(app, required=False)
    with app.app_context():
        user = db.session.get(User, uid)
        old_version = user.auth_version
        db.session.info.setdefault(SAME_PASSWORD_REHASH, set()).add(uid)
        user.password_hash = user.password_hash + "x"
        user.is_active = False
        db.session.commit()
        assert user.auth_version != old_version
        assert uid not in db.session.info[SAME_PASSWORD_REHASH]


def _sets_session_cookie(response):
    return any(
        header.startswith("session=")
        for header in response.headers.getlist("Set-Cookie")
    )


def test_request_with_revoked_cookie_cannot_overwrite_the_renewed_one(app):
    """A request still in flight with the old cookie when the password changed
    must not answer with a cleared cookie that would sign the browser out."""
    make_user(app, required=False)
    client = logged_in(app)
    old_cookie = saved_cookie(client)
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
    stale = replay(app, old_cookie).get("/profile")
    assert stale.status_code == 302  # still handled as signed out
    assert not _sets_session_cookie(stale)
    assert client.get("/profile").status_code == 200


def test_revoked_cookie_is_still_cleared_so_the_browser_can_sign_in(app):
    """Only a just-renewed login keeps its cookie untouched; a revoked one (here
    logged out) is cleared as before, so the browser is not stuck with it."""
    make_user(app, required=False)
    client = logged_in(app)
    old_cookie = saved_cookie(client)
    client.get("/logout")
    browser = replay(app, old_cookie)
    login_page = browser.get("/login")
    assert _sets_session_cookie(login_page)
    assert begin(browser).status_code == 302
    assert browser.get("/profile").status_code == 200


def test_renewed_cookie_is_cleared_again_once_its_grace_period_ends(app):
    make_user(app, required=False)
    client = logged_in(app)
    old_cookie = saved_cookie(client)
    client.post(
        "/profile",
        data={
            "action": "change_password",
            "current_password": PASSWORD,
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        },
    )
    with app.app_context():
        assert AuthSession.query.filter_by(credential_binding="renewed").count() == 1
        AuthSession.query.filter_by(credential_binding="renewed").update(
            {"expires_at": 0}
        )
        db.session.commit()
    stale = replay(app, old_cookie).get("/profile")
    assert stale.status_code == 302
    assert _sets_session_cookie(stale)  # cleared: this browser can sign in again


def test_response_for_a_token_renewed_meanwhile_does_not_resend_it(app):
    """A request that was validated before the browser's login was renewed, but
    responds after, must not put the old cookie back over the new one."""
    from flask import Response

    make_user(app, required=False)
    client = logged_in(app)
    old_cookie = saved_cookie(client)
    client.post(
        "/profile",
        data={
            "action": "change_password",
            "current_password": PASSWORD,
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!",
        },
    )
    with app.test_request_context("/", headers={"Cookie": f"session={old_cookie}"}):
        from flask import request

        interface = app.session_interface
        old_session = interface.open_session(app, request)
        assert old_session.get("auth_session")  # the in-flight request's view
        response = Response()
        interface.save_session(app, old_session, response)
        assert not _sets_session_cookie(response)
