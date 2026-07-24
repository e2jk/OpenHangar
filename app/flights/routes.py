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
    Flight,
    GpsTrack,
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
from flights.form_parsing import (  # pyright: ignore[reportMissingImports]
    apply_flight_fields,
    parse_flight_fields,
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


def _check_flight_hour_milestone(fe: Flight) -> None:
    """Set a one-shot session flag when total fleet hours cross a milestone."""
    this_flight = float(fe.flight_time or 0)
    if this_flight <= 0:
        return
    tid = _tenant_id()
    aircraft_ids = [a.id for a in accessible_aircraft(tid).all()]
    new_total = float(
        db.session.query(func.sum(Flight.flight_time))
        .filter(Flight.aircraft_id.in_(aircraft_ids))
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


def _get_flight_or_404(flight_id: int) -> Flight:
    """Fetch a Flight row, authorizing by tenant (managed-aircraft rows) or
    by crew identity (standalone rows, aircraft_id NULL — no tenant to check,
    so only the pic/second-crew occupant may access it)."""
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


def _save_upload(file: Any, flight_id: int, label: str) -> str | None:
    # secure_filename() raises TypeError on None — file.filename is None
    # (not just "") when a multipart part omits the filename= attribute
    # entirely. The only current caller already guards this, but this
    # function shouldn't rely on that (found auditing every secure_filename
    # call site for the fuzzing backlog's "extension-allowlist logic"
    # entry).
    ext = os.path.splitext(secure_filename(file.filename or ""))[1].lower()
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
        for row in db.session.query(Flight.nature_of_flight)
        .filter_by(aircraft_id=aircraft_id)
        .filter(Flight.nature_of_flight.isnot(None))
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
) -> dict[str, Any] | None:
    """Return info about a matching Flight row, or None.

    Unified model note: a pilot's own standalone entry and an aircraft-log
    entry are the same table now, so there's only one id space to exclude —
    the old separate ``exclude_pilot_entry_id`` parameter is gone, since
    "my own linked entry for this flight" and "this flight" are the same
    row and the same id.
    """
    if aircraft_id and block_off and block_on:
        q = Flight.query.filter(
            Flight.aircraft_id == aircraft_id,
            Flight.block_off_utc.isnot(None),
            Flight.block_on_utc.isnot(None),
            Flight.block_off_utc < block_on,
            Flight.block_on_utc > block_off,
        )
        if exclude_flight_id:
            q = q.filter(Flight.id != exclude_flight_id)
        existing = q.first()
        if existing:
            return {"type": "flight", "entry": existing}

    if aircraft_id and not block_off:
        q2 = Flight.query.filter_by(
            aircraft_id=aircraft_id,
            date=date,
            departure_icao=dep_icao,
            arrival_icao=arr_icao,
        )
        if exclude_flight_id:
            q2 = q2.filter(Flight.id != exclude_flight_id)
        existing2 = q2.first()
        if existing2:
            return {"type": "flight", "entry": existing2}

    q3 = Flight.query.filter(
        or_(
            Flight.pic_user_id == pilot_user_id,
            Flight.second_crew_user_id == pilot_user_id,
        ),
        Flight.date == date,
        Flight.departure_icao == dep_icao,
        Flight.arrival_icao == arr_icao,
    )
    if exclude_flight_id:
        q3 = q3.filter(Flight.id != exclude_flight_id)
    existing3 = q3.first()
    if existing3:
        return {"type": "pilot", "entry": existing3}

    return None


def _get_counter_hint(aircraft_id: int) -> dict[str, float | None]:
    last = (
        Flight.query.filter_by(aircraft_id=aircraft_id)
        .filter(
            db.or_(
                Flight.flight_time_counter_end.isnot(None),
                Flight.engine_time_counter_end.isnot(None),
            )
        )
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
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


def apply_pilot_identity(
    fe: Flight,
    ac: Aircraft | None,
    uid: int,
    pilot_role: str,
) -> None:
    """Resolve the current user's own EASA figures onto whichever crew slot
    matches their `pilot_role` ("pic" -> `pic_user_id`, "dual" ->
    `second_crew_user_id`), and (re)compute the single_pilot_se/me split
    from `fe.flight_time` and `ac`'s category.

    Call only when `pilot_role` is "pic" or "dual". The caller is
    responsible for assigning `fe`'s other shared EASA fields (night_time,
    instrument_time, landings_day/night, multi_pilot) from the submitted
    form values first — those aren't tied to identity resolution, unlike
    the fields here.

    `ac` is None for the "other aircraft" case (no fleet aircraft to derive
    a category from) — treated as single-engine, matching the old inline
    no-fleet-aircraft branch's behaviour.

    Two pilots logging the same real flight must never end up with
    different figures for it — that was a bug in the pre-refactor
    two-table design, not a feature; this unified row makes it structurally
    impossible, since there's only one set of EASA figures per flight.
    """
    ft_decimal = fe.flight_time
    cat = _ac_category(ac) if ac is not None else "SEP"
    fe.single_pilot_se = ft_decimal if cat in ("SEP", "SET", "") else None
    fe.single_pilot_me = ft_decimal if cat in ("MEP", "MET") else None

    _u = db.session.get(User, uid)
    display_name = _u.display_name if _u else ""

    if pilot_role == "pic":
        fe.pic_user_id = uid
        if not fe.pic_name:
            fe.pic_name = display_name
        fe.function_pic = ft_decimal
        fe.function_dual = None
    else:  # "dual"
        fe.second_crew_user_id = uid
        if not fe.second_crew_name:
            fe.second_crew_name = display_name
        if not fe.second_crew_role:
            fe.second_crew_role = CrewRole.STUDENT
        fe.function_dual = ft_decimal
        fe.function_pic = None


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
        # Counter and fuel photos are stored directly on Flight (not via Document).
        fe = Flight.query.filter(
            or_(
                Flight.flight_counter_photo == filename,
                Flight.engine_counter_photo == filename,
                Flight.fuel_photo == filename,
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
        Flight.query.filter(Flight.aircraft_id.in_([ac.id for ac in aircraft_list]))
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
        )
        .all()
    )
    return render_template(
        "flights/fleet.html", flights=flights, aircraft_map=aircraft_map
    )


# ── Airframe logbook ──────────────────────────────────────────────────────────


@flights_bp.route("/aircraft/<aircraft_ref:aircraft_id>/flights")
@login_required
def list_flights(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    flights = (
        Flight.query.filter_by(aircraft_id=ac.id)
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
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


@flights_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/components/<int:component_id>/logbook"
)
@login_required
def component_logbook(aircraft_id: int, component_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    comp = db.session.get(Component, component_id)
    if not comp or comp.aircraft_id != ac.id:
        abort(404)

    query = Flight.query.filter_by(aircraft_id=ac.id)
    if comp.installed_at:
        query = query.filter(Flight.date >= comp.installed_at)
    if comp.removed_at:
        query = query.filter(Flight.date <= comp.removed_at)

    flights_asc = query.order_by(
        Flight.date.asc(),
        Flight.departure_time.asc().nullslast(),
        Flight.id.asc(),
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
    # Unified model: every pilot-log field now lives on `fe` itself — no
    # separate PilotLogbookEntry to fetch.
    aircraft = db.session.get(Aircraft, fe.aircraft_id) if fe.aircraft_id else None
    counter_hint = _get_counter_hint(fe.aircraft_id) if fe.aircraft_id else None

    return render_template(
        "flights/flight_form.html",
        flight=fe,
        pilot_entry=None,
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

    # Only "other aircraft" (unmanaged) rows carry a free-text registration —
    # a managed aircraft's type is already known via its own Aircraft record.
    # Source 1: current user's own history (either crew slot)
    user_entries = (
        Flight.query.filter(Flight.aircraft_id.is_(None))
        .filter(or_(Flight.pic_user_id == uid, Flight.second_crew_user_id == uid))
        .filter(Flight.other_aircraft_registration.isnot(None))
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
        )
        .all()
    )
    for e in user_entries:
        if (
            _norm(e.other_aircraft_registration or "") == q_norm
            and e.other_aircraft_type
        ):
            return jsonify(
                {
                    "result": {
                        "aircraft_type": e.other_aircraft_type,
                        "aircraft_type_icao": e.other_aircraft_type_icao or "",
                    }
                }
            )

    # Source 2: any user in the same tenant
    from models import TenantUser as _TU  # pyright: ignore[reportMissingImports]

    tenant_user_ids = db.session.query(_TU.user_id).filter(_TU.tenant_id == tid)
    tenant_entries = (
        Flight.query.filter(Flight.aircraft_id.is_(None))
        .filter(
            or_(
                Flight.pic_user_id.in_(tenant_user_ids),
                Flight.second_crew_user_id.in_(tenant_user_ids),
            )
        )
        .filter(Flight.other_aircraft_registration.isnot(None))
        .filter(Flight.other_aircraft_type.isnot(None))
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
        )
        .all()
    )
    for e in tenant_entries:
        if _norm(e.other_aircraft_registration or "") == q_norm:
            return jsonify(
                {
                    "result": {
                        "aircraft_type": e.other_aircraft_type,
                        "aircraft_type_icao": e.other_aircraft_type_icao or "",
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
        db.session.query(Flight.aircraft_id)
        .join(GpsTrack, Flight.gps_track_id == GpsTrack.id)
        .filter(GpsTrack.device_id == device_id)
        .order_by(
            Flight.date.desc(),
            Flight.departure_time.desc().nullslast(),
            Flight.id.desc(),
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
    fe: Flight | None,
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
    pilot_role = f.get("pilot_role", "none").strip()
    if pilot_role not in ("pic", "dual", "none"):
        pilot_role = "none"

    # Pilot-log fields
    night_time_raw = f.get("night_time", "").strip()
    instrument_time_raw = f.get("instrument_time", "").strip()
    landings_day_raw = f.get("landings_day", "").strip()
    landings_night_raw = f.get("landings_night", "").strip()
    multi_pilot_raw = f.get("multi_pilot", "").strip()

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

    # GPS hidden fields (carried from parse step or re-render)
    gps_filename = f.get("gps_filename", "").strip() or None
    gps_device_id = f.get("gps_device_id", "").strip() or None
    gps_block_off_raw = f.get("gps_block_off_utc", "").strip()
    gps_block_on_raw = f.get("gps_block_on_utc", "").strip()
    gps_geojson_raw = f.get("gps_geojson", "").strip()

    duplicate_action = f.get("duplicate_action", "").strip()

    errors = []

    if not fe and not ac and not other_aircraft:
        errors.append(_("Please select an aircraft."))

    if other_aircraft and pilot_role not in ("pic", "dual"):
        errors.append(_("Pilot role is required for other aircraft flights."))
    if (
        other_aircraft
        and pilot_role in ("pic", "dual")
        and not f.get("crew_name_0", "").strip()
    ):
        errors.append(_("Pilot name is required."))
    if other_aircraft and not other_ac_make_model:
        errors.append(
            _("Aircraft type (make/model) is required for other aircraft flights.")
        )
    if other_aircraft and not other_ac_reg:
        errors.append(
            _("Aircraft registration is required for other aircraft flights.")
        )

    # The aircraft-log `landing_count` is derived from the pilot-log day/night
    # split (there is no separate `landing_count` form field); when neither is
    # given, an existing value is preserved rather than cleared — mirrored here
    # by injecting the resolved value into the field map handed to
    # parse_flight_fields, which always assigns it unconditionally.
    if landings_day is not None or landings_night is not None:
        landing_count_for_fe: int | None = (landings_day or 0) + (landings_night or 0)
    else:
        landing_count_for_fe = fe.landing_count if fe else None
    field_map = dict(f)
    field_map["landing_count"] = (
        str(landing_count_for_fe) if landing_count_for_fe is not None else ""
    )
    values, field_errors = parse_flight_fields(field_map, ac)
    errors.extend(field_errors)

    flight_date = values["date"]
    dep = values["departure_icao"]
    arr = values["arrival_icao"]
    departure_time = values["departure_time"]

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
            entry.gps_track_id = link_track.id
            db.session.commit()
            flash(_("GPS track linked to the existing flight entry."), "success")
        else:
            flash(_("Could not link GPS track — no matching entry found."), "warning")
        return redirect(url_for("pilots.logbook"))

    # ── Build GpsTrack if GPS data is present ─────────────────────────────────
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

    # ── Unified flight row ─────────────────────────────────────────────────────
    # A Flight row now always exists, whether or not the aircraft is managed
    # here — the old "other aircraft" path built a standalone PilotLogbookEntry
    # instead of a FlightEntry; that distinction no longer exists in the
    # unified schema, only aircraft_id being NULL vs set.
    _fe_is_new = fe is None
    if fe is None:
        fe = Flight(aircraft_id=ac.id if ac else None)
        db.session.add(fe)
    else:
        fe.aircraft_id = ac.id if ac else None

    if ac:
        fe.other_aircraft_type = None
        fe.other_aircraft_type_icao = None
        fe.other_aircraft_registration = None
    else:
        fe.other_aircraft_type = other_ac_make_model or None
        fe.other_aircraft_type_icao = f.get("aircraft_type_icao", "").strip() or None
        fe.other_aircraft_registration = other_ac_reg or None

    apply_flight_fields(fe, values)

    if gps_track:
        fe.gps_track_id = gps_track.id
    if gps_block_off:
        fe.block_off_utc = gps_block_off
    if gps_block_on:
        fe.block_on_utc = gps_block_on

    if ac:
        if _fe_is_new and flight_date is not None:
            anchor = _datetime.combine(
                flight_date, departure_time or _time(12, 0), tzinfo=_timezone.utc
            )
            covering = _find_covering_reservation(ac.id, uid, anchor)
            fe.reservation_id = covering.id if covering else None

        db.session.flush()

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

    # ── Pilot log figures ──────────────────────────────────────────────────────
    if create_pilot:
        fe.night_time = night_time
        fe.instrument_time = instrument_time
        fe.landings_day = landings_day if landings_day is not None else 0
        fe.landings_night = landings_night
        fe.multi_pilot = multi_pilot
        apply_pilot_identity(fe, ac, uid, pilot_role)
    else:
        # pilot_role == "none": this user isn't tracking a personal logbook
        # entry for this flight. If they previously occupied a slot on this
        # same flight (editing), un-claim just their identity + function
        # figures — the shared EASA figures (night_time etc.) stay, since
        # they describe the flight itself and another crew member may still
        # depend on them.
        if fe.pic_user_id == uid:
            fe.pic_user_id = None
            fe.function_pic = None
        elif fe.second_crew_user_id == uid:
            fe.second_crew_user_id = None
            fe.function_dual = None

    db.session.commit()

    if ac:
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
    flight: Flight | None,
    pilot_entry: None,
    preselect_id: int | None,
    duplicate: dict[str, Any] | None,
) -> ResponseReturnValue:
    """`pilot_entry` is always None now — kept as a parameter only so every
    call site doesn't need touching; every pilot-log field lives on `flight`
    itself in the unified model."""
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
    "/aircraft/<aircraft_ref:aircraft_id>/flights/<int:flight_id>/delete",
    methods=["POST"],
)
@login_required
@require_pilot_access
def delete_flight(aircraft_id: int, flight_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    fe = db.session.get(Flight, flight_id)
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
_AIRFRAME_IMPORT_REVIEW_SESSION_KEY = "airframe_import_review"
_AIRFRAME_IMPORT_EXTS = {".csv", ".xlsx", ".xls"}
_AIRFRAME_IMPORT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _airframe_tmp_dir() -> str:
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    d = os.path.join(folder, "import_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _airframe_cleanup_tmp() -> None:
    """Delete any leftover temp import file, including one left behind by
    an abandoned conflict-review (started a fresh upload instead of
    finishing it)."""
    meta = session.get(_AIRFRAME_IMPORT_SESSION_KEY)
    if meta:
        tmp = meta.get("tmp_path")
        if tmp and os.path.isfile(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)
    session.pop(_AIRFRAME_IMPORT_SESSION_KEY, None)

    review_state = session.get(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY)
    if review_state:
        tmp = review_state.get("tmp_path")
        if tmp and os.path.isfile(tmp):
            with contextlib.suppress(OSError):
                os.remove(tmp)
    session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)


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


@flights_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/flights/import", methods=["GET", "POST"]
)
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
    "/aircraft/<aircraft_ref:aircraft_id>/flights/import/execute", methods=["POST"]
)
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_execute(aircraft_id: int) -> ResponseReturnValue:
    from models import AirframeImportBatch, AirframeImportMapping  # pyright: ignore[reportMissingImports]
    from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
        AIRFRAME_TARGET_FIELDS,
        execute_airframe_import,
        find_conflicting_airframe_rows,
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

    resolved_opening_counters = (
        opening_counters
        if any(v is not None for v in opening_counters.values())
        else None
    )

    # Rows that look like they might be an edited version of an existing
    # flight (score >= _CANDIDATE_MIN_SCORE) need a human decision, not a
    # guess — carve them out of this pass and route them through the
    # interactive review step below instead of silently importing or
    # skipping them.
    conflicts = find_conflicting_airframe_rows(parsed, mapping, ac.id)

    result = execute_airframe_import(
        parsed=parsed,
        mapping=mapping,
        aircraft=ac,
        batch_id=batch.id,
        opening_counters=resolved_opening_counters,
        skip_row_nums={c.row_num for c in conflicts},
    )
    batch.row_count = result.imported
    batch.subtotal_count = result.subtotals
    batch.skipped_count = len(result.skipped)
    batch.warning_count = len(result.continuity_warnings)
    batch.has_opening_counters = result.has_opening_counters
    db.session.commit()

    if conflicts:
        # Defer activity logging and tmp-file cleanup until every conflict
        # is resolved — _finalize_airframe_import_review does both, covering
        # entries added during review as well as the ones just committed.
        session[_AIRFRAME_IMPORT_REVIEW_SESSION_KEY] = {
            "aircraft_id": ac.id,
            "tmp_path": tmp_path,
            "original_filename": original_filename,
            "mapping": mapping,
            "batch_id": batch.id,
            "resolved": {},
        }
        session.pop(_AIRFRAME_IMPORT_SESSION_KEY, None)
        session.modified = True

        flash(
            _(
                "%(imported)d flights imported so far. %(n)d rows look like "
                "they might already be in this aircraft's log with "
                "different data — please review them below.",
                imported=result.imported,
                n=len(conflicts),
            ),
            "info",
        )
        if result.duplicates:
            detail = "; ".join(
                f"row {r}: {reason}" for r, reason in result.duplicates[:5]
            )
            if len(result.duplicates) > 5:
                detail += f" … and {len(result.duplicates) - 5} more"
            flash(
                _(
                    "Rows already in this aircraft's log, skipped: %(detail)s",
                    detail=detail,
                ),
                "info",
            )
        if result.skipped:
            detail = "; ".join(f"row {r}: {reason}" for r, reason in result.skipped[:5])
            if len(result.skipped) > 5:
                detail += f" … and {len(result.skipped) - 5} more"
            flash(_("Skipped rows: %(detail)s", detail=detail), "warning")

        return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))

    activity(
        "flights.airframe_import",
        aircraft_id=ac.id,
        batch_id=batch.id,
        imported=result.imported,
    )
    _airframe_cleanup_tmp()

    # Duplicates are a normal, expected outcome — e.g. re-uploading a
    # spreadsheet after appending a few new flights — not an error, so these
    # messages stay in the "success"/"info" register rather than "warning".
    if result.imported == 0 and result.duplicates:
        flash(
            _(
                "All flights in this file were already in this aircraft's "
                "log — nothing new was imported."
            ),
            "success",
        )
    elif result.duplicates:
        flash(
            _(
                "Import complete: %(imported)d new flights imported, "
                "%(duplicates)d rows were already in this aircraft's log and "
                "were skipped, %(subtotals)d subtotal rows skipped, "
                "%(skipped)d rows could not be parsed.",
                imported=result.imported,
                duplicates=len(result.duplicates),
                subtotals=result.subtotals,
                skipped=len(result.skipped),
            ),
            "success",
        )
    else:
        flash(
            _(
                "Import complete: %(imported)d flights imported, %(subtotals)d "
                "subtotal rows skipped, %(skipped)d rows could not be parsed.",
                imported=result.imported,
                subtotals=result.subtotals,
                skipped=len(result.skipped),
            ),
            "success",
        )
    if result.duplicates:
        detail = "; ".join(f"row {r}: {reason}" for r, reason in result.duplicates[:5])
        if len(result.duplicates) > 5:
            detail += f" … and {len(result.duplicates) - 5} more"
        flash(
            _(
                "Rows already in this aircraft's log, skipped: %(detail)s",
                detail=detail,
            ),
            "info",
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


def _finalize_airframe_import_review(
    ac: Aircraft, state: dict[str, Any]
) -> ResponseReturnValue:
    """Common tail once every conflict row from a review has a decision:
    activity log, tmp-file/session cleanup, summary flash, redirect — the
    same shape as the no-conflicts tail of airframe_import_execute above."""
    from models import AirframeImportBatch  # pyright: ignore[reportMissingImports]

    batch_id: int = state["batch_id"]
    tmp_path: str = state["tmp_path"]
    batch = db.session.get(AirframeImportBatch, batch_id)

    activity(
        "flights.airframe_import",
        aircraft_id=ac.id,
        batch_id=batch_id,
        imported=batch.row_count if batch else 0,
    )

    with contextlib.suppress(OSError):
        os.remove(tmp_path)
    session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)

    resolved: dict[str, str] = state.get("resolved", {})
    kept = sum(1 for d in resolved.values() if d == "keep")
    overwritten = sum(1 for d in resolved.values() if d.startswith("overwrite:"))
    added_new = sum(1 for d in resolved.values() if d == "new")

    flash(
        _(
            "Review complete: %(overwritten)d flights updated, %(new)d "
            "imported as new, %(kept)d left unchanged.",
            overwritten=overwritten,
            new=added_new,
            kept=kept,
        ),
        "success",
    )

    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))


@flights_bp.route("/aircraft/<aircraft_ref:aircraft_id>/flights/import/review")
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_review(aircraft_id: int) -> ResponseReturnValue:
    from flights.airframe_import import find_conflicting_airframe_rows  # pyright: ignore[reportMissingImports]
    from pilots.logbook_import import parse_file  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    state = session.get(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY)
    if not state or state.get("aircraft_id") != ac.id:
        flash(_("Import session expired. Please upload the file again."), "warning")
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    tmp_path: str = state["tmp_path"]
    if not os.path.isfile(tmp_path):
        flash(_("Temporary file not found. Please upload the file again."), "warning")
        session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    mapping: dict[str, str] = state["mapping"]
    resolved: dict[str, str] = state.get("resolved", {})

    with open(tmp_path, "rb") as fh:
        data = fh.read()
    try:
        parsed = parse_file(data, state["original_filename"])
    except ValueError:
        session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    exclude_row_nums = {int(k) for k in resolved}
    conflicts = find_conflicting_airframe_rows(
        parsed, mapping, ac.id, exclude_row_nums=exclude_row_nums
    )

    if not conflicts:
        return _finalize_airframe_import_review(ac, state)

    candidate_ids = {cid for c in conflicts for _score, cid in c.candidates}
    candidate_entries: dict[int, Flight] = (
        {e.id: e for e in Flight.query.filter(Flight.id.in_(candidate_ids))}
        if candidate_ids
        else {}
    )

    rows = [
        {
            "row_num": c.row_num,
            "fields": c.fields,
            "crew_name": c.crew_name,
            "candidates": [
                {"id": cid, "score": score, "entry": candidate_entries.get(cid)}
                for score, cid in c.candidates
            ],
        }
        for c in conflicts
    ]

    return render_template(
        "flights/airframe_import_review.html",
        aircraft=ac,
        rows=rows,
        resolved_count=len(resolved),
        total_count=len(resolved) + len(conflicts),
    )


@flights_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/flights/import/review/resolve",
    methods=["POST"],
)
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def airframe_import_review_resolve(aircraft_id: int) -> ResponseReturnValue:
    from flights.airframe_import import (  # pyright: ignore[reportMissingImports]
        AirframeConflictRow,
        _fields_to_flight_entry_kwargs,
        find_conflicting_airframe_rows,
    )
    from pilots.logbook_import import parse_file  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    state = session.get(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY)
    if not state or state.get("aircraft_id") != ac.id:
        flash(_("Import session expired. Please upload the file again."), "warning")
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    tmp_path: str = state["tmp_path"]
    mapping: dict[str, str] = state["mapping"]
    resolved: dict[str, str] = state.get("resolved", {})

    try:
        row_num = int(request.form.get("row_num", ""))
    except (ValueError, TypeError):
        flash(_("Invalid row number."), "danger")
        return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))

    if str(row_num) in resolved:
        flash(_("This row has already been resolved."), "info")
        return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))

    if not os.path.isfile(tmp_path):
        flash(_("Temporary file not found. Please upload the file again."), "warning")
        session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    with open(tmp_path, "rb") as fh:
        data = fh.read()
    try:
        parsed = parse_file(data, state["original_filename"])
    except ValueError:
        session.pop(_AIRFRAME_IMPORT_REVIEW_SESSION_KEY, None)
        return redirect(url_for("flights.airframe_import_upload", aircraft_id=ac.id))

    exclude_row_nums = {int(k) for k in resolved}
    conflicts = find_conflicting_airframe_rows(
        parsed, mapping, ac.id, exclude_row_nums=exclude_row_nums
    )
    conflict: AirframeConflictRow | None = next(
        (c for c in conflicts if c.row_num == row_num), None
    )
    if conflict is None:
        flash(_("Invalid row number."), "danger")
        return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))

    decision = request.form.get("decision", "")
    candidate_ids = {cid for _score, cid in conflict.candidates}

    if decision == "keep":
        pass  # the freshly-parsed row is discarded; the existing entry is untouched
    elif decision.startswith("overwrite:"):
        try:
            existing_id = int(decision.split(":", 1)[1])
        except ValueError:
            existing_id = -1
        if existing_id not in candidate_ids:
            flash(_("Invalid selection."), "danger")
            return redirect(
                url_for("flights.airframe_import_review", aircraft_id=ac.id)
            )
        existing = Flight.query.filter_by(id=existing_id, aircraft_id=ac.id).first()
        if existing is None:
            # candidate_ids just came from a live query for this aircraft in
            # the same request — only a concurrent delete reaches this.
            abort(404)  # pragma: no cover
        # Full replace of every field this row provides — id and
        # airframe_import_batch_id are deliberately left untouched, so this
        # entry stays outside the *current* batch's rollback (it wasn't
        # created by it).
        for field_name, value in _fields_to_flight_entry_kwargs(
            conflict.fields
        ).items():
            setattr(existing, field_name, value)
        if conflict.crew_name and not existing.pic_name:
            existing.pic_name = conflict.crew_name
        db.session.commit()
    elif decision == "new":
        from models import AirframeImportBatch  # pyright: ignore[reportMissingImports]

        batch_id: int = state["batch_id"]
        fe = Flight(
            aircraft_id=ac.id,
            airframe_import_batch_id=batch_id,
            source="import",
            pic_name=conflict.crew_name,
            **_fields_to_flight_entry_kwargs(conflict.fields),
        )
        db.session.add(fe)
        db.session.flush()
        batch = db.session.get(AirframeImportBatch, batch_id)
        if batch is not None:
            batch.row_count += 1
        else:
            current_app.logger.debug(  # pragma: no cover — batch was just created
                "airframe_import_review_resolve: batch %s vanished before resolve",
                batch_id,
            )
        db.session.commit()
    else:
        flash(_("Invalid decision."), "danger")
        return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))

    resolved[str(row_num)] = decision
    state["resolved"] = resolved
    session[_AIRFRAME_IMPORT_REVIEW_SESSION_KEY] = state
    session.modified = True

    remaining = find_conflicting_airframe_rows(
        parsed, mapping, ac.id, exclude_row_nums={int(k) for k in resolved}
    )
    if not remaining:
        return _finalize_airframe_import_review(ac, state)

    return redirect(url_for("flights.airframe_import_review", aircraft_id=ac.id))


@flights_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/flights/import/<int:batch_id>/rollback",
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
        for row in Flight.query.filter_by(airframe_import_batch_id=batch.id)
        .with_entities(Flight.id)
        .all()
    ]
    if entry_ids:
        Flight.query.filter(Flight.id.in_(entry_ids)).delete(synchronize_session=False)
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
