from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    jsonify,
    session,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_wtf.csrf import generate_csrf  # pyright: ignore[reportMissingImports]

from models import Aircraft, FlightEntry, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import user_can_access_aircraft  # pyright: ignore[reportMissingImports]

from offline.serialize import canonical_entry  # pyright: ignore[reportMissingImports]

offline_bp = Blueprint("offline", __name__)


def api_login_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Like @login_required, but returns JSON 401 instead of redirecting.

    Fetch/IndexedDB-driven callers cannot follow a redirect to the login
    page usefully — they need a status they can branch on.
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


@offline_bp.route("/api/offline/aircraft/<int:aircraft_id>/logbook")
@api_login_required
def aircraft_logbook_snapshot(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    entries = (
        FlightEntry.query.filter_by(aircraft_id=ac.id)
        .order_by(FlightEntry.date.asc(), FlightEntry.id.asc())
        .all()
    )
    return jsonify(
        {
            "aircraft": {
                "id": ac.id,
                "registration": ac.registration,
                "has_flight_counter": ac.has_flight_counter,
                "flight_counter_offset": str(ac.flight_counter_offset),
            },
            "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
            "entries": [
                {
                    "id": fe.id,
                    "fields": canonical_entry(fe, fe.crew),
                    "meta": {
                        "has_flight_counter_photo": bool(fe.flight_counter_photo),
                        "has_engine_counter_photo": bool(fe.engine_counter_photo),
                        "has_fuel_photo": bool(fe.fuel_photo),
                        "has_gps_track": fe.gps_track_id is not None,
                        "source": fe.source,
                        "created_at": fe.created_at.isoformat()
                        if fe.created_at
                        else None,
                    },
                }
                for fe in entries
            ],
        }
    )


@offline_bp.route("/api/offline/csrf")
@api_login_required
def csrf_token() -> ResponseReturnValue:
    return jsonify({"csrf_token": generate_csrf()})
