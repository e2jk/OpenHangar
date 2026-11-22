"""A passport login binds roles, data and browser storage to its selected membership."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pw_hash
import pyotp
import pytest
from flask import Flask, g, session
from flask.testing import FlaskClient
from models import (
    Aircraft,
    Passport,
    PassportParticipant,
    PermissionBit,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    UserAllAircraftAccess,
    db,
)
from services import auth_sessions
from services.authorization import AuthorizationService
from services.offline_identity import OWNER_HEADER, SESSION_HEADER, identity
from utils import accessible_aircraft, current_membership, user_can_access_aircraft

PASSWORD = "Passport-context-password-123"


@pytest.fixture
def memberships(app: Flask) -> Iterator[dict[str, int]]:
    with app.app_context():
        # The passport organisation is older, but must not steal the full-account default.
        guest = Tenant(name="Passport group")
        db.session.add(guest)
        db.session.flush()
        home = Tenant(name="Existing pilot organisation")
        user = User(
            email="context@example.test",
            password_hash=pw_hash.hash(PASSWORD),
            name="Context pilot",
            is_pilot=True,
        )
        db.session.add_all([home, user])
        db.session.flush()
        db.session.add_all(
            [
                TenantUser(user_id=user.id, tenant_id=home.id, role=Role.OWNER),
                TenantUser(
                    user_id=user.id,
                    tenant_id=guest.id,
                    role=None,
                    access_scope="passport",
                ),
            ]
        )
        planes = [
            Aircraft(tenant_id=t.id, registration=reg, make="Test", model="Test")
            for t, reg in [(home, "HOME-PRIVATE"), (guest, "GUEST-PRIVATE")]
        ]
        passports = [
            Passport(tenant_id=t.id, owner_user_id=user.id, title=title)
            for t, title in [(home, "Home passport"), (guest, "Group passport")]
        ]
        db.session.add_all(planes + passports)
        db.session.flush()
        for p in passports:
            db.session.add(
                PassportParticipant(
                    passport_id=p.id,
                    user_id=user.id,
                    display_name="Pilot",
                    accepted_at=datetime.now(UTC),
                )
            )
        db.session.commit()
        result = {
            "user": user.id,
            "home": home.id,
            "guest": guest.id,
            "home_plane": planes[0].id,
            "guest_plane": planes[1].id,
            "home_passport": passports[0].id,
            "guest_passport": passports[1].id,
        }

    yield result


def in_context(app: Flask, user_id: int, tenant_id: int | None = None) -> FlaskClient:
    client = app.test_client()
    with app.test_request_context():
        user = db.session.get(User, user_id)
        auth_sessions.start(user, tenant_id=tenant_id)
        cookie = app.session_interface.get_signing_serializer(app).dumps(dict(session))
    client.set_cookie("session", cookie)
    return client


def test_roleless_context_keeps_other_full_membership_and_default(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    guest = in_context(app, m["user"], m["guest"])
    page = guest.get("/passports/")
    assert (
        page.status_code == 200
        and b"Group passport" in page.data
        and b"Home passport" not in page.data
    )
    assert guest.get(f"/passports/{m['home_passport']}/").status_code == 404
    assert (
        guest.get(
            f"/passports/{m['guest_passport']}/?tenant_id={m['home']}"
        ).status_code
        == 200
    )
    assert guest.get("/aircraft/").status_code == 403
    assert guest.get("/config/").status_code == 403
    with app.app_context():
        assert db.session.get(TenantUser, (m["user"], m["home"])).role == Role.OWNER
    home = in_context(app, m["user"])
    assert home.get("/config/").status_code == 200
    assert b"Home passport" in home.get("/passports/").data
    assert home.get(f"/passports/{m['guest_passport']}/").status_code == 404


def test_context_owns_cache_and_renewal_namespace(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    with app.test_request_context():
        user = db.session.get(User, m["user"])
        auth_sessions.start(user, tenant_id=m["home"])
        first = identity()
        assert current_membership().tenant_id == m["home"]
        # An arbitrary cookie hint is never a context selector.
        session["tenant_id"] = m["home"]
        auth_sessions.start(user, tenant_id=m["guest"])
        second = identity()
        assert (
            current_membership().tenant_id == m["guest"]
            and current_membership().role is None
        )
        auth_sessions.start(user)
        assert current_membership().tenant_id == m["guest"]
        third = identity()
        assert first[0] != second[0] and second[0] == third[0]
        assert len({first[1], second[1], third[1]}) == 3
        assert g.authenticated_tenant_id == m["guest"]


def test_password_change_preserves_selected_context_and_revokes_other_logins(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    home = in_context(app, m["user"], m["home"])
    guest = in_context(app, m["user"], m["guest"])
    before = guest.get("/profile")
    response = guest.post(
        "/profile",
        data={
            "action": "change_password",
            "current_password": PASSWORD,
            "new_password": "Another-password-123",
            "confirm_password": "Another-password-123",
        },
    )
    assert response.status_code == 302
    after = guest.get("/profile")
    assert (
        after.status_code == 200
        and after.headers[OWNER_HEADER] == before.headers[OWNER_HEADER]
    )
    assert after.headers[SESSION_HEADER] != before.headers[SESSION_HEADER]
    assert guest.get(f"/passports/{m['guest_passport']}/").status_code == 200
    assert guest.get("/config/").status_code == 403
    assert home.get("/profile").status_code == 302


@pytest.mark.parametrize("change", ["membership", "inactive", "mandatory_mfa"])
def test_bound_context_revocation_never_falls_back_to_full_account(
    app: Flask, memberships: dict[str, int], change: str
) -> None:
    m = memberships
    client = in_context(app, m["user"], m["guest"])
    assert client.get("/profile").status_code == 200
    with app.app_context():
        if change == "membership":
            db.session.delete(db.session.get(TenantUser, (m["user"], m["guest"])))
        elif change == "inactive":
            db.session.get(Tenant, m["guest"]).is_active = False
        else:
            db.session.get(Tenant, m["guest"]).require_totp = True
        db.session.commit()
    response = client.get("/profile")
    assert response.status_code == 302 and "/login" in response.location


def test_invalid_context_does_not_retire_current_cookie(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    with app.test_request_context():
        user = db.session.get(User, m["user"])
        auth_sessions.start(user, tenant_id=m["home"])
        token = session["auth_session"]
        with pytest.raises(ValueError, match="active membership"):
            auth_sessions.start(user, tenant_id=999999)
        assert session["auth_session"] == token and auth_sessions.validate()
        db.session.get(Tenant, m["guest"]).is_active = False
        db.session.commit()
        with pytest.raises(ValueError):
            auth_sessions.issue(user, tenant_id=m["guest"])


def test_service_permissions_deny_scoped_and_foreign_aircraft_even_with_grants(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    with app.app_context():
        db.session.add(
            UserAllAircraftAccess(
                user_id=m["user"],
                tenant_id=m["guest"],
                permissions_mask=PermissionBit.ALL,
            )
        )
        db.session.commit()
        assert (
            AuthorizationService.effective_mask(m["user"], m["guest_plane"], m["guest"])
            == 0
        )
        assert (
            AuthorizationService.effective_mask(m["user"], m["guest_plane"], m["home"])
            == 0
        )
        assert AuthorizationService.effective_mask(m["user"], 999999, m["home"]) == 0
    with app.test_request_context():
        user = db.session.get(User, m["user"])
        auth_sessions.start(user, tenant_id=m["guest"])
        assert not AuthorizationService.can(m["user"], "view_aircraft", m["home_plane"])
        assert not user_can_access_aircraft(m["home_plane"])
        assert accessible_aircraft(m["home"]).all() == []
        assert accessible_aircraft(m["guest"]).all() == []
        session.clear()
        assert not AuthorizationService.can(m["user"], "view_aircraft", m["home_plane"])


def test_full_context_queries_and_roles_agree_across_feature_helpers(
    app: Flask, memberships: dict[str, int]
) -> None:
    from documents.routes import _current_role
    from pilots.routes import _pilot_tenant_id
    from pwa.routes import _get_user_aircraft
    from reservations.routes import _is_owner_role

    m = memberships
    with app.app_context():
        tu = db.session.get(TenantUser, (m["user"], m["guest"]))
        tu.access_scope, tu.role = "full", Role.VIEWER
        # A viewer sees only the aircraft they were given access to.
        db.session.add(
            UserAircraftAccess(user_id=m["user"], aircraft_id=m["guest_plane"])
        )
        db.session.commit()
    with app.test_request_context():
        user = db.session.get(User, m["user"])
        auth_sessions.start(user, tenant_id=m["guest"])
        assert _current_role() == Role.VIEWER
        assert _pilot_tenant_id(m["user"]) == m["guest"]
        assert [a.id for a in _get_user_aircraft()] == [m["guest_plane"]]
        assert not _is_owner_role(m["user"], m["guest"])
        assert _is_owner_role(m["user"], m["home"])
        assert not user_can_access_aircraft(m["home_plane"])
        assert accessible_aircraft(m["home"]).all() == []


def test_default_login_uses_active_membership_and_required_two_factor(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    with app.app_context():
        db.session.get(Tenant, m["home"]).is_active = False
        db.session.get(Tenant, m["guest"]).require_totp = True
        db.session.commit()
    client = app.test_client()
    response = client.post(
        "/login", data={"email": "context@example.test", "password": PASSWORD}
    )
    assert response.status_code == 302 and "totp-enrol" in response.location
    from tests.auth_helpers import challenge_payload

    payload = challenge_payload(client, "login_challenge")
    response = client.post(
        "/login",
        data={"step": "totp-enrol", "totp_code": pyotp.TOTP(payload["secret"]).now()},
    )
    assert response.status_code == 302
    assert client.get(f"/passports/{m['guest_passport']}/").status_code == 200
    assert client.get("/config/").status_code == 403


def test_personal_reminder_keeps_existing_full_default(
    app: Flask, memberships: dict[str, int]
) -> None:
    from datetime import date, timedelta
    from unittest.mock import patch

    from models import NotificationType, PilotProfile
    from services.notification_service import _check_medical_and_sep

    m = memberships
    with app.app_context():
        db.session.add(
            PilotProfile(
                user_id=m["user"], medical_expiry=date.today() + timedelta(days=5)
            )
        )
        db.session.commit()
        with patch("services.notification_service._dispatch_in_context") as send:
            _check_medical_and_sep(app)
        assert send.call_args.args[0] == NotificationType.MEDICAL_EXPIRING
        assert send.call_args.args[1] == m["home"]


def test_issuing_a_record_does_not_change_current_principal(
    app: Flask, memberships: dict[str, int]
) -> None:
    m = memberships
    with app.test_request_context():
        user = db.session.get(User, m["user"])
        auth_sessions.start(user, tenant_id=m["home"])
        original = session["auth_session"]
        assert current_membership().tenant_id == m["home"]
        auth_sessions.issue(user, tenant_id=m["guest"])
        assert session["auth_session"] == original
        assert current_membership().tenant_id == m["home"]


@pytest.mark.parametrize("remove", ["membership", "tenant"])
def test_explicit_admin_context_does_not_fall_back_to_global_recovery(
    app: Flask, memberships: dict[str, int], remove: str
) -> None:
    m = memberships
    with app.app_context():
        db.session.get(User, m["user"]).is_instance_admin = True
        db.session.commit()
    guest = in_context(app, m["user"], m["guest"])
    recovery = in_context(app, m["user"])
    assert guest.get("/config/tenants").status_code == 403
    assert recovery.get("/config/tenants").status_code == 200
    with app.app_context():
        if remove == "membership":
            db.session.delete(db.session.get(TenantUser, (m["user"], m["guest"])))
        else:
            db.session.delete(db.session.get(Tenant, m["guest"]))
        db.session.commit()
    assert guest.get("/profile").status_code == 302
    assert recovery.get("/config/tenants").status_code == 200
