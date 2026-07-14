import contextlib
import decimal
import json as _json
import os
import uuid
from datetime import (
    date as _date,
    time as _time,
    datetime as _datetime,
    timedelta as _timedelta,
    timezone as _timezone,
)

from typing import Any

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from werkzeug.utils import secure_filename

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from sqlalchemy import func, or_  # pyright: ignore[reportMissingImports]

from extensions import _rate_limiting_disabled, limiter as _limiter  # pyright: ignore[reportMissingImports]

from models import (
    Aircraft,
    AppSetting,
    Component,
    CrewRole,
    Document,
    FlightCrew,
    FlightEntry,
    GpsTrack,
    PilotLogbookEntry,
    Reservation,
    ReservationStatus,
    Role,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]
from utils import (
    accessible_aircraft,
    activity,
    login_required,
    require_pilot_access,
    require_role,
    user_can_access_aircraft,
)  # pyright: ignore[reportMissingImports]
from pilots.personal_minimums import (  # pyright: ignore[reportMissingImports]
    get_active_revision,
    recency_breaches,
)

flights_bp = Blueprint("flights", __name__)

_ALLOWED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
_ALLOWED_GPS_EXTS = {".gpx", ".kml", ".csv"}
_FUEL_UNITS = ["L", "gal"]
_NATURE_SUGGESTIONS = [
    "Local flight",
    "Navigation",
    "Cross-country",
    "Training",
    "IFR practice",
    "Night flight",
    "Touch-and-go",
    "Ferry flight",
    "Air test",
    "Sightseeing",
]

_HOUR_MILESTONES = [100, 500, 1000, 2000, 5000]


def _openaip_key() -> str | None:
    s = db.session.get(AppSetting, "openaip_api_key")
    return s.value if s and s.value else None


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _check_flight_hour_milestone(fe: FlightEntry) -> None:
    """Set a one-shot session flag when total fleet hours cross a milestone."""
    this_flight = float(fe.flight_time or 0)
    if this_flight <= 0:
        return
    tid = _tenant_id()
    aircraft_ids = [a.id for a in accessible_aircraft(tid).all()]
    new_total = float(
        db.session.query(func.sum(FlightEntry.flight_time))
        .filter(FlightEntry.aircraft_id.in_(aircraft_ids))
        .scalar()
        or 0
    )
    old_total = new_total - this_flight
    for milestone in _HOUR_MILESTONES:
        if old_total < milestone <= new_total:
            session["milestone_hours"] = milestone
            flash(
                _(
                    "🎉 You just crossed %(hours)s flight hours!",
                    hours=milestone,
                ),
                "info",
            )
            break


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
        abort(404)
    return ac


def _get_flight_or_404(flight_id: int) -> FlightEntry:
    fe = db.session.get(FlightEntry, flight_id)
    if not fe:
        abort(404)
    ac = db.session.get(Aircraft, fe.aircraft_id)
    if not ac or ac.tenant_id != _tenant_id():
        abort(404)
    return fe


def _save_upload(file: Any, flight_id: int, label: str) -> str | None:
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in _ALLOWED_PHOTO_EXTS:
        return None
    stored = f"flight_{flight_id}_{label}_{uuid.uuid4().hex[:8]}{ext}"
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, stored))
    return stored


def _delete_upload(filename: str | None) -> None:
    if not filename:
        return
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    try:
        os.remove(os.path.join(folder, filename))
    except OSError:
        current_app.logger.debug(
            "Could not delete upload %s (already absent?)", filename
        )


def _nature_suggestions(aircraft_id: int) -> list[str]:
    used = [
        row[0]
        for row in db.session.query(FlightEntry.nature_of_flight)
        .filter_by(aircraft_id=aircraft_id)
        .filter(FlightEntry.nature_of_flight.isnot(None))
        .distinct()
        .all()
    ]
    return _NATURE_SUGGESTIONS + [n for n in used if n not in _NATURE_SUGGESTIONS]


def _parse_gps_upload(file: Any) -> dict[str, Any] | None:
    """Parse a single GPS file. Returns autofill dict or None."""
    try:
        from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
            detect_segments,
            merge_and_sort,
            parse_gps_file,
        )
    except ImportError:
        return None
    filename = secure_filename(file.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_GPS_EXTS:
        return None
    data = file.read()
    try:
        parsed = parse_gps_file(data, filename)
        all_points = merge_and_sort([parsed])
        segments = detect_segments(all_points)
    except Exception:
        return None
    if not segments:
        return None
    seg = segments[0]
    return {
        "filename": filename,
        "device_id": parsed.device_id,
        "block_off_utc": seg.block_off_utc,
        "block_on_utc": seg.block_on_utc,
        "date": seg.block_off_utc.date(),
        "departure_icao": seg.departure_icao or seg.hint_departure_icao or "",
        "arrival_icao": seg.arrival_icao or seg.hint_arrival_icao or "",
        "departure_time": seg.block_off_utc.time(),
        "arrival_time": seg.block_on_utc.time(),
        "flight_time_h": round(seg.flight_time_raw_h, 1),
        "geojson": seg.track_geojson,
        "landing_count": seg.landing_count,
    }


def _find_duplicate_flight(
    aircraft_id: int | None,
    pilot_user_id: int,
    date: _date,
    dep_icao: str,
    arr_icao: str,
    block_off: _datetime | None,
    block_on: _datetime | None,
    exclude_flight_id: int | None = None,
    exclude_pilot_entry_id: int | None = None,
) -> dict[str, Any] | None:
    """Return info about a matching FlightEntry or PilotLogbookEntry, or None."""
    if aircraft_id and block_off and block_on:
        q = FlightEntry.query.filter(
            FlightEntry.aircraft_id == aircraft_id,
            FlightEntry.block_off_utc.isnot(None),
            FlightEntry.block_on_utc.isnot(None),
            FlightEntry.block_off_utc < block_on,
            FlightEntry.block_on_utc > block_off,
        )
        if exclude_flight_id:
            q = q.filter(FlightEntry.id != exclude_flight_id)
        existing = q.first()
        if existing:
            return {"type": "flight", "entry": existing}

    if aircraft_id and not block_off:
        q2 = FlightEntry.query.filter_by(
            aircraft_id=aircraft_id,
            date=date,
            departure_icao=dep_icao,
            arrival_icao=arr_icao,
        )
        if exclude_flight_id:
            q2 = q2.filter(FlightEntry.id != exclude_flight_id)
        existing2 = q2.first()
        if existing2:
            return {"type": "flight", "entry": existing2}

    q3 = PilotLogbookEntry.query.filter_by(
        pilot_user_id=pilot_user_id,
        date=date,
        departure_place=dep_icao,
        arrival_place=arr_icao,
    )
    if exclude_pilot_entry_id:
        q3 = q3.filter(PilotLogbookEntry.id != exclude_pilot_entry_id)
    existing3 = q3.first()
    if existing3:
        return {"type": "pilot", "entry": existing3}

    return None


def _get_counter_hint(aircraft_id: int) -> dict[str, float | None]:
    last = (
        FlightEntry.query.filter_by(aircraft_id=aircraft_id)
        .filter(
            db.or_(
                FlightEntry.flight_time_counter_end.isnot(None),
                FlightEntry.engine_time_counter_end.isnot(None),
            )
        )
        .order_by(
            FlightEntry.date.desc(),
            FlightEntry.departure_time.desc().nullslast(),
            FlightEntry.id.desc(),
        )
        .first()
    )
    if not last:
        return {"flight": None, "engine": None}
    return {
        "flight": float(last.flight_time_counter_end)
        if last.flight_time_counter_end is not None
        else None,
        "engine": float(last.engine_time_counter_end)
        if last.engine_time_counter_end is not None
        else None,
    }


# Phase 37d: how far outside a reservation's booked window a flight may
# still fall and be auto-linked to it — absorbs early departures / late
# returns. A constant, not a per-tenant setting, per the spec.
_RESERVATION_LINK_BEFORE = _timedelta(hours=2)
_RESERVATION_LINK_AFTER = _timedelta(hours=6)


def _find_covering_reservation(
    aircraft_id: int, pilot_user_id: int, anchor: _datetime
) -> Reservation | None:
    """A CONFIRMED reservation for this pilot on this aircraft whose booked
    window (± tolerance) contains *anchor* — never linked across pilots."""
    candidates: list[Reservation] = Reservation.query.filter_by(
        aircraft_id=aircraft_id,
        pilot_user_id=pilot_user_id,
        status=ReservationStatus.CONFIRMED,
    ).all()
    for r in candidates:
        # SQLite returns naive datetimes even for DateTime(timezone=True)
        # columns; PostgreSQL returns timezone-aware. Normalize the compare.
        cmp_anchor = (
            anchor.replace(tzinfo=None) if r.start_dt.tzinfo is None else anchor
        )
        if (
            r.start_dt - _RESERVATION_LINK_BEFORE
            <= cmp_anchor
            <= r.end_dt + _RESERVATION_LINK_AFTER
        ):
            return r
    return None


def _ac_category(ac: Aircraft) -> str:
    return getattr(ac, "category", "SEP") or "SEP"


# ── Serve uploads ─────────────────────────────────────────────────────────────


@flights_bp.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename: str) -> ResponseReturnValue:
    # Verify the requesting user may see this file before serving it.
    doc = Document.query.filter_by(filename=filename).first()
    if doc is not None:
        if doc.aircraft_id is not None:
            # Covers aircraft docs and component docs (which always carry aircraft_id too).
            _get_aircraft_or_404(
                doc.aircraft_id
            )  # aborts 404 if wrong tenant/no access
        elif doc.flight_entry_id is not None:
            _get_flight_or_404(doc.flight_entry_id)
        elif doc.pilot_user_id is not None:
            if doc.pilot_user_id != session["user_id"]:
                abort(404)
        else:
            abort(404)
    else:
        # Counter and fuel photos are stored directly on FlightEntry (not via Document).
        fe = FlightEntry.query.filter(
            or_(
                FlightEntry.flight_counter_photo == filename,
                FlightEntry.engine_counter_photo == filename,
                FlightEntry.fuel_photo == filename,
            )
        ).first()
        if fe is None:
            abort(404)
        _get_flight_or_404(fe.id)
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    return send_from_directory(folder, filename)


# ── Fleet logbook ─────────────────────────────────────────────────────────────


@flights_bp.route("/flights")
@login_required
def fleet_flights() -> ResponseReturnValue:
    tid = _tenant_id()
    aircraft_list = accessible_aircraft(tid, include_archived=True).all()
    aircraft_map = {ac.id: ac for ac in aircraft_list}
    flights = (
        FlightEntry.query.filter(
            FlightEntry.aircraft_id.in_([ac.id for ac in aircraft_list])
        )
        .order_by(
            FlightEntry.date.desc(),
            FlightEntry.departure_time.desc().nullslast(),
            FlightEntry.id.desc(),
        )
        .all()
    )
    return render_template(
        "flights/fleet.html", flights=flights, aircraft_map=aircraft_map
    )


# ── Airframe logbook ──────────────────────────────────────────────────────────


@flights_bp.route("/aircraft/<int:aircraft_id>/flights")
@login_required
def list_flights(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    flights = (
        FlightEntry.query.filter_by(aircraft_id=ac.id)
        .order_by(
            FlightEntry.date.desc(),
            FlightEntry.departure_time.desc().nullslast(),
            FlightEntry.id.desc(),
        )
        .all()
    )
    milestone_hours = session.pop("milestone_hours", None)
    return render_template(
        "flights/list.html",
        aircraft=ac,
        flights=flights,
        milestone_hours=milestone_hours,
    )


# ── Component logbook ─────────────────────────────────────────────────────────


@flights_bp.route("/aircraft/<int:aircraft_id>/components/<int:component_id>/logbook")
@login_required
def component_logbook(aircraft_id: int, component_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    comp = db.session.get(Component, component_id)
    if not comp or comp.aircraft_id != ac.id:
        abort(404)

    query = FlightEntry.query.filter_by(aircraft_id=ac.id)
    if comp.installed_at:
        query = query.filter(FlightEntry.date >= comp.installed_at)
    if comp.removed_at:
        query = query.filter(FlightEntry.date <= comp.removed_at)

    flights_asc = query.order_by(
        FlightEntry.date.asc(),
        FlightEntry.departure_time.asc().nullslast(),
        FlightEntry.id.asc(),
    ).all()

    base = float(comp.time_at_install or 0)
    cumulative = base
    flights_with_hours = []
    for f in flights_asc:
        if (
            f.flight_time_counter_end is not None
            and f.flight_time_counter_start is not None
        ):
            cumulative += float(f.flight_time_counter_end) - float(
                f.flight_time_counter_start
            )
        flights_with_hours.append((f, cumulative))

    flights_with_hours.reverse()

    # TBO from the dedicated column (legacy data may still carry it in extras);
    # a recorded overhaul resets the reference point.
    tbo_hours = (
        float(comp.tbo_hours)
        if comp.tbo_hours is not None
        else (comp.extras or {}).get("tbo_hours")
    )
    since_overhaul = cumulative - float(comp.overhauled_at_hours or 0)
    tbo_remaining = (tbo_hours - since_overhaul) if tbo_hours else None

    return render_template(
        "flights/logbook_component.html",
        aircraft=ac,
        component=comp,
        flights_with_hours=flights_with_hours,
        total_component_hours=cumulative,
        since_overhaul=since_overhaul,
        tbo_hours=tbo_hours,
        tbo_remaining=tbo_remaining,
    )


# ── Unified log / edit flight ─────────────────────────────────────────────────


@flights_bp.route("/flights/new", methods=["GET", "POST"])
@login_required
@require_pilot_access
def log_flight() -> ResponseReturnValue:
    tid = _tenant_id()
    managed_aircraft = accessible_aircraft(tid).all()
    uid = int(session["user_id"])
    preselect_id = request.args.get("aircraft_id", type=int)

    if request.method == "POST":
        return _handle_log_flight_post(managed_aircraft, uid, fe=None)

    gps_prefill = session.pop("gps_prefill", None)
    gps_review_return_aircraft_id = request.args.get("gps_review_return", type=int)
    gps_review_return_seg_idx = request.args.get("gps_seg", type=int)
    _u = db.session.get(User, uid)
    pilot_name_hint = _u.display_name if _u else ""
    nature_suggestions = _NATURE_SUGGESTIONS
    aircraft: Aircraft | None = None
    if preselect_id:
        aircraft = next((a for a in managed_aircraft if a.id == preselect_id), None)
        if aircraft:
            nature_suggestions = _nature_suggestions(aircraft.id)
    counter_hint = _get_counter_hint(aircraft.id) if aircraft else None
    covering_reservation = (
        _find_covering_reservation(aircraft.id, uid, _datetime.now(_timezone.utc))
        if aircraft
        else None
    )

    active_minimums = get_active_revision(uid)
    minimums_breaches = (
        recency_breaches(active_minimums, uid) if active_minimums else []
    )

    return render_template(
        "flights/flight_form.html",
        flight=None,
        pilot_entry=None,
        aircraft=aircraft,
        managed_aircraft=managed_aircraft,
        preselect_aircraft_id=preselect_id,
        gps_prefill=gps_prefill,
        nature_suggestions=nature_suggestions,
        pilot_name_hint=pilot_name_hint,
        crew_roles=CrewRole,
        fuel_units=_FUEL_UNITS,
        duplicate=None,
        counter_hint=counter_hint,
        openaip_key=_openaip_key(),
        today_date=_date.today().isoformat(),
        gps_review_return_aircraft_id=gps_review_return_aircraft_id,
        gps_review_return_seg_idx=gps_review_return_seg_idx,
        covering_reservation=covering_reservation,
        active_minimums=active_minimums,
        minimums_breaches=minimums_breaches,
    )


@flights_bp.route("/flights/<int:flight_id>/edit", methods=["GET", "POST"])
@login_required
@require_pilot_access
def edit_flight(flight_id: int) -> ResponseReturnValue:
    tid = _tenant_id()
    managed_aircraft = accessible_aircraft(tid, include_archived=True).all()
    uid = int(session["user_id"])
    fe = _get_flight_or_404(flight_id)

    if request.method == "POST":
        return _handle_log_flight_post(managed_aircraft, uid, fe=fe)

    gps_prefill = session.pop("gps_prefill", None)
    pilot_entry = PilotLogbookEntry.query.filter_by(
        flight_id=fe.id, pilot_user_id=uid
    ).first()
    aircraft = db.session.get(Aircraft, fe.aircraft_id)
    counter_hint = _get_counter_hint(fe.aircraft_id)

    return render_template(
        "flights/flight_form.html",
        flight=fe,
        pilot_entry=pilot_entry,
        aircraft=aircraft,
        managed_aircraft=managed_aircraft,
        preselect_aircraft_id=fe.aircraft_id,
        gps_prefill=gps_prefill,
        nature_suggestions=_nature_suggestions(fe.aircraft_id),
        pilot_name_hint=None,
        crew_roles=CrewRole,
        fuel_units=_FUEL_UNITS,
        duplicate=None,
        counter_hint=counter_hint,
        openaip_key=_openaip_key(),
        gps_review_return_aircraft_id=None,
        gps_review_return_seg_idx=None,
        covering_reservation=None,
        active_minimums=None,
        minimums_breaches=[],
    )


@flights_bp.route("/flights/<int:flight_id>/track/image.png")
@login_required
@require_pilot_access
def flight_track_image(flight_id: int) -> ResponseReturnValue:
    """Return a static PNG of the flight's GPS track."""
    from flask import Response  # pyright: ignore[reportMissingImports]
    from utils import generate_single_track_image  # pyright: ignore[reportMissingImports]

    fe = _get_flight_or_404(flight_id)
    track = fe.gps_track
    if not track or not track.geojson:
        abort(404)

    hires = request.args.get("quality") == "hires"
    portrait = request.args.get("orientation") == "portrait"
    is_default = not hires and not portrait

    if is_default and track.cached_png:
        png_bytes = bytes(track.cached_png)
    else:
        tile_s = db.session.get(AppSetting, "openaip_api_key")
        base_w, base_h = (480, 800) if portrait else (800, 480)
        mul = 2 if hires else 1
        canvas_w, canvas_h = base_w * mul, base_h * mul

        png_bytes = generate_single_track_image(
            track.geojson,
            date=str(fe.date),
            dep=fe.departure_icao or "",
            arr=fe.arrival_icao or "",
            _openaip_key=tile_s.value if tile_s and tile_s.value else None,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            high_res=hires,
        )
        if is_default:
            track.cached_png = png_bytes  # type: ignore[attr-defined]
            db.session.commit()
    orient_sfx = "-portrait" if portrait else ""
    qual_sfx = "-hires" if hires else ""
    suffix = orient_sfx + qual_sfx
    filename = f"flight_{flight_id}_track{suffix}.png"
    return Response(
        png_bytes,
        mimetype="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{track.id}"',
        },
    )


@flights_bp.route("/flights/<int:flight_id>/track/animation.gif")
@login_required
@require_pilot_access
def flight_track_gif(flight_id: int) -> ResponseReturnValue:
    """Return an animated GIF of the flight's GPS track drawn progressively."""
    from flask import Response  # pyright: ignore[reportMissingImports]
    from utils import generate_single_track_gif  # pyright: ignore[reportMissingImports]

    fe = _get_flight_or_404(flight_id)
    track = fe.gps_track
    if not track or not track.geojson:
        abort(404)

    hires = request.args.get("quality") == "hires"
    portrait = request.args.get("orientation") == "portrait"
    is_default = not hires and not portrait

    if is_default and track.cached_gif:
        gif_bytes = bytes(track.cached_gif)
    else:
        tile_s = db.session.get(AppSetting, "openaip_api_key")
        base_w, base_h = (480, 800) if portrait else (800, 480)
        mul = 2 if hires else 1
        canvas_w, canvas_h = base_w * mul, base_h * mul

        gif_bytes = generate_single_track_gif(
            track.geojson,
            date=str(fe.date),
            dep=fe.departure_icao or "",
            arr=fe.arrival_icao or "",
            _openaip_key=tile_s.value if tile_s and tile_s.value else None,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            high_res=hires,
        )
        if is_default:
            track.cached_gif = gif_bytes  # type: ignore[attr-defined]
            db.session.commit()
    orient_sfx = "-portrait" if portrait else ""
    qual_sfx = "-hires" if hires else ""
    suffix = orient_sfx + qual_sfx
    filename = f"flight_{flight_id}_track{suffix}.gif"
    return Response(
        gif_bytes,
        mimetype="image/gif",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{track.id}"',
        },
    )


@flights_bp.route("/flights/registration-lookup")
@login_required
@require_pilot_access
def registration_lookup() -> ResponseReturnValue:
    """AJAX endpoint: return aircraft type for a previously logged registration.

    Sources (in priority order):
    1. Current user's own logbook entries (most recent first).
    2. Any user in the same tenant (shared pool within the organisation).
    Sources 3 (cross-tenant) and 4 (external registry) are intentionally omitted.

    Matching is normalised: case-insensitive, ignoring dashes and spaces.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"result": None})

    def _norm(s: str) -> str:
        return s.upper().replace("-", "").replace(" ", "")

    q_norm = _norm(q)
    uid = int(session["user_id"])
    tid = _tenant_id()

    # Source 1: current user's own history
    user_entries = (
        PilotLogbookEntry.query.filter_by(pilot_user_id=uid)
        .filter(PilotLogbookEntry.aircraft_registration.isnot(None))
        .order_by(
            PilotLogbookEntry.date.desc(),
            PilotLogbookEntry.departure_time.desc().nullslast(),
            PilotLogbookEntry.id.desc(),
        )
        .all()
    )
    for e in user_entries:
        if _norm(e.aircraft_registration or "") == q_norm and e.aircraft_type:
            return jsonify(
                {
                    "result": {
                        "aircraft_type": e.aircraft_type,
                        "aircraft_type_icao": e.aircraft_type_icao or "",
                    }
                }
            )

    # Source 2: any user in the same tenant
    from models import TenantUser as _TU  # pyright: ignore[reportMissingImports]

    tenant_entries = (
        PilotLogbookEntry.query.join(
            _TU, _TU.user_id == PilotLogbookEntry.pilot_user_id
        )
        .filter(_TU.tenant_id == tid)
        .filter(PilotLogbookEntry.aircraft_registration.isnot(None))
        .filter(PilotLogbookEntry.aircraft_type.isnot(None))
        .order_by(
            PilotLogbookEntry.date.desc(),
            PilotLogbookEntry.departure_time.desc().nullslast(),
            PilotLogbookEntry.id.desc(),
        )
        .all()
    )
    for e in tenant_entries:
        if _norm(e.aircraft_registration or "") == q_norm:
            return jsonify(
                {
                    "result": {
                        "aircraft_type": e.aircraft_type,
                        "aircraft_type_icao": e.aircraft_type_icao or "",
                    }
                }
            )

    return jsonify({"result": None})


@flights_bp.route("/flights/parse-gps", methods=["POST"])
@_limiter.limit("30 per minute", exempt_when=_rate_limiting_disabled)
@login_required
@require_pilot_access
def parse_gps_api() -> ResponseReturnValue:
    """AJAX endpoint: parse a GPS upload, check for duplicates, return JSON."""
    gps_file = request.files.get("gps_file")
    if not gps_file or not gps_file.filename:
        return jsonify(
            {
                "success": False,
                "error": str(
                    _("Could not parse GPS file. Fill in the fields manually.")
                ),
            }
        )
    gps_data = _parse_gps_upload(gps_file)
    if not gps_data:
        return jsonify(
            {
                "success": False,
                "error": str(
                    _("Could not parse GPS file. Fill in the fields manually.")
                ),
            }
        )
    return jsonify(
        {
            "success": True,
            "message": str(
                _(
                    "GPS file parsed: %(filename)s — fields pre-filled below. Review and save.",
                    filename=gps_data["filename"],
                )
            ),
            "data": {
                "filename": gps_data["filename"],
                "date": gps_data["date"].isoformat(),
                "departure_icao": gps_data["departure_icao"],
                "arrival_icao": gps_data["arrival_icao"],
                "departure_time": gps_data["departure_time"].strftime("%H:%M")
                if gps_data["departure_time"]
                else "",
                "arrival_time": gps_data["arrival_time"].strftime("%H:%M")
                if gps_data["arrival_time"]
                else "",
                "flight_time_h": str(gps_data["flight_time_h"]),
                "block_off_utc": gps_data["block_off_utc"].isoformat()
                if gps_data["block_off_utc"]
                else "",
                "block_on_utc": gps_data["block_on_utc"].isoformat()
                if gps_data["block_on_utc"]
                else "",
                "geojson": _json.dumps(gps_data["geojson"])
                if gps_data["geojson"]
                else "",
                "landing_count": gps_data["landing_count"] or 0,
                "device_id": gps_data["device_id"] or "",
            },
            "duplicate": _check_gps_duplicate(gps_data),
            "suggested_aircraft_id": _suggested_aircraft_for_device(
                gps_data["device_id"]
            ),
        }
    )


def _suggested_aircraft_for_device(device_id: str | None) -> int | None:
    """Return the aircraft_id most recently used with this device_id, or None."""
    if not device_id:
        return None
    row = (
        db.session.query(FlightEntry.aircraft_id)
        .join(GpsTrack, FlightEntry.gps_track_id == GpsTrack.id)
        .filter(GpsTrack.device_id == device_id)
        .order_by(
            FlightEntry.date.desc(),
            FlightEntry.departure_time.desc().nullslast(),
            FlightEntry.id.desc(),
        )
        .first()
    )
    return int(row[0]) if row else None


def _check_gps_duplicate(gps_data: dict[str, Any]) -> dict[str, Any] | None:
    """Return a duplicate summary dict if a matching entry exists, else None."""
    uid = int(session.get("user_id", 0))
    aircraft_id = request.form.get("aircraft_id", type=int)
    if aircraft_id is not None:
        ac = db.session.get(Aircraft, aircraft_id)
        if not ac or ac.tenant_id != _tenant_id():
            aircraft_id = None
    dup = _find_duplicate_flight(
        aircraft_id=aircraft_id,
        pilot_user_id=uid,
        date=gps_data["date"],
        dep_icao=gps_data["departure_icao"],
        arr_icao=gps_data["arrival_icao"],
        block_off=gps_data["block_off_utc"],
        block_on=gps_data["block_on_utc"],
    )
    if not dup:
        return None
    entry = dup["entry"]
    return {
        "type": dup["type"],
        "date": str(gps_data["date"]),
        "dep": gps_data["departure_icao"],
        "arr": gps_data["arrival_icao"],
        "entry_id": entry.id,
    }


def _handle_log_flight_post(
    managed_aircraft: list[Aircraft],
    uid: int,
    fe: FlightEntry | None,
) -> ResponseReturnValue:
    f = request.form
    gps_file = request.files.get("gps_file")

    # ── GPS parse step ─────────────────────────────────────────────────────────
    if request.form.get("action") == "parse_gps" and gps_file and gps_file.filename:
        gps_data = _parse_gps_upload(gps_file)
        if gps_data:
            session["gps_prefill"] = {
                "filename": gps_data["filename"],
                "date": gps_data["date"].isoformat(),
                "departure_icao": gps_data["departure_icao"],
                "arrival_icao": gps_data["arrival_icao"],
                "departure_time": gps_data["departure_time"].strftime("%H:%M")
                if gps_data["departure_time"]
                else "",
                "arrival_time": gps_data["arrival_time"].strftime("%H:%M")
                if gps_data["arrival_time"]
                else "",
                "flight_time_h": str(gps_data["flight_time_h"]),
                "block_off_utc": gps_data["block_off_utc"].isoformat(),
                "block_on_utc": gps_data["block_on_utc"].isoformat(),
                "geojson": _json.dumps(gps_data["geojson"])
                if gps_data["geojson"]
                else "",
                "landing_count": gps_data["landing_count"],
            }
            flash(_("GPS file parsed — fields pre-filled. Review and save."), "info")
        else:
            flash(
                _("Could not parse GPS file. Fill in the fields manually."), "warning"
            )
        if fe:
            return redirect(url_for("flights.edit_flight", flight_id=fe.id))
        aircraft_id = f.get("aircraft_id", type=int)
        qs: dict[str, Any] = {"aircraft_id": aircraft_id} if aircraft_id else {}
        return redirect(url_for("flights.log_flight", **qs))

    # ── Determine aircraft ─────────────────────────────────────────────────────
    other_aircraft = f.get("other_aircraft") == "1"
    aircraft_id_raw = f.get("aircraft_id", type=int)
    # When editing an existing flight, fall back to the flight's own aircraft_id
    # so the `if ac:` block is entered even when aircraft_id is absent from the form.
    if aircraft_id_raw is None and fe is not None:
        aircraft_id_raw = fe.aircraft_id
    ac: Aircraft | None = None
    if not other_aircraft and aircraft_id_raw:
        ac = next((a for a in managed_aircraft if a.id == aircraft_id_raw), None)

    other_ac_make_model = f.get("other_ac_make_model", "").strip()
    other_ac_reg = f.get("other_ac_reg", "").strip().upper()

    # ── Parse common fields ────────────────────────────────────────────────────
    date_raw = f.get("date", "").strip()
    dep = (f.get("departure_icao") or "").strip().upper()[:4]
    arr = (f.get("arrival_icao") or "").strip().upper()[:4]
    departure_time_raw = f.get("departure_time", "").strip()
    arrival_time_raw = f.get("arrival_time", "").strip()
    pilot_departure_time_raw = f.get("pilot_departure_time", "").strip()
    pilot_arrival_time_raw = f.get("pilot_arrival_time", "").strip()
    flight_time_raw = f.get("flight_time", "").strip()
    nature_of_flight = f.get("nature_of_flight", "").strip() or None
    notes = f.get("notes", "").strip() or None
    pilot_role = f.get("pilot_role", "none").strip()
    if pilot_role not in ("pic", "dual", "none"):
        pilot_role = "none"

    # Aircraft-log fields
    flight_time_counter_start_raw = f.get("flight_time_counter_start", "").strip()
    flight_time_counter_end_raw = f.get("flight_time_counter_end", "").strip()
    engine_time_counter_start_raw = f.get("engine_time_counter_start", "").strip()
    engine_time_counter_end_raw = f.get("engine_time_counter_end", "").strip()
    passenger_count_raw = f.get("passenger_count", "").strip()
    fuel_event_raw = f.get("fuel_event", "none").strip()
    fuel_added_qty_raw = f.get("fuel_added_qty", "").strip()
    fuel_added_unit = f.get("fuel_added_unit", "L").strip()
    fuel_remaining_qty_raw = f.get("fuel_remaining_qty", "").strip()
    oil_added_l_raw = f.get("oil_added_l", "").strip()
    crew_name_0 = f.get("crew_name_0", "").strip()
    crew_role_0 = f.get("crew_role_0", CrewRole.PIC).strip()
    crew_name_1 = f.get("crew_name_1", "").strip()
    crew_role_1 = f.get("crew_role_1", CrewRole.COPILOT).strip()

    # Pilot-log fields
    night_time_raw = f.get("night_time", "").strip()
    instrument_time_raw = f.get("instrument_time", "").strip()
    landings_day_raw = f.get("landings_day", "").strip()
    landings_night_raw = f.get("landings_night", "").strip()
    multi_pilot_raw = f.get("multi_pilot", "").strip()
    pic_name = f.get("pic_name", "").strip() or None

    # GPS hidden fields (carried from parse step or re-render)
    gps_filename = f.get("gps_filename", "").strip() or None
    gps_device_id = f.get("gps_device_id", "").strip() or None
    gps_block_off_raw = f.get("gps_block_off_utc", "").strip()
    gps_block_on_raw = f.get("gps_block_on_utc", "").strip()
    gps_geojson_raw = f.get("gps_geojson", "").strip()

    duplicate_action = f.get("duplicate_action", "").strip()

    errors = []

    flight_date: _date | None = None
    if not date_raw:
        errors.append(_("Date is required."))
    else:
        try:
            flight_date = _date.fromisoformat(date_raw)
        except ValueError:
            errors.append(_("Date must be a valid date (YYYY-MM-DD)."))

    if not dep:
        errors.append(_("Departure airfield is required."))
    if not arr:
        errors.append(_("Arrival airfield is required."))

    if not fe and not ac and not other_aircraft:
        errors.append(_("Please select an aircraft."))

    if ac and not crew_name_0:
        errors.append(_("Pilot (crew 1) name is required."))

    if other_aircraft and pilot_role not in ("pic", "dual"):
        errors.append(_("Pilot role is required for other aircraft flights."))
    if other_aircraft and pilot_role in ("pic", "dual") and not crew_name_0:
        errors.append(_("Pilot name is required."))
    if other_aircraft and not other_ac_make_model:
        errors.append(
            _("Aircraft type (make/model) is required for other aircraft flights.")
        )
    if other_aircraft and not other_ac_reg:
        errors.append(
            _("Aircraft registration is required for other aircraft flights.")
        )

    departure_time: _time | None = None
    arrival_time: _time | None = None
    if departure_time_raw:
        try:
            departure_time = _time.fromisoformat(departure_time_raw)
        except ValueError:
            errors.append(_("Departure time must be a valid UTC time (HH:MM)."))
    if arrival_time_raw:
        try:
            arrival_time = _time.fromisoformat(arrival_time_raw)
        except ValueError:
            errors.append(_("Arrival time must be a valid UTC time (HH:MM)."))

    # Pilot-log times default to (mirror) the aircraft-log times above when
    # left blank; only parsed into their own value when explicitly set.
    pilot_departure_time: _time | None = None
    pilot_arrival_time: _time | None = None
    if pilot_departure_time_raw:
        try:
            pilot_departure_time = _time.fromisoformat(pilot_departure_time_raw)
        except ValueError:
            errors.append(
                _("Pilot log departure time must be a valid UTC time (HH:MM).")
            )
    if pilot_arrival_time_raw:
        try:
            pilot_arrival_time = _time.fromisoformat(pilot_arrival_time_raw)
        except ValueError:
            errors.append(_("Pilot log arrival time must be a valid UTC time (HH:MM)."))

    flight_time_counter_start = flight_time_counter_end = None
    engine_time_counter_start = engine_time_counter_end = None
    if ac:
        for raw, dest in [
            (flight_time_counter_start_raw, "fc_start"),
            (flight_time_counter_end_raw, "fc_end"),
            (engine_time_counter_start_raw, "ec_start"),
            (engine_time_counter_end_raw, "ec_end"),
        ]:
            if raw:
                try:
                    val = float(raw)
                    if val < 0:
                        raise ValueError
                    if dest == "fc_start":
                        flight_time_counter_start = val
                    elif dest == "fc_end":
                        flight_time_counter_end = val
                    elif dest == "ec_start":
                        engine_time_counter_start = val
                    else:
                        engine_time_counter_end = val
                except (ValueError, TypeError):
                    errors.append(_("Counter value must be a positive number."))

        if (
            flight_time_counter_start is not None
            and flight_time_counter_end is not None
            and flight_time_counter_end < flight_time_counter_start
        ):
            errors.append(
                _("Flight counter end must not be less than flight counter start.")
            )
        if (
            engine_time_counter_start is not None
            and engine_time_counter_end is not None
            and engine_time_counter_end < engine_time_counter_start
        ):
            errors.append(
                _("Engine counter end must not be less than engine counter start.")
            )

    flight_time: float | None = None
    if flight_time_raw:
        try:
            flight_time = round(float(flight_time_raw), 1)
            if flight_time < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Flight time must be a non-negative number."))
    elif (
        ac
        and flight_time_counter_start is not None
        and flight_time_counter_end is not None
    ):
        flight_time = round(flight_time_counter_end - flight_time_counter_start, 1)
    elif (
        ac
        and not getattr(ac, "has_flight_counter", True)
        and engine_time_counter_start is not None
        and engine_time_counter_end is not None
    ):
        raw_diff = (engine_time_counter_end - engine_time_counter_start) - float(
            getattr(ac, "flight_counter_offset", 0) or 0
        )
        flight_time = round(max(0.0, raw_diff), 1)

    passenger_count: int | None = None
    if passenger_count_raw:
        try:
            passenger_count = int(passenger_count_raw)
            if passenger_count < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Passenger count must be a non-negative integer."))

    fuel_event = fuel_event_raw if fuel_event_raw in ("before", "after") else None
    fuel_added_qty: float | None = None
    if fuel_event and fuel_added_qty_raw:
        try:
            fuel_added_qty = float(fuel_added_qty_raw)
            if fuel_added_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Fuel quantity added must be a non-negative number."))

    fuel_remaining_qty: float | None = None
    if fuel_remaining_qty_raw:
        try:
            fuel_remaining_qty = float(fuel_remaining_qty_raw)
            if fuel_remaining_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Fuel remaining must be a non-negative number."))

    oil_added_l: float | None = None
    if oil_added_l_raw:
        try:
            oil_added_l = float(oil_added_l_raw)
            if oil_added_l < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Oil added must be a non-negative number."))

    def _parse_dec(raw: str) -> decimal.Decimal | None:
        if not raw:
            return None
        try:
            v = decimal.Decimal(raw)
            return v if v >= 0 else None
        except Exception:
            return None

    night_time = _parse_dec(night_time_raw)
    instrument_time = _parse_dec(instrument_time_raw)
    multi_pilot = _parse_dec(multi_pilot_raw)
    landings_day: int | None = (
        int(landings_day_raw) if landings_day_raw.isdigit() else None
    )
    landings_night: int | None = (
        int(landings_night_raw) if landings_night_raw.isdigit() else None
    )

    gps_block_off: _datetime | None = None
    gps_block_on: _datetime | None = None
    if gps_block_off_raw:
        with contextlib.suppress(
            ValueError
        ):  # malformed hidden field — treat as absent
            gps_block_off = _datetime.fromisoformat(gps_block_off_raw)
    if gps_block_on_raw:
        with contextlib.suppress(
            ValueError
        ):  # malformed hidden field — treat as absent
            gps_block_on = _datetime.fromisoformat(gps_block_on_raw)

    gps_geojson: Any = None
    if gps_geojson_raw:
        with contextlib.suppress(
            Exception
        ):  # malformed hidden field — GPS track simply not applied
            gps_geojson = _json.loads(gps_geojson_raw)

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return _render_form(managed_aircraft, fe, None, aircraft_id_raw, None)

    # An edit of an existing FlightEntry must not flag its own already-linked
    # PilotLogbookEntry as a "duplicate" of itself.
    existing_pilot_entry_id = None
    if fe:
        existing_pilot_entry = PilotLogbookEntry.query.filter_by(
            flight_id=fe.id, pilot_user_id=uid
        ).first()
        existing_pilot_entry_id = (
            existing_pilot_entry.id if existing_pilot_entry else None
        )

    # ── Duplicate detection (first pass) ──────────────────────────────────────
    if not duplicate_action and flight_date and dep and arr:
        dup = _find_duplicate_flight(
            aircraft_id=ac.id if ac else None,
            pilot_user_id=uid,
            date=flight_date,
            dep_icao=dep,
            arr_icao=arr,
            block_off=gps_block_off,
            block_on=gps_block_on,
            exclude_flight_id=fe.id if fe else None,
            exclude_pilot_entry_id=existing_pilot_entry_id,
        )
        if dup:
            return _render_form(managed_aircraft, fe, None, aircraft_id_raw, dup)

    # ── GPS-attach-only path ───────────────────────────────────────────────────
    if duplicate_action == "link_gps" and flight_date:
        dup = _find_duplicate_flight(
            aircraft_id=ac.id if ac else None,
            pilot_user_id=uid,
            date=flight_date,
            dep_icao=dep,
            arr_icao=arr,
            block_off=gps_block_off,
            block_on=gps_block_on,
            exclude_flight_id=fe.id if fe else None,
            exclude_pilot_entry_id=existing_pilot_entry_id,
        )
        if dup and (gps_geojson or gps_filename):
            link_track = GpsTrack(
                source_filename=gps_filename,
                device_id=gps_device_id,
                block_off_utc=gps_block_off,
                block_on_utc=gps_block_on,
                departure_icao=dep,
                arrival_icao=arr,
                geojson=gps_geojson,
            )
            db.session.add(link_track)
            db.session.flush()
            entry = dup["entry"]
            if isinstance(entry, FlightEntry):
                entry.gps_track_id = link_track.id
                plink = PilotLogbookEntry.query.filter_by(
                    flight_id=entry.id, pilot_user_id=uid
                ).first()
                if plink:
                    plink.gps_track_id = link_track.id
            else:
                entry.gps_track_id = link_track.id
            db.session.commit()
            flash(_("GPS track linked to the existing flight entry."), "success")
        else:
            flash(_("Could not link GPS track — no matching entry found."), "warning")
        return redirect(url_for("pilots.logbook"))

    # ── Build GpsTrack if GPS data is present ─────────────────────────────────
    ft_decimal = decimal.Decimal(str(flight_time)) if flight_time is not None else None
    create_pilot = pilot_role in ("pic", "dual")

    gps_track: GpsTrack | None = None
    if gps_geojson or gps_filename:
        existing_track_id: int | None = fe.gps_track_id if fe else None
        if existing_track_id:
            gps_track = db.session.get(GpsTrack, existing_track_id)
            if gps_track:
                if gps_geojson:
                    gps_track.geojson = gps_geojson
                if gps_filename:
                    gps_track.source_filename = gps_filename
                if gps_block_off:
                    gps_track.block_off_utc = gps_block_off
                if gps_block_on:
                    gps_track.block_on_utc = gps_block_on
        if gps_track and gps_device_id:
            gps_track.device_id = gps_device_id
        if not gps_track:
            gps_track = GpsTrack(
                source_filename=gps_filename,
                device_id=gps_device_id,
                block_off_utc=gps_block_off,
                block_on_utc=gps_block_on,
                departure_icao=dep,
                arrival_icao=arr,
                geojson=gps_geojson,
            )
            db.session.add(gps_track)
            db.session.flush()

    # ── Pilot log aircraft fields ──────────────────────────────────────────────
    plog_ac_type: str | None
    if ac:
        plog_ac_type = f"{ac.make} {ac.model}".strip()
        plog_ac_type_icao = getattr(ac, "aircraft_type_icao", None)
        plog_ac_reg = ac.registration
        cat = _ac_category(ac)
        plog_sp_se = ft_decimal if cat in ("SEP", "SET", "") else None
        plog_sp_me = ft_decimal if cat in ("MEP", "MET") else None
    else:
        plog_ac_type = other_ac_make_model or None
        plog_ac_type_icao = f.get("aircraft_type_icao", "").strip() or None
        plog_ac_reg = other_ac_reg or None
        plog_sp_se = ft_decimal
        plog_sp_me = None

    # ── Aircraft log entry ─────────────────────────────────────────────────────
    _fe_is_new = fe is None
    if ac:
        if fe is None:
            fe = FlightEntry(aircraft_id=ac.id)
            db.session.add(fe)

        fe.date = flight_date
        fe.departure_icao = dep
        fe.arrival_icao = arr
        fe.departure_time = departure_time
        fe.arrival_time = arrival_time
        fe.flight_time = ft_decimal
        fe.nature_of_flight = nature_of_flight
        fe.passenger_count = passenger_count
        if landings_day is not None or landings_night is not None:
            fe.landing_count = (landings_day or 0) + (landings_night or 0)
        fe.flight_time_counter_start = flight_time_counter_start
        fe.flight_time_counter_end = flight_time_counter_end
        fe.notes = notes
        fe.engine_time_counter_start = engine_time_counter_start
        fe.engine_time_counter_end = engine_time_counter_end
        fe.fuel_event = fuel_event
        fe.fuel_added_qty = fuel_added_qty
        fe.fuel_added_unit = fuel_added_unit if fuel_added_qty is not None else None
        fe.fuel_remaining_qty = fuel_remaining_qty
        fe.oil_added_l = oil_added_l
        if gps_track:
            fe.gps_track_id = gps_track.id
        if gps_block_off:
            fe.block_off_utc = gps_block_off
        if gps_block_on:
            fe.block_on_utc = gps_block_on

        if _fe_is_new and flight_date is not None:
            anchor = _datetime.combine(
                flight_date, departure_time or _time(12, 0), tzinfo=_timezone.utc
            )
            covering = _find_covering_reservation(ac.id, uid, anchor)
            fe.reservation_id = covering.id if covering else None

        db.session.flush()

        FlightCrew.query.filter_by(flight_id=fe.id).delete()
        if crew_name_0:
            db.session.add(
                FlightCrew(
                    flight_id=fe.id,
                    name=crew_name_0,
                    role=crew_role_0 if crew_role_0 in CrewRole.ALL else CrewRole.PIC,
                    sort_order=0,
                )
            )
        if crew_name_1:
            db.session.add(
                FlightCrew(
                    flight_id=fe.id,
                    name=crew_name_1,
                    role=crew_role_1
                    if crew_role_1 in CrewRole.ALL
                    else CrewRole.COPILOT,
                    sort_order=1,
                )
            )

        for photo_field, label, attr in [
            ("flight_counter_photo", "flight", "flight_counter_photo"),
            ("engine_counter_photo", "engine", "engine_counter_photo"),
            ("fuel_photo", "fuel", "fuel_photo"),
        ]:
            photo_file = request.files.get(photo_field)
            if photo_file and photo_file.filename:
                stored = _save_upload(photo_file, fe.id, label)
                if stored:
                    _delete_upload(getattr(fe, attr))
                    setattr(fe, attr, stored)

    # ── Pilot log entry ────────────────────────────────────────────────────────
    if create_pilot:
        _u = db.session.get(User, uid)
        effective_pic_name = (
            pic_name
            or (crew_name_0 if pilot_role == "pic" else None)
            or (_u.display_name if _u else "")
        )
        existing_pe: PilotLogbookEntry | None = None
        if fe and fe.id:
            existing_pe = PilotLogbookEntry.query.filter_by(
                flight_id=fe.id, pilot_user_id=uid
            ).first()

        pe = existing_pe or PilotLogbookEntry(pilot_user_id=uid)
        if not existing_pe:
            db.session.add(pe)

        pe.flight_id = fe.id if fe else None
        pe.date = flight_date
        pe.aircraft_type = plog_ac_type
        pe.aircraft_type_icao = plog_ac_type_icao
        pe.aircraft_registration = plog_ac_reg
        pe.departure_place = dep
        pe.departure_time = (
            pilot_departure_time if pilot_departure_time is not None else departure_time
        )
        pe.arrival_place = arr
        pe.arrival_time = (
            pilot_arrival_time if pilot_arrival_time is not None else arrival_time
        )
        pe.pic_name = effective_pic_name
        pe.night_time = night_time
        pe.instrument_time = instrument_time
        pe.landings_day = landings_day if landings_day is not None else 0
        pe.landings_night = landings_night
        pe.single_pilot_se = plog_sp_se
        pe.single_pilot_me = plog_sp_me
        pe.multi_pilot = multi_pilot
        pe.function_pic = ft_decimal if pilot_role == "pic" else None
        pe.function_dual = ft_decimal if pilot_role == "dual" else None
        pe.remarks = notes
        if gps_track:
            pe.gps_track_id = gps_track.id

    elif fe and fe.id:
        detach_action = f.get("detach_pilot_log", "").strip()
        if detach_action in ("detach", "delete"):
            existing_pe2 = PilotLogbookEntry.query.filter_by(
                flight_id=fe.id, pilot_user_id=uid
            ).first()
            if existing_pe2:
                if detach_action == "delete":
                    db.session.delete(existing_pe2)
                else:
                    existing_pe2.flight_id = None

    db.session.commit()

    if fe and ac:
        event_name = "flight.logged" if _fe_is_new else "flight.updated"
        activity(
            event_name,
            flight_id=fe.id,
            aircraft_id=ac.id,
            dep=dep,
            arr=arr,
            date=str(flight_date),
        )
        _check_flight_hour_milestone(fe)

    if ac and fe:
        flash(
            _(
                "Flight %(dep)s→%(arr)s on %(date)s saved.",
                dep=dep,
                arr=arr,
                date=flight_date,
            ),
            "success",
        )
        return_ac_id = f.get("gps_review_return_aircraft_id", type=int)
        return_seg_idx = f.get("gps_review_return_seg_idx", type=int)
        if return_ac_id is not None:
            gps_state = session.get("gps_import", {})
            if (
                gps_state.get("aircraft_id") == return_ac_id
                and return_seg_idx is not None
            ):
                confirmed = gps_state.get("confirmed_segments", {})
                confirmed[str(return_seg_idx)] = fe.id
                gps_state["confirmed_segments"] = confirmed
                session["gps_import"] = gps_state
                session.modified = True
            return redirect(
                url_for("aircraft.gps_import_review", aircraft_id=return_ac_id)
            )
        return redirect(url_for("flights.list_flights", aircraft_id=ac.id))

    flash(
        _(
            "Flight %(dep)s→%(arr)s on %(date)s saved to your pilot logbook.",
            dep=dep,
            arr=arr,
            date=flight_date,
        ),
        "success",
    )
    return redirect(url_for("pilots.logbook"))


def _render_form(
    managed_aircraft: list[Aircraft],
    flight: FlightEntry | None,
    pilot_entry: PilotLogbookEntry | None,
    preselect_id: int | None,
    duplicate: dict[str, Any] | None,
) -> ResponseReturnValue:
    nature_suggestions = _NATURE_SUGGESTIONS
    aircraft: Aircraft | None = None
    if preselect_id:
        aircraft = next((a for a in managed_aircraft if a.id == preselect_id), None)
        if aircraft:
            nature_suggestions = _nature_suggestions(aircraft.id)
    counter_hint = _get_counter_hint(aircraft.id) if aircraft else None
    return render_template(
        "flights/flight_form.html",
        flight=flight,
        pilot_entry=pilot_entry,
        aircraft=aircraft,
        managed_aircraft=managed_aircraft,
        preselect_aircraft_id=preselect_id,
        gps_prefill=None,
        nature_suggestions=nature_suggestions,
        pilot_name_hint=None,
        crew_roles=CrewRole,
        fuel_units=_FUEL_UNITS,
        duplicate=duplicate,
        counter_hint=counter_hint,
        openaip_key=_openaip_key(),
        gps_review_return_aircraft_id=None,
        gps_review_return_seg_idx=None,
        covering_reservation=None,
        active_minimums=None,
        minimums_breaches=[],
    )


# ── Delete flight ─────────────────────────────────────────────────────────────


@flights_bp.route(
    "/aircraft/<int:aircraft_id>/flights/<int:flight_id>/delete", methods=["POST"]
)
@login_required
@require_pilot_access
def delete_flight(aircraft_id: int, flight_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    fe = db.session.get(FlightEntry, flight_id)
    if not fe or fe.aircraft_id != ac.id:
        abort(404)
    label = f"{fe.departure_icao}→{fe.arrival_icao} on {fe.date}"
    activity(
        "flight.deleted", flight_id=flight_id, aircraft_id=aircraft_id, label=label
    )
    _delete_upload(fe.flight_counter_photo)
    _delete_upload(fe.engine_counter_photo)
    db.session.delete(fe)
    db.session.commit()
    flash(_("Flight %(label)s deleted.", label=label), "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))


# ── Bulk airframe logbook import (CSV / Excel) ────────────────────────────────

_AIRFRAME_IMPORT_SESSION_KEY = "airframe_import"
_AIRFRAME_IMPORT_EXTS = {".csv", ".xlsx", ".xls"}
_AIRFRAME_IMPORT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _airframe_tmp_dir() -> str:
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    d = os.path.join(folder, "import_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _airframe_cleanup_tmp() -> None:
    meta = session.get(_AIRFRAME_IMPORT_SESSION_KEY)
    if meta:
        tmp = meta.get("tmp_path")
        if tmp and os.path.isfile(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)
    session.pop(_AIRFRAME_IMPORT_SESSION_KEY, None)


def _render_airframe_map(
    ac: Aircraft, parsed: Any, mapping: dict[str, str], match_type: str, filename: str
) -> str:
    from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
        AIRFRAME_TARGET_FIELDS,
        airframe_type_hints,
    )
    from pilots.logbook_import import _norm, preview_rows  # pyright: ignore[reportMissingImports]

    return render_template(
        "flights/airframe_import_map.html",
        aircraft=ac,
        norm_cols=parsed.norm_cols,
        raw_cols=parsed.raw_cols,
        base_norm_cols=[_norm(r) for r in parsed.raw_cols],
        mapping=mapping,
        match_type=match_type,
        target_fields=AIRFRAME_TARGET_FIELDS,
        preview=preview_rows(parsed, mapping, n=5),
        filename=filename,
        type_hints=airframe_type_hints(parsed, mapping),
    )


@flights_bp.route("/aircraft/<int:aircraft_id>/flights/import", methods=["GET", "POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_upload(aircraft_id: int) -> ResponseReturnValue:
    from models import AirframeImportBatch, AirframeImportMapping  # pyright: ignore[reportMissingImports]
    from flights.airframe_import import propose_airframe_mapping  # pyright: ignore[reportMissingImports]
    from pilots.logbook_import import parse_file  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    batches = (
        AirframeImportBatch.query.filter_by(aircraft_id=ac.id)
        .order_by(AirframeImportBatch.imported_at.desc())
        .all()
    )

    if request.method == "GET":
        return render_template(
            "flights/airframe_import_upload.html", aircraft=ac, batches=batches
        )

    uploaded = request.files.get("logbook_file")
    if not uploaded or not uploaded.filename:
        flash(_("Please select a file to upload."), "danger")
        return render_template(
            "flights/airframe_import_upload.html", aircraft=ac, batches=batches
        ), 422

    ext = os.path.splitext(uploaded.filename)[1].lower()
    if ext not in _AIRFRAME_IMPORT_EXTS:
        flash(_("Unsupported format. Please upload a .csv or .xlsx file."), "danger")
        return render_template(
            "flights/airframe_import_upload.html", aircraft=ac, batches=batches
        ), 422

    data = uploaded.read()
    if len(data) > _AIRFRAME_IMPORT_MAX_BYTES:
        flash(_("File too large (maximum 10 MB)."), "danger")
        return render_template(
            "flights/airframe_import_upload.html", aircraft=ac, batches=batches
        ), 422

    try:
        parsed = parse_file(data, uploaded.filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_template(
            "flights/airframe_import_upload.html", aircraft=ac, batches=batches
        ), 422

    _airframe_cleanup_tmp()
    safe_base = secure_filename(uploaded.filename) or "upload"
    tmp_path = os.path.join(
        _airframe_tmp_dir(), f"airframe_{ac.id}_{uuid.uuid4().hex}_{safe_base}"
    )
    with open(tmp_path, "wb") as fh:
        fh.write(data)

    session[_AIRFRAME_IMPORT_SESSION_KEY] = {
        "aircraft_id": ac.id,
        "tmp_path": tmp_path,
        "original_filename": uploaded.filename,
        "norm_cols": parsed.norm_cols,
        "fingerprint": parsed.fingerprint,
    }

    saved = AirframeImportMapping.query.filter_by(tenant_id=ac.tenant_id).all()
    mapping, match_type = propose_airframe_mapping(parsed, saved)
    return _render_airframe_map(ac, parsed, mapping, match_type, uploaded.filename)


@flights_bp.route(
    "/aircraft/<int:aircraft_id>/flights/import/execute", methods=["POST"]
)
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_execute(aircraft_id: int) -> ResponseReturnValue:
    from models import AirframeImportBatch, AirframeImportMapping  # pyright: ignore[reportMissingImports]
    from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
        AIRFRAME_TARGET_FIELDS,
        execute_airframe_import,
    )
    from pilots.logbook_import import parse_duration_value, parse_file  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    meta = session.get(_AIRFRAME_IMPORT_SESSION_KEY)
    if not meta or meta.get("aircraft_id") != ac.id:
        flash(_("Import session expired. Please upload the file again."), "warning")
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    tmp_path: str = meta["tmp_path"]
    original_filename: str = meta["original_filename"]
    norm_cols: list[str] = meta["norm_cols"]
    fingerprint: str = meta["fingerprint"]

    if not os.path.isfile(tmp_path):
        flash(_("Temporary file not found. Please upload the file again."), "warning")
        session.pop(_AIRFRAME_IMPORT_SESSION_KEY, None)
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    mapping: dict[str, str] = {}
    for col in norm_cols:
        val = request.form.get(f"mapping_{col}", "ignore").strip()
        mapping[col] = val if val in AIRFRAME_TARGET_FIELDS else "ignore"

    with open(tmp_path, "rb") as fh:
        data = fh.read()
    try:
        parsed = parse_file(data, original_filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        _airframe_cleanup_tmp()
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    if "date" not in mapping.values():
        flash(_("You must map at least one column to 'Date'."), "danger")
        return _render_airframe_map(
            ac, parsed, mapping, "alias", original_filename
        ), 422

    opening_counters = {
        "flight": parse_duration_value(
            request.form.get("ob_flight_counter", "").strip()
        )
        if request.form.get("ob_flight_counter", "").strip()
        else None,
        "engine": parse_duration_value(
            request.form.get("ob_engine_counter", "").strip()
        )
        if request.form.get("ob_engine_counter", "").strip()
        else None,
    }

    mapping_record = None
    for m in AirframeImportMapping.query.filter_by(tenant_id=ac.tenant_id).all():
        if m.source_fingerprint == fingerprint:
            m.column_mapping = _json.dumps(mapping)
            mapping_record = m
            break
    if mapping_record is None:
        mapping_record = AirframeImportMapping(
            tenant_id=ac.tenant_id,
            source_fingerprint=fingerprint,
            column_mapping=_json.dumps(mapping),
            source_columns=_json.dumps(norm_cols),
            created_at=_datetime.now(_timezone.utc),
        )
        db.session.add(mapping_record)
    db.session.flush()

    batch = AirframeImportBatch(
        aircraft_id=ac.id,
        mapping_id=mapping_record.id,
        source_filename=original_filename,
        imported_at=_datetime.now(_timezone.utc),
    )
    db.session.add(batch)
    db.session.flush()

    result = execute_airframe_import(
        parsed=parsed,
        mapping=mapping,
        aircraft=ac,
        batch_id=batch.id,
        opening_counters=opening_counters
        if any(v is not None for v in opening_counters.values())
        else None,
    )
    batch.row_count = result.imported
    batch.subtotal_count = result.subtotals
    batch.skipped_count = len(result.skipped)
    batch.warning_count = len(result.continuity_warnings)
    batch.has_opening_counters = result.has_opening_counters
    db.session.commit()

    activity(
        "flights.airframe_import",
        aircraft_id=ac.id,
        batch_id=batch.id,
        imported=result.imported,
    )
    _airframe_cleanup_tmp()

    flash(
        _(
            "Import complete: %(imported)d flights imported, %(subtotals)d subtotal rows "
            "skipped, %(skipped)d rows could not be parsed.",
            imported=result.imported,
            subtotals=result.subtotals,
            skipped=len(result.skipped),
        ),
        "success",
    )
    if result.continuity_warnings:
        detail = "; ".join(
            _(
                "row %(row)d: %(kind)s counter starts at %(got).1f but the previous "
                "entry ended at %(prev).1f",
                row=row,
                kind=kind,
                got=got,
                prev=prev,
            )
            for row, kind, prev, got in result.continuity_warnings[:5]
        )
        if len(result.continuity_warnings) > 5:
            detail += _(" … and %(n)d more", n=len(result.continuity_warnings) - 5)
        flash(_("Counter continuity warnings: %(detail)s", detail=detail), "warning")
    if result.skipped:
        detail = "; ".join(f"row {r}: {reason}" for r, reason in result.skipped[:5])
        if len(result.skipped) > 5:
            detail += f" … and {len(result.skipped) - 5} more"
        flash(_("Skipped rows: %(detail)s", detail=detail), "warning")

    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))


@flights_bp.route(
    "/aircraft/<int:aircraft_id>/flights/import/<int:batch_id>/rollback",
    methods=["POST"],
)
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_rollback(aircraft_id: int, batch_id: int) -> ResponseReturnValue:
    from models import AirframeImportBatch  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    batch = db.session.get(AirframeImportBatch, batch_id)
    if not batch or batch.aircraft_id != ac.id:
        abort(404)

    entry_ids = [
        row.id
        for row in FlightEntry.query.filter_by(airframe_import_batch_id=batch.id)
        .with_entities(FlightEntry.id)
        .all()
    ]
    if entry_ids:
        FlightCrew.query.filter(FlightCrew.flight_id.in_(entry_ids)).delete(
            synchronize_session=False
        )
        FlightEntry.query.filter(FlightEntry.id.in_(entry_ids)).delete(
            synchronize_session=False
        )
    db.session.delete(batch)
    db.session.commit()

    flash(
        _(
            "Import deleted: %(n)d flight entries removed.",
            n=len(entry_ids),
        ),
        "success",
    )
    return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))
