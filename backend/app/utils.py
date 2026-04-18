"""Shared utilities available to all blueprints."""
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
