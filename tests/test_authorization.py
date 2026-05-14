"""
Tests for Phase 23 — Authorization Service, all-planes access, capability flags,
and maintenance view level.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    PermissionBit,
    Role,
    Tenant,
    TenantUser,
    User,
    UserAircraftAccess,
    UserAllAircraftAccess,
    db,
)
from services.authorization import AuthorizationService  # pyright: ignore[reportMissingImports]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_env(app, role, *, is_pilot=False, is_maintenance=False, view_only=False):
    """Create tenant + user with the given role and optional flags. Returns (tid, uid, acid)."""
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=f"user-{role.value}@test.dev",
            password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(),
            is_active=True,
            is_pilot=is_pilot,
            is_maintenance=is_maintenance,
            view_only=view_only,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        ac = Aircraft(tenant_id=tenant.id, registration="OO-TST", make="Test", model="T")
        db.session.add(ac)
        db.session.commit()
        return tenant.id, user.id, ac.id


def _grant_specific(app, user_id, aircraft_id):
    with app.app_context():
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=aircraft_id))
        db.session.commit()


def _grant_all_planes(app, user_id, tenant_id):
    with app.app_context():
        db.session.add(UserAllAircraftAccess(user_id=user_id, tenant_id=tenant_id))
        db.session.commit()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


# ── PermissionBit defaults ────────────────────────────────────────────────────

class TestPermissionBitDefaults:
    def test_admin_has_all(self):
        assert PermissionBit.ROLE_DEFAULTS["admin"] == PermissionBit.ALL

    def test_owner_has_all(self):
        assert PermissionBit.ROLE_DEFAULTS["owner"] == PermissionBit.ALL

    def test_pilot_has_view_logbook_reserve(self):
        mask = PermissionBit.ROLE_DEFAULTS["pilot"]
        assert mask & PermissionBit.VIEW_AIRCRAFT
        assert mask & PermissionBit.WRITE_LOGBOOK
        assert mask & PermissionBit.RESERVE_AIRCRAFT
        assert not (mask & PermissionBit.EDIT_AIRCRAFT)
        assert not (mask & PermissionBit.WRITE_MAINTENANCE)

    def test_maintenance_has_write_maintenance(self):
        mask = PermissionBit.ROLE_DEFAULTS["maintenance"]
        assert mask & PermissionBit.WRITE_MAINTENANCE
        assert mask & PermissionBit.EDIT_COMPONENTS
        assert not (mask & PermissionBit.WRITE_LOGBOOK)

    def test_viewer_has_full_read_only(self):
        mask = PermissionBit.ROLE_DEFAULTS["viewer"]
        assert mask & PermissionBit.VIEW_AIRCRAFT
        assert mask & PermissionBit.READ_MAINT_FULL
        assert not (mask & PermissionBit.EDIT_AIRCRAFT)
        assert not (mask & PermissionBit.WRITE_LOGBOOK)


# ── AuthorizationService.effective_mask ──────────────────────────────────────

class TestEffectiveMask:
    def test_admin_always_gets_all(self, app):
        tid, uid, acid = _make_env(app, Role.ADMIN)
        with app.app_context():
            assert AuthorizationService.effective_mask(uid, acid, tid) == PermissionBit.ALL

    def test_owner_always_gets_all(self, app):
        tid, uid, acid = _make_env(app, Role.OWNER)
        with app.app_context():
            assert AuthorizationService.effective_mask(uid, acid, tid) == PermissionBit.ALL

    def test_pilot_no_access_row_gets_zero(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        with app.app_context():
            assert AuthorizationService.effective_mask(uid, acid, tid) == 0

    def test_pilot_with_specific_row_gets_role_default(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_specific(app, uid, acid)
        with app.app_context():
            mask = AuthorizationService.effective_mask(uid, acid, tid)
            assert mask == PermissionBit.ROLE_DEFAULTS["pilot"]

    def test_all_planes_row_grants_full_access(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_all_planes(app, uid, tid)
        with app.app_context():
            mask = AuthorizationService.effective_mask(uid, acid, tid)
            assert mask == PermissionBit.ROLE_DEFAULTS["pilot"]

    def test_all_planes_overrides_specific_row(self, app):
        """all_planes row takes priority over per-aircraft row."""
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_all_planes(app, uid, tid)
        _grant_specific(app, uid, acid)
        with app.app_context():
            # With all_planes present, specific row is irrelevant
            mask = AuthorizationService.effective_mask(uid, acid, tid)
            assert mask == PermissionBit.ROLE_DEFAULTS["pilot"]

    def test_custom_mask_on_specific_row(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        with app.app_context():
            db.session.add(UserAircraftAccess(
                user_id=uid, aircraft_id=acid,
                permissions_mask=PermissionBit.VIEW_AIRCRAFT,
            ))
            db.session.commit()
            mask = AuthorizationService.effective_mask(uid, acid, tid)
            assert mask == PermissionBit.VIEW_AIRCRAFT
            assert not (mask & PermissionBit.WRITE_LOGBOOK)

    def test_view_only_flag_strips_write_bits(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT, view_only=True)
        _grant_specific(app, uid, acid)
        with app.app_context():
            mask = AuthorizationService.effective_mask(uid, acid, tid)
            assert not (mask & PermissionBit.WRITE_LOGBOOK)
            assert not (mask & PermissionBit.RESERVE_AIRCRAFT)
            assert mask & PermissionBit.VIEW_AIRCRAFT

    def test_nonexistent_user_returns_zero(self, app):
        tid, _, acid = _make_env(app, Role.PILOT)
        with app.app_context():
            assert AuthorizationService.effective_mask(99999, acid, tid) == 0


# ── AuthorizationService.can ─────────────────────────────────────────────────

class TestCan:
    def test_owner_can_all_actions(self, app):
        tid, uid, acid = _make_env(app, Role.OWNER)
        with app.app_context():
            assert AuthorizationService.can(uid, "view_aircraft", acid, tid)
            assert AuthorizationService.can(uid, "edit_aircraft", acid, tid)
            assert AuthorizationService.can(uid, "log_flight", acid, tid)
            assert AuthorizationService.can(uid, "edit_maintenance", acid, tid)

    def test_pilot_cannot_edit_aircraft_without_access(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        with app.app_context():
            assert not AuthorizationService.can(uid, "view_aircraft", acid, tid)
            assert not AuthorizationService.can(uid, "log_flight", acid, tid)

    def test_pilot_can_log_with_access(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_specific(app, uid, acid)
        with app.app_context():
            assert AuthorizationService.can(uid, "log_flight", acid, tid)
            assert not AuthorizationService.can(uid, "edit_aircraft", acid, tid)

    def test_unknown_action_returns_false(self, app):
        tid, uid, acid = _make_env(app, Role.OWNER)
        with app.app_context():
            assert not AuthorizationService.can(uid, "fly_to_the_moon", acid, tid)

    def test_view_maintenance_with_either_read_bit(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_specific(app, uid, acid)
        with app.app_context():
            # Pilot has READ_MAINT_LIMITED → view_maintenance should pass
            assert AuthorizationService.can(uid, "view_maintenance", acid, tid)


# ── AuthorizationService.maintenance_view_level ──────────────────────────────

class TestMaintenanceViewLevel:
    def test_owner_gets_full(self, app):
        tid, uid, acid = _make_env(app, Role.OWNER)
        with app.app_context():
            assert AuthorizationService.maintenance_view_level(uid, acid, tid) == "full"

    def test_pilot_with_access_gets_limited(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        _grant_specific(app, uid, acid)
        with app.app_context():
            assert AuthorizationService.maintenance_view_level(uid, acid, tid) == "limited"

    def test_maintenance_role_gets_full(self, app):
        tid, uid, acid = _make_env(app, Role.MAINTENANCE)
        _grant_specific(app, uid, acid)
        with app.app_context():
            assert AuthorizationService.maintenance_view_level(uid, acid, tid) == "full"

    def test_viewer_gets_full(self, app):
        tid, uid, acid = _make_env(app, Role.VIEWER)
        _grant_specific(app, uid, acid)
        with app.app_context():
            assert AuthorizationService.maintenance_view_level(uid, acid, tid) == "full"

    def test_no_access_row_gets_none(self, app):
        tid, uid, acid = _make_env(app, Role.PILOT)
        with app.app_context():
            assert AuthorizationService.maintenance_view_level(uid, acid, tid) == "none"


# ── all-planes route integration ─────────────────────────────────────────────

class TestAllPlanesRoute:
    def _make_owner_and_pilot(self, app):
        with app.app_context():
            tenant = Tenant(name="AP Hangar")
            db.session.add(tenant)
            db.session.flush()
            owner = User(email="owner@ap.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            pilot = User(email="pilot@ap.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            db.session.add_all([owner, pilot])
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER))
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tenant.id, role=Role.PILOT))
            db.session.commit()
            return tenant.id, owner.id, pilot.id

    def test_toggle_all_planes_on(self, app, client):
        tid, owner_id, pilot_id = self._make_owner_and_pilot(app)
        _login(client, owner_id)
        resp = client.post(f"/config/users/{pilot_id}/all-planes", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert UserAllAircraftAccess.query.filter_by(
                user_id=pilot_id, tenant_id=tid
            ).first() is not None

    def test_toggle_all_planes_off(self, app, client):
        tid, owner_id, pilot_id = self._make_owner_and_pilot(app)
        _grant_all_planes(app, pilot_id, tid)
        _login(client, owner_id)
        resp = client.post(f"/config/users/{pilot_id}/all-planes", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert UserAllAircraftAccess.query.filter_by(
                user_id=pilot_id, tenant_id=tid
            ).first() is None

    def test_owner_cannot_toggle_own_all_planes(self, app, client):
        tid, owner_id, _ = self._make_owner_and_pilot(app)
        _login(client, owner_id)
        # owner trying to toggle all-planes for themselves → info flash, no row created
        resp = client.post(f"/config/users/{owner_id}/all-planes", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert UserAllAircraftAccess.query.filter_by(
                user_id=owner_id, tenant_id=tid
            ).first() is None


# ── User flags route integration ──────────────────────────────────────────────

class TestUserFlagsRoute:
    def _make_owner_and_pilot(self, app):
        with app.app_context():
            tenant = Tenant(name="Flags Hangar")
            db.session.add(tenant)
            db.session.flush()
            owner = User(email="owner@flags.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            pilot = User(email="pilot@flags.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            db.session.add_all([owner, pilot])
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER))
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tenant.id, role=Role.PILOT))
            db.session.commit()
            return owner.id, pilot.id

    def test_set_is_pilot_flag(self, app, client):
        owner_id, pilot_id = self._make_owner_and_pilot(app)
        _login(client, owner_id)
        resp = client.post(f"/config/users/{pilot_id}/flags",
                           data={"is_pilot": "on"}, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            u = db.session.get(User, pilot_id)
            assert u.is_pilot is True
            assert u.is_maintenance is False
            assert u.view_only is False

    def test_set_view_only_flag(self, app, client):
        owner_id, pilot_id = self._make_owner_and_pilot(app)
        _login(client, owner_id)
        resp = client.post(f"/config/users/{pilot_id}/flags",
                           data={"view_only": "on"}, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            u = db.session.get(User, pilot_id)
            assert u.view_only is True
            assert u.is_pilot is False

    def test_cannot_change_own_flags(self, app, client):
        owner_id, _ = self._make_owner_and_pilot(app)
        _login(client, owner_id)
        resp = client.post(f"/config/users/{owner_id}/flags",
                           data={"is_pilot": "on"}, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            u = db.session.get(User, owner_id)
            assert u.is_pilot is False  # unchanged


# ── accessible_aircraft with all-planes ──────────────────────────────────────

class TestAccessibleAircraftAllPlanes:
    def test_all_planes_user_sees_all_aircraft(self, app, client):
        with app.app_context():
            tenant = Tenant(name="AP Fleet")
            db.session.add(tenant)
            db.session.flush()
            user = User(email="ap@fleet.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            db.session.add(user)
            db.session.flush()
            db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT))
            ac1 = Aircraft(tenant_id=tenant.id, registration="OO-AA", make="A", model="1")
            ac2 = Aircraft(tenant_id=tenant.id, registration="OO-BB", make="B", model="2")
            db.session.add_all([ac1, ac2])
            db.session.add(UserAllAircraftAccess(user_id=user.id, tenant_id=tenant.id))
            db.session.commit()
            uid = user.id

        _login(client, uid)
        # Aircraft list uses accessible_aircraft — both should appear
        resp = client.get("/aircraft/")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert "OO-AA" in data
        assert "OO-BB" in data

    def test_pilot_without_all_planes_sees_only_granted(self, app, client):
        with app.app_context():
            tenant = Tenant(name="Partial Fleet")
            db.session.add(tenant)
            db.session.flush()
            user = User(email="partial@fleet.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            db.session.add(user)
            db.session.flush()
            db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT))
            ac1 = Aircraft(tenant_id=tenant.id, registration="OO-CC", make="C", model="3")
            ac2 = Aircraft(tenant_id=tenant.id, registration="OO-DD", make="D", model="4")
            db.session.add_all([ac1, ac2])
            db.session.flush()
            db.session.add(UserAircraftAccess(user_id=user.id, aircraft_id=ac1.id))
            db.session.commit()
            uid = user.id

        _login(client, uid)
        # Aircraft list — pilot should see only granted aircraft
        resp = client.get("/aircraft/")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert "OO-CC" in data
        assert "OO-DD" not in data


# ── Maintenance list view level integration ───────────────────────────────────

class TestMaintenanceListViewLevel:
    def _setup(self, app):
        from models import MaintenanceTrigger, TriggerType
        from datetime import date
        with app.app_context():
            tenant = Tenant(name="MV Hangar")
            db.session.add(tenant)
            db.session.flush()
            owner = User(email="owner@mv.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            pilot = User(email="pilot@mv.dev", password_hash=bcrypt.hashpw(b"password-12-chars", bcrypt.gensalt()).decode(), is_active=True)
            db.session.add_all([owner, pilot])
            db.session.flush()
            db.session.add(TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER))
            db.session.add(TenantUser(user_id=pilot.id, tenant_id=tenant.id, role=Role.PILOT))
            ac = Aircraft(tenant_id=tenant.id, registration="OO-MV", make="MV", model="T")
            db.session.add(ac)
            db.session.flush()
            db.session.add(UserAircraftAccess(user_id=pilot.id, aircraft_id=ac.id))
            # Add one overdue trigger and one ok trigger
            t_overdue = MaintenanceTrigger(
                aircraft_id=ac.id, name="Overdue check",
                trigger_type=TriggerType.CALENDAR,
                due_date=date(2020, 1, 1),
            )
            t_ok = MaintenanceTrigger(
                aircraft_id=ac.id, name="OK check",
                trigger_type=TriggerType.CALENDAR,
                due_date=date(2099, 1, 1),
            )
            db.session.add_all([t_overdue, t_ok])
            db.session.commit()
            return tenant.id, owner.id, pilot.id, ac.id

    def test_owner_sees_all_triggers(self, app, client):
        _, owner_id, _, ac_id = self._setup(app)
        _login(client, owner_id)
        resp = client.get(f"/aircraft/{ac_id}/maintenance")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert "Overdue check" in data
        assert "OK check" in data
        assert "limited view" not in data

    def test_pilot_limited_view_shows_only_open_items(self, app, client):
        _, _, pilot_id, ac_id = self._setup(app)
        _login(client, pilot_id)
        resp = client.get(f"/aircraft/{ac_id}/maintenance")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert "Overdue check" in data
        # OK item (not overdue and not due_soon) should be hidden in limited view
        assert "OK check" not in data
        assert "limited view" in data.lower() or "vue limit" in data.lower() or "beperkte weergave" in data.lower()
