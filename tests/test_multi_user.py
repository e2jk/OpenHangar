"""
Tests for Phase 21 — Multi-user.

Covers:
  - UserInvitation model and invitation flow (create, expiry, accept, duplicate rejection)
  - Role enforcement on representative routes for each role
  - Demo two-user slots: enter as owner / renter
  - Profile: change password, TOTP setup
  - User management: list, change role, revoke
"""

import os
from datetime import UTC, datetime, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from models import (
    DemoSlot,
    PermissionBit,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    UserAllAircraftAccess,
    UserInvitation,
    db,
)  # pyright: ignore[reportMissingImports]

from tests.auth_helpers import challenge_payload, set_membership_flags

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant_user(app, email, role, password="password-12-chars"):
    """Create a tenant+user with the given role and return (tenant_id, user_id)."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash(password),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return tenant.id, user.id


def _make_aircraft(app, tenant_id):
    """Create a minimal aircraft and return its id."""
    from models import Aircraft

    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration="OO-TST",
            make="Test",
            model="TestModel",
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── UserInvitation model ──────────────────────────────────────────────────────


class TestUserInvitationModel:
    def test_token_generated_automatically(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.token is not None
            assert len(inv.token) == 43  # secrets.token_urlsafe(32)

    def test_is_expired_false_for_future(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_expired is False

    def test_is_expired_true_for_past(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_expired is True

    def test_is_accepted_false_initially(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_accepted is False

    def test_is_accepted_true_after_acceptance(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=7),
                accepted_at=datetime.now(UTC),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_accepted is True


# ── Invitation: create ────────────────────────────────────────────────────────


class TestInvitationCreate:
    def test_admin_can_create_invitation(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        response = client.post(
            "/config/users/invite", data={"role": "pilot", "email": ""}
        )
        assert response.status_code == 302
        with app.app_context():
            assert UserInvitation.query.count() == 1

    def test_owner_can_create_invitation(self, app, client):
        _tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        _login(client, uid)
        response = client.post(
            "/config/users/invite", data={"role": "pilot", "email": ""}
        )
        assert response.status_code == 302
        with app.app_context():
            assert UserInvitation.query.count() == 1

    def test_pilot_cannot_create_invitation(self, app, client):
        _tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        response = client.post("/config/users/invite", data={"role": "viewer"})
        assert response.status_code == 403

    def test_viewer_cannot_create_invitation(self, app, client):
        _tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        _login(client, uid)
        response = client.post(
            "/config/users/invite", data={"role": "pilot", "email": ""}
        )
        assert response.status_code == 403

    def test_invitation_has_correct_role(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "maintenance", "email": ""})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.MAINTENANCE

    def test_invitation_not_accepted_by_default(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "pilot", "email": ""})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.accepted_at is None


# ── Invitation: accept ────────────────────────────────────────────────────────


class TestInvitationAccept:
    def _create_invitation(self, app, role=Role.PILOT, expired=False):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            delta = timedelta(days=-1) if expired else timedelta(days=7)
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=role,
                expires_at=datetime.now(UTC) + delta,
            )
            db.session.add(inv)
            db.session.commit()
            return inv.token, tenant.id

    def test_get_invite_page_renders(self, app, client):
        token, _ = self._create_invitation(app)
        response = client.get(f"/config/users/invite/{token}")
        assert response.status_code == 200
        assert b"Accept" in response.data

    def test_accept_creates_user_and_tenant_user(self, app, client):
        token, _tid = self._create_invitation(app, role=Role.PILOT)
        client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "newuser@test.com",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
        )
        with app.app_context():
            user = User.query.filter_by(email="newuser@test.com").first()
            assert user is not None
            tu = TenantUser.query.filter_by(user_id=user.id).first()
            assert tu is not None
            assert tu.role == Role.PILOT

    def test_accept_marks_invitation_accepted(self, app, client):
        token, _ = self._create_invitation(app)
        client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "newuser@test.com",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
        )
        with app.app_context():
            inv = UserInvitation.query.filter_by(token=token).first()
            assert inv.accepted_at is not None

    def test_expired_invitation_rejected(self, app, client):
        token, _ = self._create_invitation(app, expired=True)
        response = client.get(f"/config/users/invite/{token}", follow_redirects=True)
        assert b"expired" in response.data.lower()

    def test_already_accepted_invitation_rejected(self, app, client):
        token, _ = self._create_invitation(app)
        # Accept once
        client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "first@test.com",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
        )
        # Try to use again
        response = client.get(f"/config/users/invite/{token}", follow_redirects=True)
        assert b"already" in response.data.lower()

    def test_password_mismatch_shows_error(self, app, client):
        token, _ = self._create_invitation(app)
        response = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "newuser@test.com",
                "password": "securepass-123",
                "password2": "different-pass",
            },
            follow_redirects=True,
        )
        assert b"match" in response.data.lower()

    def test_short_password_shows_error(self, app, client):
        token, _ = self._create_invitation(app)
        response = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "newuser@test.com",
                "password": "short",
                "password2": "short",
            },
            follow_redirects=True,
        )
        assert b"12" in response.data

    def test_nonexistent_token_returns_404(self, app, client):
        response = client.get(
            "/config/users/invite/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404


# ── Role enforcement: aircraft config (OWNER only) ────────────────────────────


class TestRoleEnforcementAircraft:
    def test_admin_can_access_new_aircraft(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        assert client.get("/aircraft/new").status_code == 200

    def test_pilot_cannot_create_aircraft(self, app, client):
        _tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        assert (
            client.post(
                "/aircraft/new",
                data={
                    "registration": "OO-TST",
                    "make": "Test",
                    "model": "M",
                },
            ).status_code
            == 403
        )

    def test_maintenance_cannot_create_aircraft(self, app, client):
        _tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        _login(client, uid)
        assert (
            client.post(
                "/aircraft/new",
                data={
                    "registration": "OO-TST",
                    "make": "Test",
                    "model": "M",
                },
            ).status_code
            == 403
        )

    def test_viewer_cannot_create_aircraft(self, app, client):
        _tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        _login(client, uid)
        assert (
            client.post(
                "/aircraft/new",
                data={
                    "registration": "OO-TST",
                    "make": "Test",
                    "model": "M",
                },
            ).status_code
            == 403
        )

    def test_owner_can_create_aircraft(self, app, client):
        _tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        _login(client, uid)
        # Just checking not 403
        resp = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-TST",
                "make": "Test",
                "model": "M",
            },
        )
        assert resp.status_code != 403


# ── Role enforcement: flights (PILOT and above) ───────────────────────────────


class TestRoleEnforcementFlights:
    def test_pilot_can_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/flights/new?aircraft_id={ac_id}")
        assert resp.status_code != 403

    def test_maintenance_cannot_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(ac_id),
                "date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    def test_viewer_cannot_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(
            "/flights/new",
            data={
                "aircraft_id": str(ac_id),
                "date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    def test_admin_can_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/flights/new?aircraft_id={ac_id}")
        assert resp.status_code != 403


# ── Role enforcement: maintenance ─────────────────────────────────────────────


class TestRoleEnforcementMaintenance:
    def test_maintenance_can_create_trigger(self, app, client):
        tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/maintenance/new")
        assert resp.status_code != 403

    def test_pilot_cannot_create_maintenance_trigger(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(
            f"/aircraft/{ac_id}/maintenance/new",
            data={
                "name": "Test",
                "type": "calendar",
            },
        )
        assert resp.status_code == 403

    def test_viewer_cannot_create_maintenance_trigger(self, app, client):
        tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/maintenance/new", data={})
        assert resp.status_code == 403


# ── Role enforcement: expenses (OWNER only) ───────────────────────────────────


class TestRoleEnforcementExpenses:
    def test_pilot_cannot_add_expense(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/expenses/add", data={})
        assert resp.status_code == 403

    def test_maintenance_cannot_add_expense(self, app, client):
        tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/expenses/add", data={})
        assert resp.status_code == 403

    def test_owner_can_add_expense(self, app, client):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/expenses/add")
        assert resp.status_code != 403

    # Found by the signed-in browser crawl (2026-11-15): the list and the
    # cost dashboard checked only aircraft access, so any user assigned to
    # the aircraft could read its expenses by address; the aircraft page
    # also listed both for the offline prefetch, for every role.
    @pytest.mark.parametrize("role", [Role.PILOT, Role.MAINTENANCE, Role.VIEWER])
    @pytest.mark.parametrize("page", ["expenses", "costs"])
    def test_other_roles_cannot_read_expenses(self, app, client, role, page):
        from models import UserAllAircraftAccess

        tid, uid = _make_tenant_user(app, "crew@test.com", role)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            db.session.add(UserAllAircraftAccess(user_id=uid, tenant_id=tid))
            db.session.commit()
        _login(client, uid)
        assert client.get(f"/aircraft/{ac_id}").status_code == 200  # has access
        assert (
            f"/aircraft/OO-TST/{page}".encode()
            not in client.get(f"/aircraft/{ac_id}").data
        )
        assert client.get(f"/aircraft/{ac_id}/{page}").status_code == 403

    @pytest.mark.parametrize("page", ["expenses", "costs"])
    def test_owner_reads_expenses(self, app, client, page):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        assert (
            f"/aircraft/OO-TST/{page}".encode() in client.get(f"/aircraft/{ac_id}").data
        )
        assert client.get(f"/aircraft/{ac_id}/{page}").status_code == 200


# ── Role enforcement: documents (OWNER only) ──────────────────────────────────


def _aircraft_document(app, ac_id, fname, title, *, sensitive=False, receipt=False):
    """An aircraft document with its file on disk (optionally sensitive, or
    the receipt of an expense)."""
    import os
    from datetime import date

    from models import Document, Expense

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    with open(os.path.join(app.config["UPLOAD_FOLDER"], fname), "wb") as f:
        f.write(b"%PDF-1.4 document")
    with app.app_context():
        expense_id = None
        if receipt:
            expense = Expense(aircraft_id=ac_id, date=date(2026, 1, 15), amount=2400)
            db.session.add(expense)
            db.session.flush()
            expense_id = expense.id
        db.session.add(
            Document(
                aircraft_id=ac_id,
                filename=fname,
                original_filename=fname,
                mime_type="application/pdf",
                title=title,
                is_sensitive=sensitive,
                expense_id=expense_id,
            )
        )
        db.session.commit()


def _assigned_user(app, client, role):
    from models import UserAllAircraftAccess

    tid, uid = _make_tenant_user(app, "crew@test.com", role)
    ac_id = _make_aircraft(app, tid)
    with app.app_context():
        db.session.add(UserAllAircraftAccess(user_id=uid, tenant_id=tid))
        db.session.commit()
    _login(client, uid)
    return ac_id


def _sees(client, ac_id, fname, title):
    """Whether the documents list (sensitive ones shown) lists *title*, and
    whether its file is served."""
    listing = client.get(f"/aircraft/{ac_id}/documents?sensitive=1").data
    with client.get(f"/uploads/{fname}") as resp:
        return title.encode() in listing, resp.status_code == 200


class TestDocumentVisibility:
    # Found while checking the browser crawl's findings (2026-11-15). An
    # expense receipt is stored as an aircraft document, so it stayed
    # readable once the expenses were limited to owners, and was listed on a
    # full-access share page. And a sensitive document, which the user guide
    # hides from viewers, was listed (?sensitive=1) and served to them.
    @pytest.mark.parametrize("role", [Role.PILOT, Role.MAINTENANCE, Role.VIEWER])
    def test_expense_receipts_are_for_owners_only(self, app, client, role):
        ac_id = _assigned_user(app, client, role)
        _aircraft_document(app, ac_id, "receipt.pdf", "Fuel invoice", receipt=True)
        assert _sees(client, ac_id, "receipt.pdf", "Fuel invoice") == (False, False)

    def test_sensitive_documents_are_not_for_viewers(self, app, client):
        ac_id = _assigned_user(app, client, Role.VIEWER)
        _aircraft_document(app, ac_id, "policy.pdf", "Policy", sensitive=True)
        assert b"Show sensitive" not in client.get(f"/aircraft/{ac_id}/documents").data
        assert _sees(client, ac_id, "policy.pdf", "Policy") == (False, False)

    @pytest.mark.parametrize("role", [Role.PILOT, Role.MAINTENANCE])
    def test_crew_still_see_sensitive_documents(self, app, client, role):
        # e.g. the insurance and ARC certificates, always stored sensitive and
        # linked from the aircraft page for every role.
        ac_id = _assigned_user(app, client, role)
        _aircraft_document(app, ac_id, "policy.pdf", "Policy", sensitive=True)
        assert _sees(client, ac_id, "policy.pdf", "Policy") == (True, True)

    def test_owner_sees_both(self, app, client):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        _aircraft_document(app, ac_id, "receipt.pdf", "Fuel invoice", receipt=True)
        _aircraft_document(app, ac_id, "policy.pdf", "Policy", sensitive=True)
        assert b"Show sensitive" in client.get(f"/aircraft/{ac_id}/documents").data
        assert _sees(client, ac_id, "receipt.pdf", "Fuel invoice") == (True, True)
        assert _sees(client, ac_id, "policy.pdf", "Policy") == (True, True)


class TestRoleEnforcementDocuments:
    def test_pilot_cannot_upload_document(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/documents/upload", data={})
        assert resp.status_code == 403

    def test_owner_can_upload_document(self, app, client):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/documents/upload")
        assert resp.status_code != 403


# ── User management: list, change role, revoke ────────────────────────────────


class TestUserManagement:
    def test_admin_can_list_users(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.get("/config/users/")
        assert resp.status_code == 200

    def test_pilot_cannot_list_users(self, app, client):
        _tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        resp = client.get("/config/users/")
        assert resp.status_code == 403

    def test_admin_can_change_role(self, app, client):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(user2)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user2.id, tenant_id=tid, role=Role.VIEWER)
            )
            db.session.commit()
            user2_id = user2.id
        _login(client, admin_uid)
        client.post(
            f"/config/users/{user2_id}/role", data={"role": "pilot", "email": ""}
        )
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=user2_id).first()
            assert tu.role == Role.PILOT

    def test_admin_cannot_change_own_role(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(
            f"/config/users/{uid}/role", data={"role": "viewer"}, follow_redirects=True
        )
        assert b"own role" in resp.data.lower()

    def test_admin_can_revoke_user(self, app, client):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(user2)
            db.session.flush()
            db.session.add(TenantUser(user_id=user2.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()
            user2_id = user2.id
        _login(client, admin_uid)
        client.post(f"/config/users/{user2_id}/revoke")
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=user2_id).first()
            assert tu is None

    def test_admin_cannot_revoke_own_access(self, app, client):
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(f"/config/users/{uid}/revoke", follow_redirects=True)
        assert b"own" in resp.data.lower()


def _add_member(app, tid, email, role, *, instance_admin=False, scope="full"):
    """Add a user to tenant `tid` and return the user's id."""
    with app.app_context():
        user = User(
            email=email,
            password_hash=_pw_hash.hash("pass-12-chars"),
            is_active=True,
            is_instance_admin=instance_admin,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tid, role=role, access_scope=scope)
        )
        db.session.commit()
        return user.id


class TestProtectedMembers:
    """An owner cannot demote, strip or revoke an admin; only the instance
    admin manages the instance admin; revoking never disables an account that
    still belongs to another hangar."""

    @pytest.mark.parametrize(
        ("path", "data"),
        [
            ("role", {"role": "viewer"}),
            ("revoke", {}),
            ("flags", {}),
        ],
    )
    def test_owner_cannot_manage_an_admin(self, app, client, path, data):
        tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        admin_uid = _add_member(app, tid, "admin@test.com", Role.ADMIN)
        with app.app_context():
            set_membership_flags(admin_uid, is_pilot=True)
            db.session.commit()
        _login(client, owner_uid)
        resp = client.post(
            f"/config/users/{admin_uid}/{path}", data=data, follow_redirects=True
        )
        assert b"Only an admin can change another admin" in resp.data
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=admin_uid, tenant_id=tid).one()
            assert tu.role == Role.ADMIN
            admin = db.session.get(User, admin_uid)
            assert admin.is_active is True
            assert tu.is_pilot is True

    def test_owner_list_hides_controls_on_admin_rows(self, app, client):
        tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        admin_uid = _add_member(app, tid, "admin@test.com", Role.ADMIN)
        pilot_uid = _add_member(app, tid, "pilot@test.com", Role.PILOT)
        _login(client, owner_uid)
        html = client.get("/config/users/").data.decode()
        assert f"/config/users/{admin_uid}/revoke" not in html
        assert f"/config/users/{admin_uid}/role" not in html
        assert f"/config/users/{admin_uid}/flags" not in html
        assert f"/config/users/{pilot_uid}/revoke" in html

    def test_admin_can_still_manage_another_admin(self, app, client):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        other_uid = _add_member(app, tid, "admin2@test.com", Role.ADMIN)
        _login(client, admin_uid)
        client.post(f"/config/users/{other_uid}/role", data={"role": "owner"})
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=other_uid, tenant_id=tid).one()
            assert tu.role == Role.OWNER

    @pytest.mark.parametrize(
        ("path", "data"),
        [
            ("role", {"role": "viewer"}),
            ("revoke", {}),
            ("flags", {}),
        ],
    )
    def test_tenant_admin_cannot_manage_the_instance_admin(
        self, app, client, path, data
    ):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        ia_uid = _add_member(app, tid, "ia@test.com", Role.OWNER, instance_admin=True)
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{ia_uid}/{path}", data=data, follow_redirects=True
        )
        assert b"Only the instance administrator can change" in resp.data
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=ia_uid, tenant_id=tid).one()
            assert tu.role == Role.OWNER
            assert db.session.get(User, ia_uid).is_active is True

    def test_owner_cannot_toggle_personal_logbook_of_instance_admin(self, app, client):
        tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        ia_uid = _add_member(
            app, tid, "ia@test.com", None, instance_admin=True, scope="personal"
        )
        _login(client, owner_uid)
        client.post(f"/config/users/{ia_uid}/personal-logbook", data={"enable": "yes"})
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=ia_uid, tenant_id=tid).one()
            assert tu.access_scope == "personal"

    def test_instance_admin_can_manage_another_instance_admin(self, app, client):
        tid, actor_uid = _make_tenant_user(app, "ia1@test.com", Role.ADMIN)
        with app.app_context():
            db.session.get(User, actor_uid).is_instance_admin = True
            db.session.commit()
        ia_uid = _add_member(app, tid, "ia2@test.com", Role.OWNER, instance_admin=True)
        _login(client, actor_uid)
        client.post(f"/config/users/{ia_uid}/revoke")
        with app.app_context():
            assert (
                TenantUser.query.filter_by(user_id=ia_uid, tenant_id=tid).first()
                is None
            )
            # The instance admin's account itself is never switched off.
            assert db.session.get(User, ia_uid).is_active is True

    def test_revoke_keeps_an_account_that_belongs_to_another_hangar(self, app, client):
        tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        other_tid, member_uid = _make_tenant_user(app, "member@test.com", Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(
                    user_id=member_uid,
                    tenant_id=tid,
                    role=None,
                    access_scope="personal",
                )
            )
            db.session.commit()
        _login(client, owner_uid)
        client.post(f"/config/users/{member_uid}/revoke")
        with app.app_context():
            assert (
                TenantUser.query.filter_by(user_id=member_uid, tenant_id=tid).first()
                is None
            )
            assert TenantUser.query.filter_by(
                user_id=member_uid, tenant_id=other_tid
            ).one()
            assert db.session.get(User, member_uid).is_active is True

    def test_flags_set_in_one_hangar_never_reach_another(self, app, client):
        """The capability flags belong to the membership: an owner of a
        hangar the user also belongs to (say, through a passport group) can
        neither lift the read-only flag nor grant maintenance in the user's
        own hangar."""
        own_tid, member_uid = _make_tenant_user(app, "member@test.com", Role.VIEWER)
        other_tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        with app.app_context():
            db.session.get(TenantUser, (member_uid, own_tid)).view_only = True
            db.session.add(
                TenantUser(
                    user_id=member_uid,
                    tenant_id=other_tid,
                    role=None,
                    access_scope="personal",
                )
            )
            db.session.commit()
        _login(client, owner_uid)
        client.post(f"/config/users/{member_uid}/flags", data={"is_maintenance": "on"})
        with app.app_context():
            own = db.session.get(TenantUser, (member_uid, own_tid))
            assert own.view_only is True and own.is_maintenance is False
            other = db.session.get(TenantUser, (member_uid, other_tid))
            assert other.is_maintenance is True and other.view_only is False

    def test_revoke_disables_an_account_left_without_any_hangar(self, app, client):
        tid, owner_uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        pilot_uid = _add_member(app, tid, "pilot@test.com", Role.PILOT)
        _login(client, owner_uid)
        client.post(f"/config/users/{pilot_uid}/revoke")
        with app.app_context():
            assert db.session.get(User, pilot_uid).is_active is False


# ── Profile: change password ──────────────────────────────────────────────────


class TestProfileChangePassword:
    def test_profile_page_accessible_to_all_roles(self, app, client):
        for role in (Role.ADMIN, Role.PILOT, Role.MAINTENANCE, Role.VIEWER):
            _, uid = _make_tenant_user(app, f"{role.value}@test.com", role)
            _login(client, uid)
            assert client.get("/profile").status_code == 200

    def test_change_password_wrong_current(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "change_password",
                "current_password": "wrongpassword12",
                "new_password": "newpassword1234",
                "confirm_password": "newpassword1234",
            },
            follow_redirects=True,
        )
        assert b"incorrect" in resp.data.lower()

    def test_change_password_mismatch(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "change_password",
                "current_password": "correctpassword1",
                "new_password": "newpassword1234",
                "confirm_password": "differentpassw2",
            },
            follow_redirects=True,
        )
        assert b"match" in resp.data.lower()

    def test_change_password_too_short(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "change_password",
                "current_password": "correctpassword1",
                "new_password": "short",
                "confirm_password": "short",
            },
            follow_redirects=True,
        )
        assert b"12" in resp.data

    def test_change_password_success(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "change_password",
                "current_password": "correctpassword1",
                "new_password": "newpassword-1234",
                "confirm_password": "newpassword-1234",
            },
            follow_redirects=True,
        )
        assert b"updated" in resp.data.lower()
        with app.app_context():
            user = db.session.get(User, uid)
            import pw_hash as _pw  # pyright: ignore[reportMissingImports]

            assert _pw.verify("newpassword-1234", user.password_hash)


# ── Demo multi-user slots ─────────────────────────────────────────────────────


class TestDemoMultiUser:
    @pytest.fixture()
    def demo_app(self):
        old = os.environ.get("OPENHANGAR_ENV")
        os.environ["OPENHANGAR_ENV"] = "demo"
        try:
            from init import create_app

            app = create_app()
            app.config["TESTING"] = True
            app.config["WTF_CSRF_ENABLED"] = False
            app.config["RATELIMIT_ENABLED"] = False
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            with app.app_context():
                db.create_all()
            yield app
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
        finally:
            if old is None:
                os.environ.pop("OPENHANGAR_ENV", None)
            else:
                os.environ["OPENHANGAR_ENV"] = old

    @pytest.fixture()
    def demo_client(self, demo_app):
        return demo_app.test_client()

    def _make_two_user_slot(self, app):
        with app.app_context():
            tenant = Tenant(name="Demo Hangar #1")
            db.session.add(tenant)
            db.session.flush()
            owner = User(
                email="demo-1@openhangar.demo",
                password_hash=_pw_hash.hash("x"),
                is_active=True,
            )
            db.session.add(owner)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER)
            )
            renter = User(
                email="demo-renter-1@openhangar.demo",
                password_hash=_pw_hash.hash("x"),
                is_active=True,
            )
            db.session.add(renter)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=renter.id, tenant_id=tenant.id, role=Role.PILOT)
            )
            slot = DemoSlot(
                id=1, tenant_id=tenant.id, user_id=owner.id, renter_user_id=renter.id
            )
            db.session.add(slot)
            db.session.commit()
            return owner.id, renter.id

    def test_enter_as_owner_sets_owner_user(self, demo_app, demo_client):
        owner_id, _ = self._make_two_user_slot(demo_app)
        demo_client.post("/demo/enter", data={"role": "owner"})
        with demo_client.session_transaction() as sess:
            assert sess["user_id"] == owner_id

    def test_enter_as_renter_sets_renter_user(self, demo_app, demo_client):
        _, renter_id = self._make_two_user_slot(demo_app)
        demo_client.post("/demo/enter", data={"role": "renter"})
        with demo_client.session_transaction() as sess:
            assert sess["user_id"] == renter_id

    def test_renter_cannot_create_aircraft(self, demo_app, demo_client):
        _, _renter_id = self._make_two_user_slot(demo_app)
        demo_client.post("/demo/enter", data={"role": "renter"})
        resp = demo_client.post(
            "/aircraft/new",
            data={
                "registration": "OO-TST",
                "make": "Test",
                "model": "M",
            },
        )
        assert resp.status_code == 403

    def test_landing_page_shows_four_demo_buttons(self, demo_app, demo_client):
        resp = demo_client.get("/")
        assert b'value="owner"' in resp.data
        assert b'value="pilot"' in resp.data
        assert b'value="maintenance"' in resp.data
        assert b'value="viewer"' in resp.data

    def test_enter_default_role_is_owner(self, demo_app, demo_client):
        """Entering without role= param defaults to owner."""
        owner_id, _ = self._make_two_user_slot(demo_app)
        demo_client.post("/demo/enter")
        with demo_client.session_transaction() as sess:
            assert sess["user_id"] == owner_id


# ── Demo block: users blueprint ───────────────────────────────────────────────


class TestDemoBlock:
    def test_users_blueprint_blocked_in_demo(self, app, client):
        """users/routes.py:33 — before_request hook returns 403 in demo mode."""
        old = os.environ.get("OPENHANGAR_ENV")
        os.environ["OPENHANGAR_ENV"] = "demo"
        try:
            resp = client.get("/config/users/")
            assert resp.status_code == 403
        finally:
            if old is None:
                os.environ.pop("OPENHANGAR_ENV", None)
            else:
                os.environ["OPENHANGAR_ENV"] = old


# ── Profile: TOTP setup / confirm / disable ───────────────────────────────────


class TestProfileTOTP:
    def test_setup_totp_stores_only_challenge_in_session(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post(
            "/profile",
            data={"action": "setup_totp", "current_password": "password-12-chars"},
        )
        with client.session_transaction() as sess:
            assert "profile_challenge" in sess
            assert "profile_totp_secret" not in sess

    def test_setup_totp_renders_profile(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={"action": "setup_totp", "current_password": "password-12-chars"},
        )
        assert resp.status_code == 200

    def test_confirm_totp_without_session_redirects(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "confirm_totp",
                "totp_code": "000000",
            },
        )
        assert resp.status_code == 302

    def test_confirm_totp_invalid_code_shows_error(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post(
            "/profile",
            data={"action": "setup_totp", "current_password": "password-12-chars"},
        )
        resp = client.post(
            "/profile",
            data={
                "action": "confirm_totp",
                "totp_code": "000000",
            },
            follow_redirects=True,
        )
        assert b"invalid" in resp.data.lower()

    def test_confirm_totp_success_enables_2fa(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post(
            "/profile",
            data={"action": "setup_totp", "current_password": "password-12-chars"},
        )
        secret = challenge_payload(client, "profile_challenge")["secret"]
        valid_code = pyotp.TOTP(secret).now()
        client.post(
            "/profile",
            data={
                "action": "confirm_totp",
                "totp_code": valid_code,
            },
            follow_redirects=True,
        )
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret == secret

    def test_disable_totp_wrong_password_shows_error(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        with app.app_context():
            user = db.session.get(User, uid)
            user.totp_secret = pyotp.random_base32()
            db.session.commit()
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "disable_totp",
                "current_password": "wrongpassword12",
            },
            follow_redirects=True,
        )
        assert b"incorrect" in resp.data.lower()

    def test_disable_totp_success_clears_secret(self, app, client):
        _, uid = _make_tenant_user(
            app, "user@test.com", Role.ADMIN, password="correctpassword1"
        )
        with app.app_context():
            user = db.session.get(User, uid)
            user.totp_secret = secret = pyotp.random_base32()
            db.session.commit()
        _login(client, uid)
        resp = client.post(
            "/profile",
            data={
                "action": "disable_totp",
                "current_password": "correctpassword1",
                "current_totp_code": pyotp.TOTP(secret).now(),
            },
            follow_redirects=True,
        )
        assert b"disabled" in resp.data.lower()
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret is None


# ── Invitation edge cases ─────────────────────────────────────────────────────


class TestInvitationEdgeCases:
    def test_invalid_role_falls_back_to_pilot(self, app, client):
        """users/routes.py:92-93 — ValueError in Role() caught, defaults to PILOT."""
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "not-a-role", "email": ""})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.PILOT

    def test_admin_role_clamped_to_owner(self, app, client):
        """users/routes.py:95 — ADMIN role passed to invite is silently clamped to OWNER."""
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "admin", "email": ""})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.OWNER

    def test_invite_with_email_triggers_send_path(self, app, client):
        """users/routes.py:110,117-129 — invite with email hits _try_send_invite_email."""
        _tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(
            "/config/users/invite",
            data={
                "role": "pilot",
                "email": "invited@test.com",
            },
        )
        assert resp.status_code == 302
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.email == "invited@test.com"

    def _make_invitation(self, app, tenant_id):
        with app.app_context():
            inv = UserInvitation(
                tenant_id=tenant_id,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            return inv.token

    def test_accept_invite_invalid_email(self, app, client):
        """users/routes.py:157 — invalid email address shows validation error."""
        tid, _ = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        token = self._make_invitation(app, tid)
        resp = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "not-an-email",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
            follow_redirects=True,
        )
        assert b"valid email" in resp.data.lower()

    def test_accept_invite_duplicate_email(self, app, client):
        """users/routes.py:163 — already-registered email shows error."""
        tid, _ = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        token = self._make_invitation(app, tid)
        resp = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "admin@test.com",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
            follow_redirects=True,
        )
        assert b"already exists" in resp.data.lower()


# ── change_role edge cases ────────────────────────────────────────────────────


class TestChangeRoleEdgeCases:
    def _setup_two_users(self, app):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(user2)
            db.session.flush()
            db.session.add(
                TenantUser(user_id=user2.id, tenant_id=tid, role=Role.VIEWER)
            )
            db.session.commit()
            user2_id = user2.id
        return admin_uid, user2_id

    def test_invalid_role_value_returns_400(self, app, client):
        """users/routes.py:207-208 — unrecognised role string → 400."""
        admin_uid, user2_id = self._setup_two_users(app)
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{user2_id}/role", data={"role": "not-a-role"}
        )
        assert resp.status_code == 400

    def test_admin_role_returns_400(self, app, client):
        """users/routes.py:210 — attempting to assign ADMIN role → 400."""
        admin_uid, user2_id = self._setup_two_users(app)
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{user2_id}/role", data={"role": "admin", "email": ""}
        )
        assert resp.status_code == 400


# ── Revoke invite ─────────────────────────────────────────────────────────────


class TestRevokeInvite:
    def test_admin_can_revoke_pending_invitation(self, app, client):
        """users/routes.py:244-249 — admin deletes a pending invitation."""
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            inv = UserInvitation(
                tenant_id=tid,
                role=Role.PILOT,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            inv_id = inv.id
        _login(client, uid)
        resp = client.post(f"/config/users/invite/{inv_id}/revoke")
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(UserInvitation, inv_id) is None


# ── list_users with aircraft access rows ─────────────────────────────────────


class TestListUsersAircraftAccess:
    def test_list_users_populates_user_aircraft_ids(self, app, client):
        """users/routes.py:86 — list_users collects UserAircraftAccess rows per user."""
        tid, admin_uid = _make_tenant_user(app, "admin@listac.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            pilot = User(
                email="pilot@listac.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tid, role=Role.PILOT))
            db.session.add(UserAircraftAccess(user_id=pilot.id, aircraft_id=ac_id))
            db.session.commit()
        _login(client, admin_uid)
        resp = client.get("/config/users/")
        assert resp.status_code == 200


# ── invite with invalid aircraft_ids ─────────────────────────────────────────


class TestInviteAircraftIds:
    def test_invite_with_invalid_aircraft_id_results_in_empty_list(self, app, client):
        """users/routes.py:128-129 — non-integer aircraft_id triggers ValueError → []."""
        tid, admin_uid = _make_tenant_user(app, "admin@invac.dev", Role.ADMIN)
        _login(client, admin_uid)
        resp = client.post(
            "/config/users/invite",
            data={
                "role": "pilot",
                "email": "",
                "aircraft_ids": ["not-an-int"],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            inv = (
                UserInvitation.query.filter_by(tenant_id=tid)
                .order_by(UserInvitation.id.desc())
                .first()
            )
            assert inv is not None
            assert inv.aircraft_ids == []

    def test_invite_drops_another_hangars_aircraft(self, app, client):
        """An aircraft of another hangar (or a deleted one) is never stored
        on the invitation: accepting it used to fail with a server error."""
        tid, admin_uid = _make_tenant_user(app, "admin@invac3.dev", Role.ADMIN)
        own = _make_aircraft(app, tid)
        other_tid, _other = _make_tenant_user(app, "other@invac3.dev", Role.ADMIN)
        from models import Aircraft

        with app.app_context():
            foreign = Aircraft(
                tenant_id=other_tid, registration="OO-FOR", make="X", model="Y"
            )
            db.session.add(foreign)
            db.session.commit()
            foreign_id = foreign.id
        _login(client, admin_uid)
        client.post(
            "/config/users/invite",
            data={
                "role": "pilot",
                "email": "",
                "aircraft_ids": [f"{own},{foreign_id},999999"],
            },
        )
        with app.app_context():
            inv = (
                UserInvitation.query.filter_by(tenant_id=tid)
                .order_by(UserInvitation.id.desc())
                .first()
            )
            assert inv.aircraft_ids == [own]

    def test_invite_with_valid_aircraft_id_stores_it(self, app, client):
        """users/routes.py:127 — valid aircraft_id stored on invitation."""
        tid, admin_uid = _make_tenant_user(app, "admin@invac2.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        _login(client, admin_uid)
        client.post(
            "/config/users/invite",
            data={
                "role": "pilot",
                "email": "",
                "aircraft_ids": [str(ac_id)],
            },
        )
        with app.app_context():
            inv = (
                UserInvitation.query.filter_by(tenant_id=tid)
                .order_by(UserInvitation.id.desc())
                .first()
            )
            assert inv is not None
            assert ac_id in inv.aircraft_ids


# ── accept_invite grants per-aircraft access ──────────────────────────────────


class TestAcceptInviteAircraftAccess:
    def _make_invite_with_aircraft(self, app, tid, ac_id):
        with app.app_context():
            inv = UserInvitation(
                tenant_id=tid,
                role=Role.PILOT,
                aircraft_ids=[ac_id],
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            return inv.token

    def test_accept_invite_creates_aircraft_access_rows(self, app, client):
        """users/routes.py:222-223 — accepting invite with aircraft_ids creates access rows."""
        tid, _ = _make_tenant_user(app, "admin@accinv.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        token = self._make_invite_with_aircraft(app, tid, ac_id)
        resp = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "newpilot@accinv.dev",
                "password": "securepass-123",
                "password2": "securepass-123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            user = User.query.filter_by(email="newpilot@accinv.dev").first()
            assert user is not None
            access = UserAircraftAccess.query.filter_by(
                user_id=user.id, aircraft_id=ac_id
            ).first()
            assert access is not None


# ── change_role to OWNER clears access rows ───────────────────────────────────


class TestChangeRoleClearsAccess:
    def test_change_role_to_owner_deletes_aircraft_access(self, app, client):
        """users/routes.py:254 — changing role to OWNER removes UserAircraftAccess rows."""
        tid, admin_uid = _make_tenant_user(app, "admin@chgrole.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            pilot = User(
                email="pilot@chgrole.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tid, role=Role.PILOT))
            db.session.add(UserAircraftAccess(user_id=pilot.id, aircraft_id=ac_id))
            db.session.commit()
            pilot_id = pilot.id
        _login(client, admin_uid)
        resp = client.post(f"/config/users/{pilot_id}/role", data={"role": "owner"})
        assert resp.status_code == 302
        with app.app_context():
            assert UserAircraftAccess.query.filter_by(user_id=pilot_id).first() is None


# ── update_aircraft_access route ─────────────────────────────────────────────


class TestUpdateAircraftAccess:
    def _setup(self, app, suffix=""):
        tid, admin_uid = _make_tenant_user(app, f"admin@upac{suffix}.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            pilot = User(
                email=f"pilot@upac{suffix}.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()
            pilot_id = pilot.id
        return admin_uid, pilot_id, tid, ac_id

    def test_grants_aircraft_access(self, app, client):
        """users/routes.py:332-333 — valid aircraft_id → access row created."""
        admin_uid, pilot_id, _, ac_id = self._setup(app, "g")
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{pilot_id}/aircraft-access",
            data={"aircraft_ids": [str(ac_id)]},
        )
        assert resp.status_code == 302
        with app.app_context():
            assert (
                UserAircraftAccess.query.filter_by(
                    user_id=pilot_id, aircraft_id=ac_id
                ).first()
                is not None
            )

    def test_revokes_existing_access(self, app, client):
        """users/routes.py:329-330 — empty aircraft_ids removes existing rows."""
        admin_uid, pilot_id, _, ac_id = self._setup(app, "r")
        with app.app_context():
            db.session.add(UserAircraftAccess(user_id=pilot_id, aircraft_id=ac_id))
            db.session.commit()
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{pilot_id}/aircraft-access",
            data={"aircraft_ids": []},
        )
        assert resp.status_code == 302
        with app.app_context():
            assert UserAircraftAccess.query.filter_by(user_id=pilot_id).first() is None

    def test_invalid_aircraft_id_returns_400(self, app, client):
        """users/routes.py:312-313 — non-integer aircraft_id → 400."""
        admin_uid, pilot_id, _, _ = self._setup(app, "i")
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{pilot_id}/aircraft-access",
            data={"aircraft_ids": ["not-an-int"]},
        )
        assert resp.status_code == 400

    def test_owner_role_redirects_with_info(self, app, client):
        """users/routes.py:305-307 — target user is OWNER → redirect without changes."""
        tid, admin_uid = _make_tenant_user(app, "admin@upacown.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            owner = User(
                email="owner@upacown.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(owner)
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tid, role=Role.OWNER))
            db.session.commit()
            owner_id = owner.id
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{owner_id}/aircraft-access",
            data={"aircraft_ids": [str(ac_id)]},
        )
        assert resp.status_code == 302
        with app.app_context():
            assert UserAircraftAccess.query.filter_by(user_id=owner_id).first() is None


# ── update_user_flags with orphaned TenantUser ───────────────────────────────


# ── edit_permissions route ────────────────────────────────────────────────────


class TestEditPermissions:
    def _setup(self, app, suffix=""):
        tid, admin_uid = _make_tenant_user(app, f"admin@perm{suffix}.dev", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        with app.app_context():
            pilot = User(
                email=f"pilot@perm{suffix}.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tid, role=Role.PILOT))
            db.session.add(UserAircraftAccess(user_id=pilot.id, aircraft_id=ac_id))
            db.session.commit()
            pilot_id = pilot.id
        return admin_uid, pilot_id, tid, ac_id

    def test_get_renders_permissions_page(self, app, client):
        """users/routes.py:460 — GET returns 200 with the permissions template."""
        admin_uid, pilot_id, _, _ = self._setup(app, "g")
        _login(client, admin_uid)
        resp = client.get(f"/config/users/{pilot_id}/permissions")
        assert resp.status_code == 200

    def test_get_for_owner_redirects(self, app, client):
        """users/routes.py:412-414 — GET for OWNER user → redirect."""
        tid, admin_uid = _make_tenant_user(app, "admin@permown.dev", Role.ADMIN)
        with app.app_context():
            owner = User(
                email="owner@permown.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(owner)
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tid, role=Role.OWNER))
            db.session.commit()
            owner_id = owner.id
        _login(client, admin_uid)
        resp = client.get(f"/config/users/{owner_id}/permissions")
        assert resp.status_code == 302

    def test_post_updates_permission_mask(self, app, client):
        """users/routes.py:436-444 — POST saves updated masks and redirects."""
        admin_uid, pilot_id, tid, _ = self._setup(app, "p")
        with app.app_context():
            db.session.add(UserAllAircraftAccess(user_id=pilot_id, tenant_id=tid))
            db.session.commit()
        _login(client, admin_uid)
        resp = client.post(
            f"/config/users/{pilot_id}/permissions",
            data={f"bit_all_{PermissionBit.VIEW_AIRCRAFT}": "on"},
        )
        assert resp.status_code == 302

    def test_get_returns_404_when_user_row_deleted(self, app, client):
        """users/routes.py:408-410 — abort(404) when TenantUser exists but User deleted."""
        from sqlalchemy import text

        tid, admin_uid = _make_tenant_user(app, "admin@permdel.dev", Role.ADMIN)
        with app.app_context():
            pilot = User(
                email="pilot@permdel.dev",
                password_hash=_pw_hash.hash("pass-12-chars"),
                is_active=True,
            )
            db.session.add(pilot)
            db.session.flush()
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tid, role=Role.PILOT))
            db.session.commit()
            pilot_id = pilot.id
        with app.app_context():
            db.session.execute(text("PRAGMA foreign_keys=OFF"))
            db.session.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": pilot_id}
            )
            db.session.commit()
            db.session.execute(text("PRAGMA foreign_keys=ON"))
        _login(client, admin_uid)
        resp = client.get(f"/config/users/{pilot_id}/permissions")
        assert resp.status_code == 404


def test_an_invitation_to_an_address_creates_that_address_only(app, client):
    from datetime import UTC, datetime, timedelta

    tid, admin_uid = _make_tenant_user(app, "admin@bind.dev", Role.ADMIN)
    with app.app_context():
        inv = UserInvitation(
            tenant_id=tid,
            invited_by_user_id=admin_uid,
            email="friend@bind.dev",
            role=Role.PILOT,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.session.add(inv)
        db.session.commit()
        token = inv.token
    client.post(
        f"/config/users/invite/{token}",
        data={
            "email": "someone-else@bind.dev",
            "password": "a-long-password-1",
            "password2": "a-long-password-1",
        },
    )
    with app.app_context():
        assert User.query.filter_by(email="someone-else@bind.dev").first() is None
        assert User.query.filter_by(email="friend@bind.dev").first() is not None


def test_an_invitation_claimed_meanwhile_creates_no_account(app, client):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    tid, admin_uid = _make_tenant_user(app, "admin@race.dev", Role.ADMIN)
    with app.app_context():
        inv = UserInvitation(
            tenant_id=tid,
            invited_by_user_id=admin_uid,
            role=Role.PILOT,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.session.add(inv)
        db.session.commit()
        token = inv.token
    with patch("flask_sqlalchemy.query.Query.update", return_value=0):
        resp = client.post(
            f"/config/users/invite/{token}",
            data={
                "email": "late@race.dev",
                "password": "a-long-password-1",
                "password2": "a-long-password-1",
            },
            follow_redirects=True,
        )
    assert b"already been used" in resp.data
    with app.app_context():
        assert User.query.filter_by(email="late@race.dev").first() is None


def test_the_invitation_email_is_translated(app, client):
    from unittest.mock import patch

    _tid, admin_uid = _make_tenant_user(app, "admin@fr-invite.dev", Role.ADMIN)
    with app.app_context():
        db.session.get(User, admin_uid).language = "fr"
        db.session.commit()
    _login(client, admin_uid)
    with patch("services.email_service.send_email") as send:
        client.post(
            "/config/users/invite",
            data={"role": "pilot", "email": "ami@fr-invite.dev", "display_name": "Ami"},
        )
    body = send.call_args.kwargs["text_body"]
    assert "Bonjour Ami," in body and "Pilote / locataire" in body
    assert "Ce lien expire dans" in body
