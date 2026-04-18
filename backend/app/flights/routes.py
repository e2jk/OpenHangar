from datetime import date as _date

from flask import ( # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from models import Aircraft, FlightEntry, TenantUser, db # pyright: ignore[reportMissingImports]
from utils import login_required # pyright: ignore[reportMissingImports]

flights_bp = Blueprint("flights", __name__)


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


# ── Flight list ───────────────────────────────────────────────────────────────

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


# ── Log flight ────────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/new", methods=["GET", "POST"])
@login_required
def new_flight(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_flight(ac, None)
    # Pre-fill hobbs_start with the aircraft's current hobbs reading
    suggested_hobbs = ac.total_hobbs
    return render_template("flights/flight_form.html", aircraft=ac,
                           flight=None, suggested_hobbs=suggested_hobbs)


# ── Edit flight ───────────────────────────────────────────────────────────────

@flights_bp.route("/aircraft/<int:aircraft_id>/flights/<int:flight_id>/edit",
                  methods=["GET", "POST"])
@login_required
def edit_flight(aircraft_id, flight_id):
    ac = _get_aircraft_or_404(aircraft_id)
    fe = _get_flight_or_404(ac, flight_id)
    if request.method == "POST":
        return _save_flight(ac, fe)
    return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                           suggested_hobbs=None)


def _save_flight(ac: Aircraft, fe: FlightEntry | None):
    date_raw = request.form.get("date", "").strip()
    dep = request.form.get("departure_icao", "").strip().upper()
    arr = request.form.get("arrival_icao", "").strip().upper()
    hobbs_start_raw = request.form.get("hobbs_start", "").strip()
    hobbs_end_raw = request.form.get("hobbs_end", "").strip()

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

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("flights/flight_form.html", aircraft=ac, flight=fe,
                               suggested_hobbs=None)

    if fe is None:
        fe = FlightEntry(aircraft_id=ac.id)
        db.session.add(fe)

    fe.date = flight_date
    fe.departure_icao = dep
    fe.arrival_icao = arr
    fe.hobbs_start = hobbs_start
    fe.hobbs_end = hobbs_end
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
    db.session.delete(fe)
    db.session.commit()
    flash(f"Flight {label} deleted.", "success")
    return redirect(url_for("flights.list_flights", aircraft_id=ac.id))
