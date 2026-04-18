"""Shared utilities available to all blueprints."""
from collections import defaultdict
from functools import wraps

from flask import redirect, session, url_for # pyright: ignore[reportMissingImports]


def login_required(f):
    """Redirect unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def compute_aircraft_statuses(aircraft_list, triggers, hobbs_by_id):
    """Return {aircraft_id: 'ok'|'due_soon'|'overdue'} for every aircraft.

    Worst-case across all triggers wins: overdue > due_soon > ok.
    Aircraft with no triggers are 'ok'.
    """
    by_aircraft = defaultdict(list)
    for t in triggers:
        by_aircraft[t.aircraft_id].append(t)

    result = {}
    for ac in aircraft_list:
        hobbs = hobbs_by_id.get(ac.id)
        statuses = [t.status(hobbs) for t in by_aircraft.get(ac.id, [])]
        if "overdue" in statuses:
            result[ac.id] = "overdue"
        elif "due_soon" in statuses:
            result[ac.id] = "due_soon"
        else:
            result[ac.id] = "ok"
    return result
