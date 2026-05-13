import os
import uuid
from datetime import date as _date, time as _time

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename  # type: ignore

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import Aircraft, Component, CrewRole, FlightCrew, FlightEntry, Role, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import login_required, require_role  # pyright: ignore[reportMissingImports]

flights_bp = Blueprint("flights", __name__)

_PILOT_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT)

_ALLOWED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
_FUEL_UNITS = ["L", "gal"]
_NATURE_SUGGESTIONS = [
    "Local flight", "Navigation", "Cross-country", "Training",
    "IFR practice", "Night flight", "Touch-and-go", "Ferry flight",
    "Air test", "Sightseeing",
]


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return tu.tenant_id


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if not ac or ac.tenant_id != _tenant_id():
        abort(404)
    return ac


def _get_flight_or_404(aircraft: Aircraft, flight_id: int) -> FlightEntry:
    fe = db.session.get(FlightEntry, flight_id)
    if not fe or fe.aircraft_id != aircraft.id:
        abort(404)
    return fe


def _save_upload(file, flight_id: int, label: str) -> str | None:
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
        pass


# ── Serve uploads ─────────────────────────────────────────────────────────────

@flights_bp.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    return send_from_directory(folder, filename)


# ── Fleet logbook ─────────────────────────────────────────────────────────────

@flights_bp.route("/flights")
@login_required
def fleet_flights():
    tid = _tenant_id()
    aircraft_list = Aircraft.query.filter_by(tenant_id=tid).order_by(Aircraft.registration).all()
    aircraft_map = {ac.id: ac for ac in aircraft_list}
    flights = (
        FlightEntry.query
        .filter(FlightEntry.aircraft_id.in_([ac.id for ac in aircraft_list]))
        .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
        .all()
    )
    return render_template("flights/fleet.html", flights=flights, aircraft_map=aircraft_map)


# ── Airframe logbook ──────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights")
@login_required
def list_flights(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    flights = (
        FlightEntry.query
        .filter_by(aircraft_id=ac.id)
        .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
        .all()
    )
    return render_template("flights/list.html", aircraft=ac, flights=flights)


# ── Component logbook ─────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/components/<int:component_id>/logbook")
@login_required
def component_logbook(aircraft_id, component_id):
    ac = _get_aircraft_or_404(aircraft_id)
    comp = db.session.get(Component, component_id)
    if not comp or comp.aircraft_id != ac.id:
        abort(404)

    query = FlightEntry.query.filter_by(aircraft_id=ac.id)
    if comp.installed_at:
        query = query.filter(FlightEntry.date >= comp.installed_at)
    if comp.removed_at:
        query = query.filter(FlightEntry.date <= comp.removed_at)

    flights_asc = query.order_by(FlightEntry.date.asc(), FlightEntry.id.asc()).all()

    base = float(comp.time_at_install or 0)
    cumulative = base
    flights_with_hours = []
    for f in flights_asc:
        cumulative += float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
        flights_with_hours.append((f, cumulative))

    flights_with_hours.reverse()

    tbo_hours = (comp.extras or {}).get("tbo_hours")
    tbo_remaining = (tbo_hours - cumulative) if tbo_hours else None

    return render_template(
        "flights/logbook_component.html",
        aircraft=ac,
        component=comp,
        flights_with_hours=flights_with_hours,
        total_component_hours=cumulative,
        tbo_hours=tbo_hours,
        tbo_remaining=tbo_remaining,
    )


# ── Log flight ────────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/new", methods=["GET", "POST"])
@login_required
@require_role(*_PILOT_ROLES)
def new_flight(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_flight(ac, None)
    prev = (FlightEntry.query
            .filter_by(aircraft_id=ac.id)
            .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
            .first())
    counter_hint = {
        "flight": float(prev.flight_time_counter_end) if prev and prev.flight_time_counter_end is not None else None,
        "engine": float(prev.engine_time_counter_end) if prev and prev.engine_time_counter_end is not None else None,
    }
    nature_suggestions = _nature_suggestions(ac.id)
    from models import User
    _u = db.session.get(User, session.get("user_id"))
    pilot_name_hint = _u.display_name if _u else ""
    return render_template("flights/flight_form.html", aircraft=ac,
                           flight=None, counter_hint=counter_hint,
                           nature_suggestions=nature_suggestions,
                           pilot_name_hint=pilot_name_hint,
                           crew_roles=CrewRole, fuel_units=_FUEL_UNITS)


# ── Edit flight ───────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/<int:flight_id>/edit",
                  methods=["GET", "POST"])
@login_required
@require_role(*_PILOT_ROLES)
def edit_flight(aircraft_id, flight_id):
    ac = _get_aircraft_or_404(aircraft_id)
    fe = _get_flight_or_404(ac, flight_id)
    if request.method == "POST":
        return _save_flight(ac, fe)
    nature_suggestions = _nature_suggestions(ac.id)
    return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                           counter_hint=None,
                           nature_suggestions=nature_suggestions,
                           crew_roles=CrewRole, fuel_units=_FUEL_UNITS)


def _nature_suggestions(aircraft_id: int) -> list[str]:
    used = [
        row[0] for row in
        db.session.query(FlightEntry.nature_of_flight)
        .filter_by(aircraft_id=aircraft_id)
        .filter(FlightEntry.nature_of_flight.isnot(None))
        .distinct().all()
    ]
    return _NATURE_SUGGESTIONS + [n for n in used if n not in _NATURE_SUGGESTIONS]


def _save_flight(ac: Aircraft, fe: FlightEntry | None):
    date_raw = request.form.get("date", "").strip()
    dep = request.form.get("departure_icao", "").strip().upper()
    arr = request.form.get("arrival_icao", "").strip().upper()
    departure_time_raw = request.form.get("departure_time", "").strip()
    arrival_time_raw = request.form.get("arrival_time", "").strip()
    flight_time_raw = request.form.get("flight_time", "").strip()
    nature_of_flight = request.form.get("nature_of_flight", "").strip() or None
    passenger_count_raw = request.form.get("passenger_count", "").strip()
    landing_count_raw = request.form.get("landing_count", "").strip()
    flight_time_counter_start_raw = request.form.get("flight_time_counter_start", "").strip()
    flight_time_counter_end_raw = request.form.get("flight_time_counter_end", "").strip()
    notes = request.form.get("notes", "").strip() or None
    engine_time_counter_start_raw = request.form.get("engine_time_counter_start", "").strip()
    engine_time_counter_end_raw = request.form.get("engine_time_counter_end", "").strip()
    fuel_event_raw = request.form.get("fuel_event", "none").strip()
    fuel_added_qty_raw = request.form.get("fuel_added_qty", "").strip()
    fuel_added_unit = request.form.get("fuel_added_unit", "L").strip()
    fuel_remaining_qty_raw = request.form.get("fuel_remaining_qty", "").strip()

    crew_name_0 = request.form.get("crew_name_0", "").strip()
    crew_role_0 = request.form.get("crew_role_0", CrewRole.PIC).strip()
    crew_name_1 = request.form.get("crew_name_1", "").strip()
    crew_role_1 = request.form.get("crew_role_1", CrewRole.COPILOT).strip()

    errors = []

    flight_date = None
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
    if not crew_name_0:
        errors.append(_("Pilot (crew 1) name is required."))

    departure_time = arrival_time = None
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

    flight_time_counter_start = flight_time_counter_end = None
    if flight_time_counter_start_raw:
        try:
            flight_time_counter_start = float(flight_time_counter_start_raw)
            if flight_time_counter_start < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Flight counter start must be a positive number."))

    if flight_time_counter_end_raw:
        try:
            flight_time_counter_end = float(flight_time_counter_end_raw)
            if flight_time_counter_end < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Flight counter end must be a positive number."))

    if flight_time_counter_start is not None and flight_time_counter_end is not None and flight_time_counter_end <= flight_time_counter_start:
        errors.append(_("Flight counter end must be greater than flight counter start."))

    engine_time_counter_start = engine_time_counter_end = None
    if engine_time_counter_start_raw:
        try:
            engine_time_counter_start = float(engine_time_counter_start_raw)
            if engine_time_counter_start < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Engine counter start must be a positive number."))

    if engine_time_counter_end_raw:
        try:
            engine_time_counter_end = float(engine_time_counter_end_raw)
            if engine_time_counter_end < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Engine counter end must be a positive number."))

    if engine_time_counter_start is not None and engine_time_counter_end is not None and engine_time_counter_end <= engine_time_counter_start:
        errors.append(_("Engine counter end must be greater than engine counter start."))

    # Derive flight_time: manual override > counter diff > engine−offset (tach-only)
    flight_time = None
    if flight_time_raw:
        try:
            flight_time = round(float(flight_time_raw), 1)
            if flight_time < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Flight time must be a non-negative number."))
    elif flight_time_counter_start is not None and flight_time_counter_end is not None:
        flight_time = round(flight_time_counter_end - flight_time_counter_start, 1)
    elif (not ac.has_flight_counter
          and engine_time_counter_start is not None
          and engine_time_counter_end is not None):
        raw = (engine_time_counter_end - engine_time_counter_start) - float(ac.flight_counter_offset)
        flight_time = round(max(0.0, raw), 1)

    passenger_count = None
    if passenger_count_raw:
        try:
            passenger_count = int(passenger_count_raw)
            if passenger_count < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Passenger count must be a non-negative integer."))

    landing_count = None
    if landing_count_raw:
        try:
            landing_count = int(landing_count_raw)
            if landing_count < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Landing count must be a non-negative integer."))

    fuel_event = fuel_event_raw if fuel_event_raw in ("before", "after") else None

    fuel_added_qty = None
    if fuel_event and fuel_added_qty_raw:
        try:
            fuel_added_qty = float(fuel_added_qty_raw)
            if fuel_added_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Fuel quantity added must be a non-negative number."))

    fuel_remaining_qty = None
    if fuel_remaining_qty_raw:
        try:
            fuel_remaining_qty = float(fuel_remaining_qty_raw)
            if fuel_remaining_qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(_("Fuel remaining must be a non-negative number."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                               counter_hint=None,
                               nature_suggestions=_nature_suggestions(ac.id),
                               crew_roles=CrewRole, fuel_units=_FUEL_UNITS)

    if fe is None:
        fe = FlightEntry(aircraft_id=ac.id)
        db.session.add(fe)

    fe.date = flight_date
    fe.departure_icao = dep
    fe.arrival_icao = arr
    fe.departure_time = departure_time
    fe.arrival_time = arrival_time
    fe.flight_time = flight_time
    fe.nature_of_flight = nature_of_flight
    fe.passenger_count = passenger_count
    fe.landing_count = landing_count
    fe.flight_time_counter_start = flight_time_counter_start
    fe.flight_time_counter_end = flight_time_counter_end
    fe.notes = notes
    fe.engine_time_counter_start = engine_time_counter_start
    fe.engine_time_counter_end = engine_time_counter_end

    db.session.flush()

    # Replace crew records
    FlightCrew.query.filter_by(flight_id=fe.id).delete()
    db.session.add(FlightCrew(
        flight_id=fe.id,
        name=crew_name_0,
        role=crew_role_0 if crew_role_0 in CrewRole.ALL else CrewRole.PIC,
        sort_order=0,
    ))
    if crew_name_1:
        db.session.add(FlightCrew(
            flight_id=fe.id,
            name=crew_name_1,
            role=crew_role_1 if crew_role_1 in CrewRole.ALL else CrewRole.COPILOT,
            sort_order=1,
        ))

    flight_counter_file = request.files.get("flight_counter_photo")
    if flight_counter_file and flight_counter_file.filename:
        stored = _save_upload(flight_counter_file, fe.id, "flight")
        if stored:
            _delete_upload(fe.flight_counter_photo)
            fe.flight_counter_photo = stored

    engine_counter_file = request.files.get("engine_counter_photo")
    if engine_counter_file and engine_counter_file.filename:
        stored = _save_upload(engine_counter_file, fe.id, "engine")
        if stored:
            _delete_upload(fe.engine_counter_photo)
            fe.engine_counter_photo = stored

    fuel_photo_file = request.files.get("fuel_photo")
    if fuel_photo_file and fuel_photo_file.filename:
        stored = _save_upload(fuel_photo_file, fe.id, "fuel")
        if stored:
            _delete_upload(fe.fuel_photo)
            fe.fuel_photo = stored

    fe.fuel_event = fuel_event
    fe.fuel_added_qty = fuel_added_qty
    fe.fuel_added_unit = fuel_added_unit if fuel_added_qty is not None else None
    fe.fuel_remaining_qty = fuel_remaining_qty

    db.session.commit()

    flash(_("Flight %(dep)s→%(arr)s on %(date)s saved.", dep=dep, arr=arr, date=flight_date), "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))


# ── Delete flight ─────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/<int:flight_id>/delete",
                  methods=["POST"])
@login_required
@require_role(*_PILOT_ROLES)
def delete_flight(aircraft_id, flight_id):
    ac = _get_aircraft_or_404(aircraft_id)
    fe = _get_flight_or_404(ac, flight_id)
    label = f"{fe.departure_icao}→{fe.arrival_icao} on {fe.date}"
    _delete_upload(fe.flight_counter_photo)
    _delete_upload(fe.engine_counter_photo)
    db.session.delete(fe)
    db.session.commit()
    flash(_("Flight %(label)s deleted.", label=label), "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))
