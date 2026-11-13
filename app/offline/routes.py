"""Read-only offline logbooks: snapshot APIs and the pages that render them.

Offline *editing* was removed: nothing here (or in the browser) queues or
replays a write. While online, opening a logbook stores a snapshot of it in
the browser (IndexedDB, per account — see offline_identity.js); the offline
logbook pages render that snapshot, with or without a connection.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    jsonify,
    render_template,
    session,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    TenantUser,
    db,
)
from sqlalchemy import or_  # pyright: ignore[reportMissingImports]
from utils import (  # pyright: ignore[reportMissingImports]
    login_required,
    require_pilot_access,
    user_can_access_aircraft,
)

from offline.serialize import (  # pyright: ignore[reportMissingImports]
    aircraft_logbook_row,
    pilot_logbook_row,
)

offline_bp = Blueprint("offline", __name__)


def api_login_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Like @login_required, but returns JSON 401 instead of redirecting.

    Fetch callers cannot follow a redirect to the login page usefully —
    they need a status they can branch on.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not session.get("user_id"):
            return jsonify({"status": "auth"}), 401
        return f(*args, **kwargs)

    return decorated


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    """Mirrors flights._get_aircraft_or_404 — same tenant/access guard."""
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
        abort(404)
    return ac


@offline_bp.route("/api/offline/aircraft/<aircraft_ref:aircraft_id>/logbook")
@api_login_required
def aircraft_logbook_snapshot(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    entries = (
        Flight.query.filter_by(aircraft_id=ac.id)
        .order_by(Flight.date.asc(), Flight.id.asc())
        .all()
    )
    return jsonify(
        {
            "aircraft": {"id": ac.id, "registration": ac.registration},
            "snapshot_taken_at": datetime.now(UTC).isoformat(),
            "entries": [
                {"id": fe.id, "fields": aircraft_logbook_row(fe)} for fe in entries
            ],
        }
    )


@offline_bp.route("/api/offline/pilot/logbook")
@api_login_required
@require_pilot_access
def pilot_logbook_snapshot() -> ResponseReturnValue:
    """The pilot's whole logbook: the same rows as the online pilot logbook."""
    uid = int(session["user_id"])
    entries = (
        Flight.query.filter(
            or_(Flight.pic_user_id == uid, Flight.second_crew_user_id == uid)
        )
        .order_by(Flight.date.asc(), Flight.id.asc())
        .all()
    )
    return jsonify(
        {
            "snapshot_taken_at": datetime.now(UTC).isoformat(),
            "entries": [
                {"id": fe.id, "fields": pilot_logbook_row(fe, uid)} for fe in entries
            ],
        }
    )


@offline_bp.route("/api/offline/identity")
@api_login_required
def identity_check() -> ResponseReturnValue:
    """Lets the service worker learn the current login (identity headers,
    added to every response by init.py) without loading a page."""
    return "", 204


@offline_bp.route("/aircraft/<aircraft_ref:aircraft_id>/logbook/offline")
@login_required
@require_pilot_access
def aircraft_logbook(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    return render_template("offline/aircraft_logbook.html", aircraft=ac)


@offline_bp.route("/pilot/logbook/offline")
@login_required
@require_pilot_access
def pilot_logbook() -> ResponseReturnValue:
    return render_template("offline/pilot_logbook.html")
