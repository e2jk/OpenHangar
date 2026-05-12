"""Shared utilities available to all blueprints."""
from collections import defaultdict
from functools import wraps

from flask import abort, redirect, session, url_for # pyright: ignore[reportMissingImports]


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
