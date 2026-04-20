import os
import uuid
from datetime import date as _date

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

from models import Aircraft, Component, Expense, ExpenseType, FlightEntry, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import login_required  # pyright: ignore[reportMissingImports]

flights_bp = Blueprint("flights", __name__)

_ALLOWED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
_FUEL_UNITS = ["L", "gal"]
_FUEL_CURRENCIES = ["EUR", "USD", "GBP", "CHF"]


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
        cumulative += float(f.hobbs_end) - float(f.hobbs_start)
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
def new_flight(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_flight(ac, None)
    suggested_hobbs = ac.total_hobbs
    return render_template("flights/flight_form.html", aircraft=ac,
                           flight=None, suggested_hobbs=suggested_hobbs,
                           flight_fuel=None,
                           fuel_units=_FUEL_UNITS, fuel_currencies=_FUEL_CURRENCIES)


# ── Edit flight ───────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/<int:flight_id>/edit",
                  methods=["GET", "POST"])
@login_required
def edit_flight(aircraft_id, flight_id):
    ac = _get_aircraft_or_404(aircraft_id)
    fe = _get_flight_or_404(ac, flight_id)
    if request.method == "POST":
        return _save_flight(ac, fe)
    flight_fuel = next(
        (e for e in fe.expenses if e.expense_type == ExpenseType.FUEL), None
    )
    return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                           suggested_hobbs=None, flight_fuel=flight_fuel,
                           fuel_units=_FUEL_UNITS, fuel_currencies=_FUEL_CURRENCIES)


def _save_flight(ac: Aircraft, fe: FlightEntry | None):
    date_raw = request.form.get("date", "").strip()
    dep = request.form.get("departure_icao", "").strip().upper()
    arr = request.form.get("arrival_icao", "").strip().upper()
    hobbs_start_raw = request.form.get("hobbs_start", "").strip()
    hobbs_end_raw = request.form.get("hobbs_end", "").strip()
    pilot = request.form.get("pilot", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    tach_start_raw = request.form.get("tach_start", "").strip()
    tach_end_raw = request.form.get("tach_end", "").strip()
    fuel_cost_raw = request.form.get("fuel_cost", "").strip()
    fuel_quantity_raw = request.form.get("fuel_quantity", "").strip()
    fuel_unit = request.form.get("fuel_unit", "L").strip()
    fuel_currency = request.form.get("fuel_currency", "EUR").strip()

    errors = []

    flight_date = None
    if not date_raw:
        errors.append("Date is required.")
    else:
        try:
            flight_date = _date.fromisoformat(date_raw)
        except ValueError:
            errors.append("Date must be a valid date (YYYY-MM-DD).")

    if not dep:
        errors.append("Departure airfield is required.")
    if not arr:
        errors.append("Arrival airfield is required.")

    hobbs_start = hobbs_end = None
    try:
        hobbs_start = float(hobbs_start_raw)
        if hobbs_start < 0:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("Hobbs start must be a positive number.")

    try:
        hobbs_end = float(hobbs_end_raw)
        if hobbs_end < 0:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("Hobbs end must be a positive number.")

    if hobbs_start is not None and hobbs_end is not None and hobbs_end <= hobbs_start:
        errors.append("Hobbs end must be greater than hobbs start.")

    tach_start = tach_end = None
    if tach_start_raw:
        try:
            tach_start = float(tach_start_raw)
            if tach_start < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Tach start must be a positive number.")

    if tach_end_raw:
        try:
            tach_end = float(tach_end_raw)
            if tach_end < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Tach end must be a positive number.")

    if tach_start is not None and tach_end is not None and tach_end <= tach_start:
        errors.append("Tach end must be greater than tach start.")

    fuel_cost = None
    if fuel_cost_raw:
        try:
            fuel_cost = float(fuel_cost_raw)
            if fuel_cost < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Fuel cost must be a non-negative number.")

    fuel_quantity = None
    if fuel_quantity_raw:
        try:
            fuel_quantity = float(fuel_quantity_raw)
            if fuel_quantity < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Fuel quantity must be a non-negative number.")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                               suggested_hobbs=None, flight_fuel=None,
                               fuel_units=_FUEL_UNITS, fuel_currencies=_FUEL_CURRENCIES)

    if fe is None:
        fe = FlightEntry(aircraft_id=ac.id)
        db.session.add(fe)

    fe.date = flight_date
    fe.departure_icao = dep
    fe.arrival_icao = arr
    fe.hobbs_start = hobbs_start
    fe.hobbs_end = hobbs_end
    fe.pilot = pilot
    fe.notes = notes
    fe.tach_start = tach_start
    fe.tach_end = tach_end

    db.session.flush()

    hobbs_file = request.files.get("hobbs_photo")
    if hobbs_file and hobbs_file.filename:
        stored = _save_upload(hobbs_file, fe.id, "hobbs")
        if stored:
            _delete_upload(fe.hobbs_photo)
            fe.hobbs_photo = stored

    tach_file = request.files.get("tach_photo")
    if tach_file and tach_file.filename:
        stored = _save_upload(tach_file, fe.id, "tach")
        if stored:
            _delete_upload(fe.tach_photo)
            fe.tach_photo = stored

    # Create/update/delete linked fuel expense
    existing_fuel = next(
        (e for e in fe.expenses if e.expense_type == ExpenseType.FUEL), None
    )
    if fuel_cost is not None:
        if existing_fuel is None:
            existing_fuel = Expense(
                aircraft_id=ac.id,
                flight_entry_id=fe.id,
                expense_type=ExpenseType.FUEL,
            )
            db.session.add(existing_fuel)
        existing_fuel.date = fe.date
        existing_fuel.amount = fuel_cost
        existing_fuel.currency = fuel_currency
        existing_fuel.quantity = fuel_quantity
        existing_fuel.unit = fuel_unit if fuel_quantity else None
    elif existing_fuel is not None:
        db.session.delete(existing_fuel)

    db.session.commit()

    flash(f"Flight {dep}→{arr} on {flight_date} saved.", "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))


# ── Delete flight ─────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/<int:flight_id>/delete",
                  methods=["POST"])
@login_required
def delete_flight(aircraft_id, flight_id):
    ac = _get_aircraft_or_404(aircraft_id)
    fe = _get_flight_or_404(ac, flight_id)
    label = f"{fe.departure_icao}→{fe.arrival_icao} on {fe.date}"
    _delete_upload(fe.hobbs_photo)
    _delete_upload(fe.tach_photo)
    db.session.delete(fe)
    db.session.commit()
    flash(f"Flight {label} deleted.", "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))
