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

from models import Aircraft, Component, ComponentType, Document, Expense, ExpenseType, MaintenanceTrigger, Snag, TenantUser, db # pyright: ignore[reportMissingImports]
from utils import compute_aircraft_statuses, login_required # pyright: ignore[reportMissingImports]

aircraft_bp = Blueprint("aircraft", __name__, url_prefix="/aircraft")


def _tenant_id() -> int:
    """Return the tenant ID for the currently logged-in user."""
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return tu.tenant_id


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    """Fetch an aircraft that belongs to the current tenant, or 404."""
    ac = db.session.get(Aircraft, aircraft_id)
    if not ac or ac.tenant_id != _tenant_id():
        abort(404)
    return ac


def _get_component_or_404(aircraft: Aircraft, component_id: int) -> Component:
    comp = db.session.get(Component, component_id)
    if not comp or comp.aircraft_id != aircraft.id:
        abort(404)
    return comp


# ── Aircraft list ─────────────────────────────────────────────────────────────

@aircraft_bp.route("/")
@login_required
def list_aircraft():
    aircraft = Aircraft.query.filter_by(tenant_id=_tenant_id()).order_by(Aircraft.registration).all()
    aircraft_ids = [ac.id for ac in aircraft]
    hobbs_by_id = {ac.id: ac.total_engine_hours for ac in aircraft}
    triggers = (
        MaintenanceTrigger.query
        .filter(MaintenanceTrigger.aircraft_id.in_(aircraft_ids))
        .all()
    ) if aircraft_ids else []
    aircraft_status = compute_aircraft_statuses(aircraft, triggers, hobbs_by_id)
    return render_template("aircraft/list.html", aircraft=aircraft,
                           aircraft_status=aircraft_status)


# ── Add aircraft ──────────────────────────────────────────────────────────────

@aircraft_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_aircraft():
    if request.method == "POST":
        return _save_aircraft(None)
    return render_template("aircraft/aircraft_form.html", aircraft=None)


# ── Aircraft detail ───────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>")
@login_required
def detail(aircraft_id):
    from models import FlightEntry, MaintenanceTrigger
    ac = _get_aircraft_or_404(aircraft_id)
    components_by_type = {}
    for comp in sorted(ac.components, key=lambda c: (c.type, c.position or "")):
        components_by_type.setdefault(comp.type, []).append(comp)
    recent_flights = (
        FlightEntry.query
        .filter_by(aircraft_id=ac.id)
        .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
        .limit(3)
        .all()
    )
    current_hobbs = ac.total_engine_hours
    triggers = MaintenanceTrigger.query.filter_by(aircraft_id=ac.id).all()
    maintenance_summary = [(t, t.status(current_hobbs)) for t in triggers]
    recent_expenses = (
        Expense.query
        .filter_by(aircraft_id=ac.id)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .limit(3)
        .all()
    )
    recent_documents = (
        Document.query
        .filter_by(aircraft_id=ac.id, is_sensitive=False)
        .order_by(Document.uploaded_at.desc())
        .limit(3)
        .all()
    )
    document_count = Document.query.filter_by(aircraft_id=ac.id).count()
    open_snags = (
        Snag.query
        .filter_by(aircraft_id=ac.id, resolved_at=None)
        .order_by(Snag.is_grounding.desc(), Snag.reported_at.desc())
        .all()
    )
    return render_template("aircraft/detail.html", aircraft=ac,
                           components_by_type=components_by_type,
                           component_types=ComponentType,
                           recent_flights=recent_flights,
                           maintenance_summary=maintenance_summary,
                           recent_expenses=recent_expenses,
                           expense_type_labels=ExpenseType.LABELS,
                           recent_documents=recent_documents,
                           document_count=document_count,
                           open_snags=open_snags)


# ── Edit aircraft ─────────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/edit", methods=["GET", "POST"])
@login_required
def edit_aircraft(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_aircraft(ac)
    return render_template("aircraft/aircraft_form.html", aircraft=ac)


def _save_aircraft(ac: Aircraft | None):
    registration = request.form.get("registration", "").strip().upper()
    make = request.form.get("make", "").strip()
    model = request.form.get("model", "").strip()
    year_raw = request.form.get("year", "").strip()
    is_placeholder = bool(request.form.get("is_placeholder"))
    regime = request.form.get("regime", "EASA").strip()
    has_flight_counter = bool(request.form.get("has_flight_counter"))
    flight_counter_offset_raw = request.form.get("flight_counter_offset", "0.3").strip()
    fuel_flow_raw = request.form.get("fuel_flow", "").strip()

    errors = []
    if not registration:
        errors.append("Registration is required.")
    if not make:
        errors.append("Manufacturer is required.")
    if not model:
        errors.append("Model is required.")
    year = None
    if year_raw:
        try:
            year = int(year_raw)
            if not (1900 <= year <= 2100):
                raise ValueError
        except ValueError:
            errors.append("Year must be a valid 4-digit year.")

    flight_counter_offset = 0.3
    if flight_counter_offset_raw:
        try:
            flight_counter_offset = float(flight_counter_offset_raw)
            if flight_counter_offset < 0:
                raise ValueError
        except ValueError:
            errors.append("Flight counter offset must be a non-negative number.")

    fuel_flow = None
    if fuel_flow_raw:
        try:
            fuel_flow = float(fuel_flow_raw)
            if fuel_flow < 0:
                raise ValueError
        except ValueError:
            errors.append("Fuel consumption must be a non-negative number.")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("aircraft/aircraft_form.html", aircraft=ac)

    if ac is None:
        ac = Aircraft(tenant_id=_tenant_id())
        db.session.add(ac)

    ac.registration = registration
    ac.make = make
    ac.model = model
    ac.year = year
    ac.is_placeholder = is_placeholder
    ac.regime = regime
    ac.has_flight_counter = has_flight_counter
    ac.flight_counter_offset = flight_counter_offset
    ac.fuel_flow = fuel_flow
    db.session.commit()

    flash(f"{ac.registration} saved.", "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Delete aircraft ───────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/delete", methods=["POST"])
@login_required
def delete_aircraft(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    reg = ac.registration
    db.session.delete(ac)
    db.session.commit()
    flash(f"{reg} and all its components have been deleted.", "success")
    return redirect(url_for("aircraft.list_aircraft"))


# ── Add component ─────────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/components/new", methods=["GET", "POST"])
@login_required
def new_component(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_component(ac, None)
    return render_template("aircraft/component_form.html", aircraft=ac,
                           component=None, component_types=ComponentType)


# ── Edit component ────────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/components/<int:component_id>/edit",
                   methods=["GET", "POST"])
@login_required
def edit_component(aircraft_id, component_id):
    ac = _get_aircraft_or_404(aircraft_id)
    comp = _get_component_or_404(ac, component_id)
    if request.method == "POST":
        return _save_component(ac, comp)
    return render_template("aircraft/component_form.html", aircraft=ac,
                           component=comp, component_types=ComponentType)


def _save_component(ac: Aircraft, comp: Component | None):
    from datetime import date as _date

    type_ = request.form.get("type", "").strip()
    position = request.form.get("position", "").strip() or None
    make = request.form.get("make", "").strip()
    model = request.form.get("model", "").strip()
    serial = request.form.get("serial_number", "").strip() or None
    time_raw = request.form.get("time_at_install", "").strip()
    installed_raw = request.form.get("installed_at", "").strip()
    removed_raw = request.form.get("removed_at", "").strip()

    errors = []
    if not type_:
        errors.append("Component type is required.")
    if not make:
        errors.append("Manufacturer is required.")
    if not model:
        errors.append("Model is required.")

    time_at_install = None
    if time_raw:
        try:
            time_at_install = float(time_raw)
            if time_at_install < 0:
                raise ValueError
        except ValueError:
            errors.append("Time at install must be a positive number.")

    def _parse_date(raw, label):
        if not raw:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError:
            errors.append(f"{label} must be a valid date (YYYY-MM-DD).")
            return None

    installed_at = _parse_date(installed_raw, "Install date")
    removed_at = _parse_date(removed_raw, "Removal date")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("aircraft/component_form.html", aircraft=ac,
                               component=comp, component_types=ComponentType)

    if comp is None:
        comp = Component(aircraft_id=ac.id)
        db.session.add(comp)

    comp.type = type_
    comp.position = position
    comp.make = make
    comp.model = model
    comp.serial_number = serial
    comp.time_at_install = time_at_install
    comp.installed_at = installed_at
    comp.removed_at = removed_at
    db.session.commit()

    flash(f"{comp.make} {comp.model} saved.", "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Delete component ──────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/components/<int:component_id>/delete",
                   methods=["POST"])
@login_required
def delete_component(aircraft_id, component_id):
    ac = _get_aircraft_or_404(aircraft_id)
    comp = _get_component_or_404(ac, component_id)
    label = f"{comp.make} {comp.model}"
    db.session.delete(comp)
    db.session.commit()
    flash(f"{label} removed.", "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))
