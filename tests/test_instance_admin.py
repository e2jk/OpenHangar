"""Tests for Phase 29: Instance Super Admin & Multi-Tenant Provisioning."""

from datetime import datetime, timedelta, timezone

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    PasswordResetToken,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_user(
    app,
    email="admin@example.com",
    is_instance_admin=False,
    is_active=True,
    tenant_name="Test Hangar",
    role=Role.OWNER,
):
    with app.app_context():
        user = User(
            email=email,
            password_hash=_pw_hash.hash("password1234"),
            is_active=is_active,
            is_instance_admin=is_instance_admin,
        )
        db.session.add(user)
        db.session.flush()
        tenant = Tenant(name=tenant_name, is_active=True)
        db.session.add(tenant)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _make_second_tenant(app, name="Second Hangar"):
    with app.app_context():
        t = Tenant(name=name, is_active=True)
        db.session.add(t)
        db.session.commit()
        return t.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


# ── require_instance_admin decorator ─────────────────────────────────────────


class TestRequireInstanceAdmin:
    def test_non_admin_blocked_from_tenant_list(self, app, client):
        uid, _ = _make_user(app, email="plain@example.com", is_instance_admin=False)
        _login(client, uid)
        rv = client.get("/config/tenants")
        assert rv.status_code == 403

    def test_unauthenticated_redirected_from_tenant_list(self, app, client):
        rv = client.get("/config/tenants", follow_redirects=False)
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]

    def test_instance_admin_can_access_tenant_list(self, app, client):
        uid, _ = _make_user(app, email="superadmin@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.get("/config/tenants")
        assert rv.status_code == 200

    def test_instance_admin_can_access_config_settings(self, app, client):
        """Instance admin passes _block_in_demo even without a tenant role."""
        uid, _ = _make_user(app, email="ia@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.get("/config/")
        assert rv.status_code == 200


# ── Setup wizard stamps first user as instance admin ─────────────────────────


class TestSetupWizardFirstUser:
    def test_setup_wizard_creates_instance_admin(self, app, client):
        # The setup route requires no users to exist — use a fresh empty DB context.
        # Each test gets its own DB (from the conftest fixture), so this is safe.
        with client.session_transaction() as sess:
            sess["setup_email"] = "owner@wizard.com"
            sess["setup_password_hash"] = _pw_hash.hash("password1234")
            sess["setup_operating_model"] = "sole_pilot"
            sess["setup_totp_done"] = True
            sess["setup_totp_to_save"] = None

        rv = client.post("/setup", data={"step": "summary"})
        assert rv.status_code == 302

        with app.app_context():
            user = User.query.filter_by(email="owner@wizard.com").first()
            assert user is not None
            assert user.is_instance_admin is True

    def test_subsequent_invited_user_is_not_instance_admin(self, app, client):
        """A user who joins via invitation is NOT promoted to instance admin."""
        from models import UserInvitation  # pyright: ignore[reportMissingImports]

        uid, tid = _make_user(app, email="ia2@example.com", is_instance_admin=True)

        with app.app_context():
            inv = UserInvitation(
                tenant_id=tid,
                email="invited@example.com",
                role=Role.PILOT,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            db.session.add(inv)
            db.session.commit()
            token = inv.token

        rv = client.post(
            f"/config/users/invite/{token}",
            data={
                "name": "Invited Pilot",
                "email": "invited@example.com",
                "password": "password1234",
                "confirm_password": "password1234",
            },
        )
        assert rv.status_code in (200, 302)

        with app.app_context():
            invited = User.query.filter_by(email="invited@example.com").first()
            if invited:
                assert invited.is_instance_admin is False


# ── Login blocked for inactive-tenant users ───────────────────────────────────


class TestInactiveTenantLogin:
    def test_active_tenant_login_succeeds(self, app, client):
        _make_user(app, email="active@example.com")
        rv = client.post(
            "/login",
            data={"email": "active@example.com", "password": "password1234"},
            follow_redirects=False,
        )
        assert rv.status_code == 302

    def test_inactive_tenant_login_rejected(self, app, client):
        uid, tid = _make_user(app, email="blocked@example.com")
        with app.app_context():
            tenant = db.session.get(Tenant, tid)
            tenant.is_active = False
            db.session.commit()

        rv = client.post(
            "/login",
            data={"email": "blocked@example.com", "password": "password1234"},
            follow_redirects=False,
        )
        assert rv.status_code in (200, 302)
        if rv.status_code == 302:
            assert "/login" in rv.headers["Location"]

    def test_instance_admin_with_inactive_tenant_can_still_login(self, app, client):
        uid, tid = _make_user(
            app, email="ia_blocked@example.com", is_instance_admin=True
        )
        with app.app_context():
            tenant = db.session.get(Tenant, tid)
            tenant.is_active = False
            db.session.commit()

        rv = client.post(
            "/login",
            data={"email": "ia_blocked@example.com", "password": "password1234"},
            follow_redirects=False,
        )
        assert rv.status_code == 302
        assert "/login" not in rv.headers.get("Location", "")


# ── Create tenant ─────────────────────────────────────────────────────────────


class TestCreateTenant:
    def test_create_tenant_creates_tenant_profile_and_invitation(self, app, client):
        from models import TenantProfile, UserInvitation  # pyright: ignore[reportMissingImports]

        uid, _ = _make_user(app, email="ia3@example.com", is_instance_admin=True)
        _login(client, uid)

        rv = client.post(
            "/config/tenants/create",
            data={
                "name": "New Club",
                "admin_email": "newowner@club.com",
                "operating_model": "flight_club",
            },
            follow_redirects=False,
        )
        assert rv.status_code == 302

        with app.app_context():
            new_tenant = Tenant.query.filter_by(name="New Club").first()
            assert new_tenant is not None
            assert new_tenant.is_active is True

            profile = TenantProfile.query.filter_by(tenant_id=new_tenant.id).first()
            assert profile is not None

            invitation = (
                UserInvitation.query.filter_by(
                    tenant_id=new_tenant.id, email="newowner@club.com"
                )
                .filter_by(role=Role.OWNER)
                .first()
            )
            assert invitation is not None

    def test_create_tenant_requires_name(self, app, client):
        uid, _ = _make_user(app, email="ia4@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.post(
            "/config/tenants/create",
            data={"name": "", "admin_email": "x@x.com"},
        )
        assert rv.status_code == 200  # stays on form

    def test_create_tenant_requires_admin_email(self, app, client):
        uid, _ = _make_user(app, email="ia4b@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.post(
            "/config/tenants/create",
            data={"name": "Club X", "admin_email": ""},
        )
        assert rv.status_code == 200  # stays on form

    def test_create_tenant_get_renders_form(self, app, client):
        uid, _ = _make_user(app, email="ia4c@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.get("/config/tenants/create")
        assert rv.status_code == 200

    def test_create_tenant_invalid_operating_model_is_none(self, app, client):
        from models import TenantProfile  # pyright: ignore[reportMissingImports]

        uid, _ = _make_user(app, email="ia4d@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.post(
            "/config/tenants/create",
            data={
                "name": "Invalid Model Club",
                "admin_email": "x@x.com",
                "operating_model": "not_a_valid_model",
            },
            follow_redirects=False,
        )
        assert rv.status_code == 302
        with app.app_context():
            t = Tenant.query.filter_by(name="Invalid Model Club").first()
            assert t is not None
            profile = TenantProfile.query.filter_by(tenant_id=t.id).first()
            assert profile is not None
            assert profile.operating_model is None

    def test_non_admin_cannot_create_tenant(self, app, client):
        uid, _ = _make_user(app, email="plain2@example.com", is_instance_admin=False)
        _login(client, uid)
        rv = client.post(
            "/config/tenants/create",
            data={"name": "Sneaky Tenant", "admin_email": "x@x.com"},
        )
        assert rv.status_code == 403


# ── Deactivate / reactivate tenant ───────────────────────────────────────────


class TestToggleTenantActive:
    def test_deactivate_tenant(self, app, client):
        uid, _ = _make_user(app, email="ia5@example.com", is_instance_admin=True)
        _login(client, uid)
        t2_id = _make_second_tenant(app, "Toggle Hangar")

        rv = client.post(f"/config/tenants/{t2_id}/toggle", follow_redirects=False)
        assert rv.status_code == 302

        with app.app_context():
            t2 = db.session.get(Tenant, t2_id)
            assert t2.is_active is False

    def test_reactivate_tenant(self, app, client):
        uid, _ = _make_user(app, email="ia6@example.com", is_instance_admin=True)
        _login(client, uid)
        t2_id = _make_second_tenant(app, "Toggle2 Hangar")

        with app.app_context():
            t2 = db.session.get(Tenant, t2_id)
            t2.is_active = False
            db.session.commit()

        rv = client.post(f"/config/tenants/{t2_id}/toggle", follow_redirects=False)
        assert rv.status_code == 302

        with app.app_context():
            t2 = db.session.get(Tenant, t2_id)
            assert t2.is_active is True

    def test_non_admin_cannot_toggle(self, app, client):
        uid, _ = _make_user(app, email="plain3@example.com", is_instance_admin=False)
        _login(client, uid)
        t2_id = _make_second_tenant(app, "No Toggle Hangar")
        rv = client.post(f"/config/tenants/{t2_id}/toggle")
        assert rv.status_code == 403

    def test_toggle_missing_tenant_returns_404(self, app, client):
        uid, _ = _make_user(app, email="ia5b@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.post("/config/tenants/99999/toggle")
        assert rv.status_code == 404


# ── Password reset token ──────────────────────────────────────────────────────


class TestPasswordResetToken:
    def _make_reset_token(self, app, admin_id, user_id, expired=False, used=False):
        with app.app_context():
            now = datetime.now(timezone.utc)
            token = PasswordResetToken(
                user_id=user_id,
                generated_by_user_id=admin_id,
                expires_at=(
                    now - timedelta(hours=1) if expired else now + timedelta(hours=24)
                ),
                used_at=now if used else None,
            )
            db.session.add(token)
            db.session.commit()
            return token.token

    def test_instance_admin_can_generate_reset_token(self, app, client):
        ia_id, _ = _make_user(app, email="ia7@example.com", is_instance_admin=True)
        owner_id, owner_tid = _make_user(
            app, email="owner7@example.com", tenant_name="Owner Hangar"
        )
        _login(client, ia_id)
        rv = client.post(
            f"/config/tenants/{owner_tid}/reset-password",
            data={"owner_user_id": owner_id},
            follow_redirects=False,
        )
        assert rv.status_code == 200  # displays token page
        with app.app_context():
            token = PasswordResetToken.query.filter_by(user_id=owner_id).first()
            assert token is not None
            assert token.used_at is None

    def test_non_admin_cannot_generate_reset_token(self, app, client):
        plain_id, _ = _make_user(app, email="plain4@example.com")
        owner_id, owner_tid = _make_user(
            app, email="owner8@example.com", tenant_name="Owner Hangar 2"
        )
        _login(client, plain_id)
        rv = client.post(
            f"/config/tenants/{owner_tid}/reset-password",
            data={"owner_user_id": owner_id},
        )
        assert rv.status_code == 403

    def test_reset_missing_tenant_returns_404(self, app, client):
        ia_id, _ = _make_user(app, email="ia7b@example.com", is_instance_admin=True)
        _login(client, ia_id)
        rv = client.post(
            "/config/tenants/99999/reset-password",
            data={"owner_user_id": 1},
        )
        assert rv.status_code == 404

    def test_reset_without_owner_user_id_redirects(self, app, client):
        ia_id, _ = _make_user(app, email="ia7c@example.com", is_instance_admin=True)
        _, tid = _make_user(
            app, email="owner7c@example.com", tenant_name="Reset Hangar C"
        )
        _login(client, ia_id)
        rv = client.post(
            f"/config/tenants/{tid}/reset-password",
            data={},
            follow_redirects=False,
        )
        assert rv.status_code == 302
        assert "tenants" in rv.headers["Location"]

    def test_reset_non_owner_user_returns_403(self, app, client):
        ia_id, _ = _make_user(app, email="ia7d@example.com", is_instance_admin=True)
        _, tid = _make_user(
            app, email="owner7d@example.com", tenant_name="Reset Hangar D"
        )
        pilot_id, _ = _make_user(
            app,
            email="pilot7d@example.com",
            tenant_name="Reset Hangar D2",
            role=Role.PILOT,
        )
        _login(client, ia_id)
        rv = client.post(
            f"/config/tenants/{tid}/reset-password",
            data={"owner_user_id": pilot_id},
        )
        assert rv.status_code == 403

    def test_valid_token_renders_password_form(self, app, client):
        ia_id, _ = _make_user(app, email="ia8@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user8@example.com", tenant_name="Token Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id)
        rv = client.get(f"/reset-password/{tok}")
        assert rv.status_code == 200

    def test_expired_token_redirects_to_login(self, app, client):
        ia_id, _ = _make_user(app, email="ia9@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user9@example.com", tenant_name="Expired Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id, expired=True)
        rv = client.get(f"/reset-password/{tok}", follow_redirects=False)
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]

    def test_used_token_redirects_to_login(self, app, client):
        ia_id, _ = _make_user(app, email="ia10@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user10@example.com", tenant_name="Used Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id, used=True)
        rv = client.get(f"/reset-password/{tok}", follow_redirects=False)
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]

    def test_valid_token_resets_password_and_marks_used(self, app, client):
        ia_id, _ = _make_user(app, email="ia11@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user11@example.com", tenant_name="Reset Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id)

        rv = client.post(
            f"/reset-password/{tok}",
            data={
                "new_password": "newpassword5678",
                "confirm_password": "newpassword5678",
            },
            follow_redirects=False,
        )
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]

        with app.app_context():
            token = PasswordResetToken.query.filter_by(user_id=user_id).first()
            assert token.used_at is not None

    def test_short_password_rejected(self, app, client):
        ia_id, _ = _make_user(app, email="ia12@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user12@example.com", tenant_name="Short Pass Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id)

        rv = client.post(
            f"/reset-password/{tok}",
            data={"new_password": "short", "confirm_password": "short"},
        )
        assert rv.status_code == 200  # stays on form

    def test_mismatched_passwords_rejected(self, app, client):
        ia_id, _ = _make_user(app, email="ia13@example.com", is_instance_admin=True)
        user_id, _ = _make_user(
            app, email="user13@example.com", tenant_name="Mismatch Hangar"
        )
        tok = self._make_reset_token(app, ia_id, user_id)

        rv = client.post(
            f"/reset-password/{tok}",
            data={
                "new_password": "newpassword5678",
                "confirm_password": "differentpassword",
            },
        )
        assert rv.status_code == 200  # stays on form


# ── require_instance_admin decorator (unauthenticated redirect) ───────────────


class TestRequireInstanceAdminDecorator:
    def test_unauthenticated_redirected_to_login_by_decorator(self, app, client):
        """The @require_instance_admin decorator redirects unauthenticated requests."""
        rv = client.get("/config/tenants", follow_redirects=False)
        assert rv.status_code == 302
        assert "/login" in rv.headers["Location"]


# ── PasswordResetToken.is_expired with timezone-aware datetime ─────────────────


class TestPasswordResetTokenModel:
    def test_is_expired_with_aware_future_datetime_returns_false(self, app, client):
        with app.app_context():
            now = datetime.now(timezone.utc)
            token = PasswordResetToken(
                user_id=1,  # dummy — not persisted
                expires_at=now + timedelta(hours=24),
            )
            token.expires_at = now + timedelta(hours=24)
            assert token.is_expired is False

    def test_is_expired_with_aware_past_datetime_returns_true(self, app, client):
        with app.app_context():
            now = datetime.now(timezone.utc)
            token = PasswordResetToken(
                user_id=1,
                expires_at=now - timedelta(hours=1),
            )
            assert token.is_expired is True


# ── Solo-guard: Tenants section hidden in single-tenant install ───────────────


class TestSoloGuard:
    def test_tenants_section_absent_when_single_tenant(self, app, client):
        uid, _ = _make_user(app, email="solo@example.com", is_instance_admin=True)
        _login(client, uid)
        rv = client.get("/config/")
        assert rv.status_code == 200
        # Single tenant shows upgrade path, not the full management button
        assert b"Manage tenants" not in rv.data

    def test_tenants_section_visible_when_multi_tenant(self, app, client):
        uid, _ = _make_user(app, email="multi@example.com", is_instance_admin=True)
        _make_second_tenant(app, "Second Org")
        _login(client, uid)
        rv = client.get("/config/")
        assert rv.status_code == 200
        assert b"Manage tenants" in rv.data
