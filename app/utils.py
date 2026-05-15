"""Shared utilities available to all blueprints."""

from collections import defaultdict
from functools import wraps

from flask import abort, redirect, session, url_for  # pyright: ignore[reportMissingImports]


def login_required(f):
    """Redirect unauthenticated users to the login page."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


def current_user_role():
    """Return the Role of the current user in their tenant, or None."""
    from models import TenantUser

    user_id = session.get("user_id")
    if not user_id:
        return None
    tu = TenantUser.query.filter_by(user_id=user_id).first()
    return tu.role if tu else None


def require_role(*roles):
    """Decorator: abort 403 if the current user's role is not in *roles*."""

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if current_user_role() not in roles:
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


def require_pilot_access(f):
    """Decorator: abort 403 unless the user has pilot access.

    Pilot access is granted by ADMIN/OWNER/PILOT/STUDENT/INSTRUCTOR role,
    or by the per-user is_pilot capability flag.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        from models import Role, User, db

        role = current_user_role()
        if role in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.STUDENT, Role.INSTRUCTOR):
            return f(*args, **kwargs)
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_pilot:
                return f(*args, **kwargs)
        return abort(403)

    return decorated


def require_maint_access(f):
    """Decorator: abort 403 unless the user has maintenance access.

    Maintenance access is granted by ADMIN/OWNER/MAINTENANCE/INSTRUCTOR role,
    or by the per-user is_maintenance capability flag.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        from models import Role, User, db

        role = current_user_role()
        if role in (Role.ADMIN, Role.OWNER, Role.MAINTENANCE, Role.INSTRUCTOR):
            return f(*args, **kwargs)
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_maintenance:
                return f(*args, **kwargs)
        return abort(403)

    return decorated


def user_can_access_aircraft(aircraft_id: int) -> bool:
    """Return True when the current user may access this aircraft.

    ADMIN and OWNER bypass the check entirely.  Other roles need either a
    UserAllAircraftAccess row (all-planes grant) or a per-aircraft
    UserAircraftAccess row.
    """
    from models import Role, TenantUser, UserAircraftAccess, UserAllAircraftAccess

    role = current_user_role()
    if role in (Role.ADMIN, Role.OWNER):
        return True
    uid = session.get("user_id")
    if not uid:
        return False
    tu = TenantUser.query.filter_by(user_id=uid).first()
    if (
        tu
        and UserAllAircraftAccess.query.filter_by(
            user_id=uid, tenant_id=tu.tenant_id
        ).first()
    ):
        return True
    return (
        UserAircraftAccess.query.filter_by(user_id=uid, aircraft_id=aircraft_id).first()
        is not None
    )


def accessible_aircraft(tenant_id: int):
    """Return a query of Aircraft the current user is allowed to see.

    ADMIN and OWNER see every aircraft in the tenant.  A user with a
    UserAllAircraftAccess row for the tenant also sees all aircraft.
    Other roles see only aircraft granted via UserAircraftAccess.
    """
    from models import Aircraft, Role, UserAircraftAccess, UserAllAircraftAccess

    base = Aircraft.query.filter_by(tenant_id=tenant_id).order_by(Aircraft.registration)
    role = current_user_role()
    if role in (Role.ADMIN, Role.OWNER):
        return base
    uid = session.get("user_id")
    if not uid:
        from sqlalchemy import false

        return base.filter(false())
    if UserAllAircraftAccess.query.filter_by(user_id=uid, tenant_id=tenant_id).first():
        return base
    ids = [
        row.aircraft_id
        for row in (
            UserAircraftAccess.query.filter_by(user_id=uid)
            .with_entities(UserAircraftAccess.aircraft_id)
            .all()
        )
    ]
    if not ids:
        from sqlalchemy import false

        return base.filter(false())
    return base.filter(Aircraft.id.in_(ids))


def compute_aircraft_statuses(aircraft_list, triggers, hobbs_by_id):
    """Return {aircraft_id: 'grounded'|'overdue'|'due_soon'|'ok'} for every aircraft.

    Grounded (unresolved grounding snag) takes priority over maintenance status.
    Among maintenance: overdue > due_soon > ok.
    """
    by_aircraft = defaultdict(list)
    for t in triggers:
        by_aircraft[t.aircraft_id].append(t)

    result = {}
    for ac in aircraft_list:
        if ac.is_grounded:
            result[ac.id] = "grounded"
            continue
        hobbs = hobbs_by_id.get(ac.id)
        statuses = [t.status(hobbs) for t in by_aircraft.get(ac.id, [])]
        if "overdue" in statuses:
            result[ac.id] = "overdue"
        elif "due_soon" in statuses:
            result[ac.id] = "due_soon"
        else:
            result[ac.id] = "ok"
    return result
