import decimal
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, cast

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    session,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from flask_wtf.csrf import (  # pyright: ignore[reportMissingImports]
    CSRFError,
    generate_csrf,
)

from flights.form_parsing import (  # pyright: ignore[reportMissingImports]
    apply_flight_fields,
    parse_flight_fields,
)
from flights.routes import (  # pyright: ignore[reportMissingImports]
    _check_flight_hour_milestone,
    _find_duplicate_flight,
)
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Flight,
    TenantUser,
    db,
)
from pilots.form_parsing import (  # pyright: ignore[reportMissingImports]
    apply_pilot_fields,
    parse_pilot_fields,
)
from utils import (  # pyright: ignore[reportMissingImports]
    login_required,
    require_pilot_access,
    user_can_access_aircraft,
)

from offline.serialize import (  # pyright: ignore[reportMissingImports]
    FLIGHT_EDITABLE_FIELDS,
    PILOT_EDITABLE_FIELDS,
    canonical_entry,
    canonical_pilot_entry,
)

offline_bp = Blueprint("offline", __name__)


@offline_bp.errorhandler(CSRFError)
def _csrf_error(e: CSRFError) -> ResponseReturnValue:
    """Same-shape JSON for CSRF failures on any /api/offline/* POST.

    Without this, Flask-WTF's CSRFError (a 400 BadRequest) renders the
    default HTML error page, which breaks every caller here — they all
    parse the response as JSON.
    """
    return jsonify({"status": "invalid", "errors": [str(e.description)]}), 400


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


def _get_flight_or_404(flight_id: int) -> Flight:
    """Mirrors flights._get_flight_or_404 — same tenant/identity guard as
    edit_flight (tenant check for a managed-aircraft row, crew-identity
    check for a standalone one)."""
    fe = db.session.get(Flight, flight_id)
    if not fe:
        abort(404)
    if fe.aircraft_id is not None:
        ac = db.session.get(Aircraft, fe.aircraft_id)
        if not ac or ac.tenant_id != _tenant_id():
            abort(404)
    else:
        uid = session.get("user_id")
        if fe.pic_user_id != uid and fe.second_crew_user_id != uid:
            abort(404)
    return fe


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
                    "fields": canonical_entry(fe),
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


@offline_bp.route("/aircraft/<aircraft_ref:aircraft_id>/logbook/offline")
@login_required
@require_pilot_access
def workbench(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    return render_template("offline/workbench.html", aircraft=ac)


@offline_bp.route("/offline/changes")
@login_required
def changes() -> ResponseReturnValue:
    return render_template("offline/changes.html")


@offline_bp.route("/pilot/logbook/offline")
@login_required
@require_pilot_access
def pilot_workbench() -> ResponseReturnValue:
    return render_template("offline/pilot_workbench.html")


_EDITABLE_FIELD_SET = set(FLIGHT_EDITABLE_FIELDS)

# EASA figures on FLIGHT_EDITABLE_FIELDS that parse_flight_fields (the
# airframe-only parser shared with the online form) doesn't parse — the
# online form parses these itself in _handle_log_flight_post rather than
# through that shared helper, so the offline sync path parses them here too.
_EASA_DECIMAL_FIELDS = (
    "night_time",
    "instrument_time",
    "single_pilot_se",
    "single_pilot_me",
    "multi_pilot",
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
)
_EASA_INT_FIELDS = ("landings_day", "landings_night")


def _parse_easa_decimal(raw: str) -> decimal.Decimal | None:
    if not raw:
        return None
    try:
        v = decimal.Decimal(raw)
        return v if v >= 0 else None
    except Exception:
        return None


def _parse_easa_int(raw: str) -> int | None:
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v >= 0 else None
    except Exception:
        return None


def _apply_easa_fields(fe: Flight, effective: dict[str, str]) -> None:
    for key in _EASA_DECIMAL_FIELDS:
        setattr(fe, key, _parse_easa_decimal(effective[key]))
    for key in _EASA_INT_FIELDS:
        setattr(fe, key, _parse_easa_int(effective[key]))


def _malformed_sync_body(fields: Any, base: Any) -> bool:
    return (
        not isinstance(fields, dict)
        or not isinstance(base, dict)
        or set(fields.keys()) != _EDITABLE_FIELD_SET
        or set(base.keys()) != _EDITABLE_FIELD_SET
        or not all(isinstance(v, str) for v in fields.values())
        or not all(isinstance(v, str) for v in base.values())
    )


@offline_bp.route("/api/offline/flights/<int:flight_id>/sync", methods=["POST"])
@api_login_required
@require_pilot_access
def sync_flight(flight_id: int) -> ResponseReturnValue:
    fe = _get_flight_or_404(flight_id)
    uid = int(session["user_id"])

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"status": "invalid", "errors": [_("Malformed request.")]}), 400

    fields_raw = body.get("fields")
    base_raw = body.get("base")
    force_duplicate = bool(body.get("force_duplicate", False))

    if _malformed_sync_body(fields_raw, base_raw):
        return jsonify({"status": "invalid", "errors": [_("Malformed request.")]}), 400
    fields = cast("dict[str, str]", fields_raw)
    base = cast("dict[str, str]", base_raw)

    ac = db.session.get(Aircraft, fe.aircraft_id) if fe.aircraft_id else None
    current = canonical_entry(fe)

    # Per-field conflict scan: a field is only in conflict when the user
    # changed it (fields != base) AND the server also moved since the
    # snapshot AND the server didn't happen to land on the same value.
    conflicts: list[dict[str, str]] = []
    effective = dict(current)
    for key in FLIGHT_EDITABLE_FIELDS:
        if fields[key] != base[key]:
            if current[key] != base[key] and current[key] != fields[key]:
                conflicts.append(
                    {
                        "field": key,
                        "base": base[key],
                        "local": fields[key],
                        "server": current[key],
                    }
                )
            else:
                effective[key] = fields[key]

    if conflicts:
        return (
            jsonify({"status": "conflict", "conflicts": conflicts, "entry": current}),
            409,
        )

    values, errors = parse_flight_fields(effective, ac)
    if errors:
        return jsonify({"status": "invalid", "errors": errors}), 400

    if (
        effective["date"] != current["date"]
        or effective["departure_icao"] != current["departure_icao"]
        or effective["arrival_icao"] != current["arrival_icao"]
    ):
        dup = _find_duplicate_flight(
            aircraft_id=fe.aircraft_id,
            pilot_user_id=uid,
            date=values["date"],
            dep_icao=values["departure_icao"],
            arr_icao=values["arrival_icao"],
            block_off=fe.block_off_utc,
            block_on=fe.block_on_utc,
            exclude_flight_id=fe.id,
        )
        if dup and not force_duplicate:
            return jsonify({"status": "duplicate"}), 409

    apply_flight_fields(fe, values)
    _apply_easa_fields(fe, effective)

    db.session.commit()
    if fe.aircraft_id:
        _check_flight_hour_milestone(fe)

    return jsonify({"status": "ok", "entry": canonical_entry(fe)})


@offline_bp.route("/api/offline/pilot/logbook")
@api_login_required
@require_pilot_access
def pilot_logbook_snapshot() -> ResponseReturnValue:
    from sqlalchemy import or_  # pyright: ignore[reportMissingImports]

    uid = int(session["user_id"])
    entries = (
        Flight.query.filter(
            or_(Flight.pic_user_id == uid, Flight.second_crew_user_id == uid),
            Flight.aircraft_id.is_(None),
        )
        .order_by(Flight.date.asc(), Flight.id.asc())
        .all()
    )
    return jsonify(
        {
            "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
            "entries": [
                {"id": pe.id, "fields": canonical_pilot_entry(pe)} for pe in entries
            ],
        }
    )


_PILOT_EDITABLE_FIELD_SET = set(PILOT_EDITABLE_FIELDS)


def _malformed_pilot_sync_body(fields: Any, base: Any) -> bool:
    return (
        not isinstance(fields, dict)
        or not isinstance(base, dict)
        or set(fields.keys()) != _PILOT_EDITABLE_FIELD_SET
        or set(base.keys()) != _PILOT_EDITABLE_FIELD_SET
        or not all(isinstance(v, str) for v in fields.values())
        or not all(isinstance(v, str) for v in base.values())
    )


@offline_bp.route("/api/offline/pilot/logbook/<int:entry_id>/sync", methods=["POST"])
@api_login_required
@require_pilot_access
def sync_pilot_entry(entry_id: int) -> ResponseReturnValue:
    uid = int(session["user_id"])
    pe = db.session.get(Flight, entry_id)
    if (
        not pe
        or (pe.pic_user_id != uid and pe.second_crew_user_id != uid)
        or pe.aircraft_id is not None
    ):
        abort(404)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"status": "invalid", "errors": [_("Malformed request.")]}), 400

    fields_raw = body.get("fields")
    base_raw = body.get("base")

    if _malformed_pilot_sync_body(fields_raw, base_raw):
        return jsonify({"status": "invalid", "errors": [_("Malformed request.")]}), 400
    fields = cast("dict[str, str]", fields_raw)
    base = cast("dict[str, str]", base_raw)

    current = canonical_pilot_entry(pe)

    conflicts: list[dict[str, str]] = []
    effective = dict(current)
    for key in PILOT_EDITABLE_FIELDS:
        if fields[key] != base[key]:
            if current[key] != base[key] and current[key] != fields[key]:
                conflicts.append(
                    {
                        "field": key,
                        "base": base[key],
                        "local": fields[key],
                        "server": current[key],
                    }
                )
            else:
                effective[key] = fields[key]

    if conflicts:
        return (
            jsonify({"status": "conflict", "conflicts": conflicts, "entry": current}),
            409,
        )

    values, errors = parse_pilot_fields(effective)
    if errors:
        return jsonify({"status": "invalid", "errors": errors}), 400

    apply_pilot_fields(pe, values)
    db.session.commit()

    return jsonify({"status": "ok", "entry": canonical_pilot_entry(pe)})
