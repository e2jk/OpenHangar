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
from datetime import datetime, timedelta, timezone

import bcrypt  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from models import DemoSlot, Role, Tenant, TenantUser, User, UserInvitation, db  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tenant_user(app, email, role, password="password-12-chars"):
    """Create a tenant+user with the given role and return (tenant_id, user_id)."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
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
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.token is not None
            assert len(inv.token) == 36  # UUID4

    def test_is_expired_false_for_future(self, app):
        with app.app_context():
            tenant = Tenant(name="T")
            db.session.add(tenant)
            db.session.flush()
            inv = UserInvitation(
                tenant_id=tenant.id,
                role=Role.PILOT,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
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
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
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
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
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
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                accepted_at=datetime.now(timezone.utc),
            )
            db.session.add(inv)
            db.session.commit()
            assert inv.is_accepted is True


# ── Invitation: create ────────────────────────────────────────────────────────

class TestInvitationCreate:
    def test_admin_can_create_invitation(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        response = client.post("/config/users/invite", data={"role": "pilot"})
        assert response.status_code == 302
        with app.app_context():
            assert UserInvitation.query.count() == 1

    def test_owner_can_create_invitation(self, app, client):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        _login(client, uid)
        response = client.post("/config/users/invite", data={"role": "pilot"})
        assert response.status_code == 302
        with app.app_context():
            assert UserInvitation.query.count() == 1

    def test_pilot_cannot_create_invitation(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        response = client.post("/config/users/invite", data={"role": "viewer"})
        assert response.status_code == 403

    def test_viewer_cannot_create_invitation(self, app, client):
        tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        _login(client, uid)
        response = client.post("/config/users/invite", data={"role": "pilot"})
        assert response.status_code == 403

    def test_invitation_has_correct_role(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "maintenance"})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.MAINTENANCE

    def test_invitation_not_accepted_by_default(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "pilot"})
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
                expires_at=datetime.now(timezone.utc) + delta,
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
        token, tid = self._create_invitation(app, role=Role.PILOT)
        client.post(f"/config/users/invite/{token}", data={
            "email": "newuser@test.com",
            "password": "securepass-123",
            "password2": "securepass-123",
        })
        with app.app_context():
            user = User.query.filter_by(email="newuser@test.com").first()
            assert user is not None
            tu = TenantUser.query.filter_by(user_id=user.id).first()
            assert tu is not None
            assert tu.role == Role.PILOT

    def test_accept_marks_invitation_accepted(self, app, client):
        token, _ = self._create_invitation(app)
        client.post(f"/config/users/invite/{token}", data={
            "email": "newuser@test.com",
            "password": "securepass-123",
            "password2": "securepass-123",
        })
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
        client.post(f"/config/users/invite/{token}", data={
            "email": "first@test.com",
            "password": "securepass-123",
            "password2": "securepass-123",
        })
        # Try to use again
        response = client.get(f"/config/users/invite/{token}", follow_redirects=True)
        assert b"already" in response.data.lower()

    def test_password_mismatch_shows_error(self, app, client):
        token, _ = self._create_invitation(app)
        response = client.post(f"/config/users/invite/{token}", data={
            "email": "newuser@test.com",
            "password": "securepass-123",
            "password2": "different-pass",
        }, follow_redirects=True)
        assert b"match" in response.data.lower()

    def test_short_password_shows_error(self, app, client):
        token, _ = self._create_invitation(app)
        response = client.post(f"/config/users/invite/{token}", data={
            "email": "newuser@test.com",
            "password": "short",
            "password2": "short",
        }, follow_redirects=True)
        assert b"12" in response.data

    def test_nonexistent_token_returns_404(self, app, client):
        response = client.get("/config/users/invite/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


# ── Role enforcement: aircraft config (OWNER only) ────────────────────────────

class TestRoleEnforcementAircraft:
    def test_admin_can_access_new_aircraft(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        assert client.get("/aircraft/new").status_code == 200

    def test_pilot_cannot_create_aircraft(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        assert client.post("/aircraft/new", data={
            "registration": "OO-TST", "make": "Test", "model": "M",
        }).status_code == 403

    def test_maintenance_cannot_create_aircraft(self, app, client):
        tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        _login(client, uid)
        assert client.post("/aircraft/new", data={
            "registration": "OO-TST", "make": "Test", "model": "M",
        }).status_code == 403

    def test_viewer_cannot_create_aircraft(self, app, client):
        tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        _login(client, uid)
        assert client.post("/aircraft/new", data={
            "registration": "OO-TST", "make": "Test", "model": "M",
        }).status_code == 403

    def test_owner_can_create_aircraft(self, app, client):
        tid, uid = _make_tenant_user(app, "owner@test.com", Role.OWNER)
        _login(client, uid)
        # Just checking not 403
        resp = client.post("/aircraft/new", data={
            "registration": "OO-TST", "make": "Test", "model": "M",
            "regime": "EASA",
        })
        assert resp.status_code != 403


# ── Role enforcement: flights (PILOT and above) ───────────────────────────────

class TestRoleEnforcementFlights:
    def test_pilot_can_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/flights/new")
        assert resp.status_code != 403

    def test_maintenance_cannot_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "maint@test.com", Role.MAINTENANCE)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/flights/new", data={
            "date": "2025-01-01",
        })
        assert resp.status_code == 403

    def test_viewer_cannot_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "viewer@test.com", Role.VIEWER)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.post(f"/aircraft/{ac_id}/flights/new", data={
            "date": "2025-01-01",
        })
        assert resp.status_code == 403

    def test_admin_can_log_flight(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        ac_id = _make_aircraft(app, tid)
        _login(client, uid)
        resp = client.get(f"/aircraft/{ac_id}/flights/new")
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
        resp = client.post(f"/aircraft/{ac_id}/maintenance/new", data={
            "name": "Test", "type": "calendar",
        })
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


# ── Role enforcement: documents (OWNER only) ──────────────────────────────────

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
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.get("/config/users/")
        assert resp.status_code == 200

    def test_pilot_cannot_list_users(self, app, client):
        tid, uid = _make_tenant_user(app, "pilot@test.com", Role.PILOT)
        _login(client, uid)
        resp = client.get("/config/users/")
        assert resp.status_code == 403

    def test_admin_can_change_role(self, app, client):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=bcrypt.hashpw(b"pass-12-chars", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(user2)
            db.session.flush()
            db.session.add(TenantUser(user_id=user2.id, tenant_id=tid, role=Role.VIEWER))
            db.session.commit()
            user2_id = user2.id
        _login(client, admin_uid)
        client.post(f"/config/users/{user2_id}/role", data={"role": "pilot"})
        with app.app_context():
            tu = TenantUser.query.filter_by(user_id=user2_id).first()
            assert tu.role == Role.PILOT

    def test_admin_cannot_change_own_role(self, app, client):
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(f"/config/users/{uid}/role", data={"role": "viewer"},
                           follow_redirects=True)
        assert b"own role" in resp.data.lower()

    def test_admin_can_revoke_user(self, app, client):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=bcrypt.hashpw(b"pass-12-chars", bcrypt.gensalt()).decode(),
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
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post(f"/config/users/{uid}/revoke", follow_redirects=True)
        assert b"own" in resp.data.lower()


# ── Profile: change password ──────────────────────────────────────────────────

class TestProfileChangePassword:
    def test_profile_page_accessible_to_all_roles(self, app, client):
        for role in (Role.ADMIN, Role.PILOT, Role.MAINTENANCE, Role.VIEWER):
            _, uid = _make_tenant_user(app, f"{role.value}@test.com", role)
            _login(client, uid)
            assert client.get("/profile").status_code == 200

    def test_change_password_wrong_current(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "change_password",
            "current_password": "wrongpassword12",
            "new_password": "newpassword1234",
            "confirm_password": "newpassword1234",
        }, follow_redirects=True)
        assert b"incorrect" in resp.data.lower()

    def test_change_password_mismatch(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "change_password",
            "current_password": "correctpassword1",
            "new_password": "newpassword1234",
            "confirm_password": "differentpassw2",
        }, follow_redirects=True)
        assert b"match" in resp.data.lower()

    def test_change_password_too_short(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "change_password",
            "current_password": "correctpassword1",
            "new_password": "short",
            "confirm_password": "short",
        }, follow_redirects=True)
        assert b"12" in resp.data

    def test_change_password_success(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "change_password",
            "current_password": "correctpassword1",
            "new_password": "newpassword-1234",
            "confirm_password": "newpassword-1234",
        }, follow_redirects=True)
        assert b"updated" in resp.data.lower()
        with app.app_context():
            user = db.session.get(User, uid)
            assert bcrypt.checkpw(b"newpassword-1234", user.password_hash.encode())


# ── Demo multi-user slots ─────────────────────────────────────────────────────

class TestDemoMultiUser:
    @pytest.fixture()
    def demo_app(self):
        old = os.environ.get("FLASK_ENV")
        os.environ["FLASK_ENV"] = "demo"
        try:
            from init import create_app
            app = create_app()
            app.config["TESTING"] = True
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
            with app.app_context():
                db.create_all()
            yield app
            with app.app_context():
                db.drop_all()
        finally:
            if old is None:
                os.environ.pop("FLASK_ENV", None)
            else:
                os.environ["FLASK_ENV"] = old

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
                password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(owner)
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER))
            renter = User(
                email="demo-renter-1@openhangar.demo",
                password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(renter)
            db.session.flush()
            db.session.add(TenantUser(user_id=renter.id, tenant_id=tenant.id, role=Role.PILOT))
            slot = DemoSlot(id=1, tenant_id=tenant.id, user_id=owner.id,
                            renter_user_id=renter.id)
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
        _, renter_id = self._make_two_user_slot(demo_app)
        demo_client.post("/demo/enter", data={"role": "renter"})
        resp = demo_client.post("/aircraft/new", data={
            "registration": "OO-TST", "make": "Test", "model": "M",
        })
        assert resp.status_code == 403

    def test_landing_page_shows_two_demo_buttons(self, demo_app, demo_client):
        resp = demo_client.get("/")
        assert b"Owner" in resp.data
        assert b"Renter" in resp.data

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
        old = os.environ.get("FLASK_ENV")
        os.environ["FLASK_ENV"] = "demo"
        try:
            resp = client.get("/config/users/")
            assert resp.status_code == 403
        finally:
            if old is None:
                os.environ.pop("FLASK_ENV", None)
            else:
                os.environ["FLASK_ENV"] = old


# ── Profile: TOTP setup / confirm / disable ───────────────────────────────────

class TestProfileTOTP:
    def test_setup_totp_puts_secret_in_session(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/profile", data={"action": "setup_totp"})
        with client.session_transaction() as sess:
            assert "profile_totp_secret" in sess

    def test_setup_totp_renders_profile(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post("/profile", data={"action": "setup_totp"})
        assert resp.status_code == 200

    def test_confirm_totp_without_session_redirects(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "confirm_totp",
            "totp_code": "000000",
        })
        assert resp.status_code == 302

    def test_confirm_totp_invalid_code_shows_error(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/profile", data={"action": "setup_totp"})
        resp = client.post("/profile", data={
            "action": "confirm_totp",
            "totp_code": "000000",
        }, follow_redirects=True)
        assert b"invalid" in resp.data.lower()

    def test_confirm_totp_success_enables_2fa(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/profile", data={"action": "setup_totp"})
        with client.session_transaction() as sess:
            secret = sess["profile_totp_secret"]
        valid_code = pyotp.TOTP(secret).now()
        client.post("/profile", data={
            "action": "confirm_totp",
            "totp_code": valid_code,
        }, follow_redirects=True)
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret == secret

    def test_disable_totp_wrong_password_shows_error(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        with app.app_context():
            user = db.session.get(User, uid)
            user.totp_secret = pyotp.random_base32()
            db.session.commit()
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "disable_totp",
            "current_password": "wrongpassword12",
        }, follow_redirects=True)
        assert b"incorrect" in resp.data.lower()

    def test_disable_totp_success_clears_secret(self, app, client):
        _, uid = _make_tenant_user(app, "user@test.com", Role.ADMIN,
                                   password="correctpassword1")
        with app.app_context():
            user = db.session.get(User, uid)
            user.totp_secret = pyotp.random_base32()
            db.session.commit()
        _login(client, uid)
        resp = client.post("/profile", data={
            "action": "disable_totp",
            "current_password": "correctpassword1",
        }, follow_redirects=True)
        assert b"disabled" in resp.data.lower()
        with app.app_context():
            user = db.session.get(User, uid)
            assert user.totp_secret is None


# ── Invitation edge cases ─────────────────────────────────────────────────────

class TestInvitationEdgeCases:
    def test_invalid_role_falls_back_to_pilot(self, app, client):
        """users/routes.py:92-93 — ValueError in Role() caught, defaults to PILOT."""
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "not-a-role"})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.PILOT

    def test_admin_role_clamped_to_owner(self, app, client):
        """users/routes.py:95 — ADMIN role passed to invite is silently clamped to OWNER."""
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        client.post("/config/users/invite", data={"role": "admin"})
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.role == Role.OWNER

    def test_invite_with_email_triggers_send_path(self, app, client):
        """users/routes.py:110,117-129 — invite with email hits _try_send_invite_email."""
        tid, uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        _login(client, uid)
        resp = client.post("/config/users/invite", data={
            "role": "pilot",
            "email": "invited@test.com",
        })
        assert resp.status_code == 302
        with app.app_context():
            inv = UserInvitation.query.first()
            assert inv.email == "invited@test.com"

    def _make_invitation(self, app, tenant_id):
        with app.app_context():
            inv = UserInvitation(
                tenant_id=tenant_id,
                role=Role.PILOT,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            return inv.token

    def test_accept_invite_invalid_email(self, app, client):
        """users/routes.py:157 — invalid email address shows validation error."""
        tid, _ = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        token = self._make_invitation(app, tid)
        resp = client.post(f"/config/users/invite/{token}", data={
            "email": "not-an-email",
            "password": "securepass-123",
            "password2": "securepass-123",
        }, follow_redirects=True)
        assert b"valid email" in resp.data.lower()

    def test_accept_invite_duplicate_email(self, app, client):
        """users/routes.py:163 — already-registered email shows error."""
        tid, _ = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        token = self._make_invitation(app, tid)
        resp = client.post(f"/config/users/invite/{token}", data={
            "email": "admin@test.com",
            "password": "securepass-123",
            "password2": "securepass-123",
        }, follow_redirects=True)
        assert b"already exists" in resp.data.lower()


# ── change_role edge cases ────────────────────────────────────────────────────

class TestChangeRoleEdgeCases:
    def _setup_two_users(self, app):
        tid, admin_uid = _make_tenant_user(app, "admin@test.com", Role.ADMIN)
        with app.app_context():
            user2 = User(
                email="user2@test.com",
                password_hash=bcrypt.hashpw(b"pass-12-chars", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(user2)
            db.session.flush()
            db.session.add(TenantUser(user_id=user2.id, tenant_id=tid, role=Role.VIEWER))
            db.session.commit()
            user2_id = user2.id
        return admin_uid, user2_id

    def test_invalid_role_value_returns_400(self, app, client):
        """users/routes.py:207-208 — unrecognised role string → 400."""
        admin_uid, user2_id = self._setup_two_users(app)
        _login(client, admin_uid)
        resp = client.post(f"/config/users/{user2_id}/role", data={"role": "not-a-role"})
        assert resp.status_code == 400

    def test_admin_role_returns_400(self, app, client):
        """users/routes.py:210 — attempting to assign ADMIN role → 400."""
        admin_uid, user2_id = self._setup_two_users(app)
        _login(client, admin_uid)
        resp = client.post(f"/config/users/{user2_id}/role", data={"role": "admin"})
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
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            inv_id = inv.id
        _login(client, uid)
        resp = client.post(f"/config/users/invite/{inv_id}/revoke")
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(UserInvitation, inv_id) is None
