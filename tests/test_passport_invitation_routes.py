"""Invitation previews, owner controls and account-bound login/2FA hand-off."""

from typing import Any
from unittest.mock import patch

import pw_hash
import pyotp
import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    AuthSession,
    Passport,
    PassportInvitation,
    PassportParticipant,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from passports import invitations
from passports.management import BuilderError
from services import auth_challenges

from tests.auth_helpers import challenge_payload
from tests.test_passport_builder import signin
from tests.test_passport_groups import consent_values
from tests.test_passport_invitations import invited, raw_token
from tests.test_passport_management import owner

__all__ = ["invited", "owner"]
PASSWORD = "A real friend password 123!"


@pytest.fixture
def friend(app: Flask, invited: dict[str, int]) -> dict[str, int]:
    with app.app_context():
        user = db.session.get(User, invited["other"])
        user.password_hash = pw_hash.hash(PASSWORD)
        db.session.delete(db.session.get(TenantUser, (user.id, invited["tenant"])))
        home = Tenant(name="Existing pilot organisation")
        db.session.add(home)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=home.id, role=Role.PILOT))
        db.session.commit()
        return invited | {"home": home.id}


def join_url(app: Flask, ids: dict[str, int]) -> str:
    with app.app_context():
        return "/passports/join/" + raw_token(db.session.get(Passport, ids["passport"]))


def credentials(client: FlaskClient) -> Any:
    return client.post(
        "/login", data={"email": "other@test.example", "password": PASSWORD}
    )


def assert_private(response: Any) -> None:
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_preview_creates_no_membership_or_identity(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    url = join_url(app, friend)
    page = client.get(url)
    assert page.status_code == 200 and b"Local fields" in page.data
    assert b"hx-history" in page.data
    assert_private(page)
    with app.app_context():
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0
        assert db.session.get(TenantUser, (friend["other"], friend["tenant"])) is None
        assert User.query.count() == 2
    assert client.post(url, data={"display_name": "Friend"}).status_code == 400
    invalid = client.get("/passports/join/not-a-token")
    assert invalid.status_code == 404
    assert_private(invalid)


def test_signin_automatically_joins_specific_edition_without_role_promotion(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    url = join_url(app, friend)
    assert client.post(url, data=consent_values("Friend")).location.endswith("/login")
    page = client.get("/login")
    assert b"Sign in to join Local fields" in page.data
    assert_private(page)
    with client.session_transaction() as sess:
        sess["untrusted_next"] = "https://example.test/"
    result = credentials(client)
    assert result.status_code == 302
    assert f"/passports/{friend['passport']}/" in result.location
    assert_private(result)
    with client.session_transaction() as sess:
        assert invitations.JOIN_KEY not in sess and "untrusted_next" not in sess
        token = sess["auth_session"]
    with app.app_context():
        import hashlib

        assert (
            db.session.get(
                AuthSession, hashlib.sha256(token.encode()).hexdigest()
            ).tenant_id
            == friend["tenant"]
        )
        assert (
            db.session.get(TenantUser, (friend["other"], friend["tenant"])).role is None
        )
        assert (
            db.session.get(TenantUser, (friend["other"], friend["home"])).role
            == Role.PILOT
        )
    assert client.get(result.location).status_code == 200
    assert client.get("/aircraft/").status_code == 403
    assert client.get(url).status_code == 302


def test_signed_in_join_is_one_action_and_already_enrolled_opens_map(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    signin(client, friend["other"])
    url = join_url(app, friend)
    assert b"Join this passport" in client.get(url).data
    result = client.post(url, data=consent_values("Already signed in"))
    assert f"/passports/{friend['passport']}/" in result.location
    assert client.get(url).location == result.location
    assert client.get(f"/passports/{friend['passport']}/invitation").status_code == 404


def test_owner_copy_replace_disable_and_errors(
    app: Flask, client: FlaskClient, invited: dict[str, int]
) -> None:
    signin(client, invited["user"])
    path = f"/passports/{invited['passport']}/invitation"
    old_url = join_url(app, invited)
    page = client.get(path)
    assert page.status_code == 200 and old_url.encode() in page.data
    assert_private(page)
    assert (
        client.post(
            path, data={"action": "replace", "expires_on": "not-a-date"}
        ).status_code
        == 400
    )
    assert client.post(path, data={"action": "forged"}).status_code == 400
    assert client.post(path, data={"action": "replace"}).status_code == 302
    assert client.get(old_url).status_code == 404
    assert client.post(path, data={"action": "disable"}).status_code == 302
    assert b"No active invitation" in client.get(path).data
    with app.app_context():
        db.session.delete(db.session.get(PassportInvitation, invited["passport"]))
        db.session.commit()
    assert client.post(path, data={"action": "disable"}).status_code == 302
    signin(client, invited["other"])
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("mode", ["totp", "totp-enrol"])
def test_target_organisation_mfa_before_join(
    app: Flask, client: FlaskClient, friend: dict[str, int], mode: str
) -> None:
    secret = pyotp.random_base32()
    with app.app_context():
        db.session.get(Tenant, friend["tenant"]).require_totp = True
        if mode == "totp":
            db.session.get(User, friend["other"]).totp_secret = secret
        db.session.commit()
    client.post(join_url(app, friend), data=consent_values("MFA friend"))
    result = credentials(client)
    assert result.location.endswith("step=" + mode)
    payload = challenge_payload(client, "login_challenge")
    if mode == "totp-enrol":
        assert payload["tenant_id"] == friend["tenant"]
        secret = payload["secret"]
    assert b"Local fields" in client.get(result.location).data
    with app.app_context():
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0
    code = pyotp.TOTP(secret).now()
    result = client.post("/login", data={"step": mode, "totp_code": code})
    assert f"/passports/{friend['passport']}/" in result.location
    page = client.get(result.location)
    assert page.status_code == 200
    if mode == "totp-enrol":
        assert b"Two-factor authentication is now active" in page.data


def test_replaced_invitation_during_auth_never_enrols(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    client.post(join_url(app, friend), data=consent_values())
    with app.app_context():
        invitations.replace(db.session.get(Passport, friend["passport"]))
        db.session.commit()
    result = credentials(client)
    assert result.location.startswith("/?")
    assert (
        b"This passport invitation is not available" in client.get(result.location).data
    )
    with app.app_context():
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0
        assert db.session.get(TenantUser, (friend["other"], friend["tenant"])) is None
    with client.session_transaction() as sess:
        assert invitations.JOIN_KEY not in sess


def test_retryable_conflict_retains_only_join_context(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    client.post(join_url(app, friend), data=consent_values())
    with patch("passports.invitations.complete", side_effect=BuilderError("busy", 409)):
        result = credentials(client)
    assert result.status_code == 302
    with client.session_transaction() as sess:
        assert invitations.JOIN_KEY in sess
    with app.app_context():
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0


def test_signed_in_friend_requires_target_mfa_without_enrolling_early(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    signin(client, friend["other"])
    with app.app_context():
        db.session.get(Tenant, friend["tenant"]).require_totp = True
        db.session.commit()
    result = client.post(join_url(app, friend), data=consent_values())
    assert result.location.endswith("/login")
    with client.session_transaction() as sess:
        assert "user_id" not in sess and invitations.JOIN_KEY in sess
    with app.app_context():
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0


def test_explicit_mfa_policy_handles_absent_and_inactive_target(
    app: Flask, friend: dict[str, int]
) -> None:
    with app.app_context():
        user = db.session.get(User, friend["other"])
        assert not auth_challenges.requires_totp(user, tenant_id=999999)
        tenant = db.session.get(Tenant, friend["tenant"])
        tenant.require_totp, tenant.is_active = True, False
        assert not auth_challenges.requires_totp(user, tenant_id=tenant.id)


def test_existing_member_reauthenticates_for_stronger_target_policy(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, friend["passport"])
        row = db.session.get(PassportInvitation, p.id)
        invitations.join(friend["other"], row.token_hash, p.id, consent_values())
        db.session.commit()
        db.session.get(Tenant, friend["tenant"]).require_totp = True
        db.session.commit()
    signin(client, friend["other"])
    result = client.get(join_url(app, friend))
    assert result.location.endswith("/login")
    with client.session_transaction() as sess:
        assert invitations.JOIN_KEY in sess and "user_id" not in sess
    assert credentials(client).location.endswith("step=totp-enrol")


def test_service_refuses_join_without_required_factor(
    app: Flask, friend: dict[str, int]
) -> None:
    with app.app_context():
        db.session.get(Tenant, friend["tenant"]).require_totp = True
        row = db.session.get(PassportInvitation, friend["passport"])
        with pytest.raises(BuilderError, match="two-factor"):
            invitations.join(
                friend["other"], row.token_hash, row.passport_id, consent_values()
            )
        assert PassportParticipant.query.filter_by(user_id=friend["other"]).count() == 0


def test_login_finish_rechecks_target_mfa_and_expected_account(
    app: Flask, friend: dict[str, int]
) -> None:
    from auth.routes import _finish_login, _invitation_tenant
    from flask import session

    with app.test_request_context():
        user = db.session.get(User, friend["other"])
        p = db.session.get(Passport, friend["passport"])
        invitations.prepare(raw_token(p), consent_values())
        db.session.get(Tenant, friend["tenant"]).require_totp = True
        db.session.commit()
        assert _finish_login(user).location.endswith("step=totp-enrol")
        challenge, _ = invitations.pending()
        challenge.payload = {**challenge.payload, "expected_user_id": friend["user"]}
        db.session.commit()
        assert _invitation_tenant(user) is None
        session.pop("login_challenge", None)
        assert _finish_login(user).location.startswith("/?")
        assert invitations.JOIN_KEY not in session
        assert PassportParticipant.query.filter_by(user_id=user.id).count() == 0


def test_join_csrf_and_shared_rate_limit(
    app: Flask,
    client: FlaskClient,
    friend: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = join_url(app, friend)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    assert client.post(url, data=consent_values()).status_code == 400
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", True)
    statuses = [client.post(url, data=consent_values()).status_code for _ in range(11)]
    assert statuses == [302] * 10 + [429]
    assert_private(client.get(url))


def test_global_admin_without_org_cannot_manage_invitation(
    app: Flask, client: FlaskClient, invited: dict[str, int]
) -> None:
    with app.app_context():
        admin = User(
            email="global@example.test", password_hash="unused", is_instance_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        uid = admin.id
    signin(client, uid)
    assert client.get(f"/passports/{invited['passport']}/invitation").status_code == 403
