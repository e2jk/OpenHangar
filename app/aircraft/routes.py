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

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import Aircraft, Component, ComponentType, Document, Expense, ExpenseType, FlightEntry, FUEL_DENSITY, GAL_TO_L, MaintenanceTrigger, Snag, TenantUser, WeightBalanceConfig, WeightBalanceEntry, WeightBalanceStation, db # pyright: ignore[reportMissingImports]
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
    wb_configured_ids = {ac.id for ac in aircraft if ac.wb_config is not None}
    return render_template("aircraft/list.html", aircraft=aircraft,
                           aircraft_status=aircraft_status,
                           wb_configured_ids=wb_configured_ids)


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
    wb_cfg = ac.wb_config
    last_wb_entry = None
    if wb_cfg:
        last_wb_entry = (
            WeightBalanceEntry.query
            .filter_by(config_id=wb_cfg.id)
            .order_by(WeightBalanceEntry.date.desc(), WeightBalanceEntry.id.desc())
            .first()
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
                           open_snags=open_snags,
                           wb_config=wb_cfg,
                           last_wb_entry=last_wb_entry)


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
    fuel_type = request.form.get("fuel_type", "avgas").strip()
    if fuel_type not in ("avgas", "jet_a1"):
        fuel_type = "avgas"

    errors = []
    if not registration:
        errors.append(_("Registration is required."))
    if not make:
        errors.append(_("Manufacturer is required."))
    if not model:
        errors.append(_("Model is required."))
    year = None
    if year_raw:
        try:
            year = int(year_raw)
            if not (1900 <= year <= 2100):
                raise ValueError
        except ValueError:
            errors.append(_("Year must be a valid 4-digit year."))

    flight_counter_offset = 0.3
    if flight_counter_offset_raw:
        try:
            flight_counter_offset = float(flight_counter_offset_raw)
            if flight_counter_offset < 0:
                raise ValueError
        except ValueError:
            errors.append(_("Flight counter offset must be a non-negative number."))

    fuel_flow = None
    if fuel_flow_raw:
        try:
            fuel_flow = float(fuel_flow_raw)
            if fuel_flow < 0:
                raise ValueError
        except ValueError:
            errors.append(_("Fuel consumption must be a non-negative number."))

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
    ac.fuel_type = fuel_type
    db.session.commit()

    flash(_("%(reg)s saved.", reg=ac.registration), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Delete aircraft ───────────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/delete", methods=["POST"])
@login_required
def delete_aircraft(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    reg = ac.registration
    db.session.delete(ac)
    db.session.commit()
    flash(_("%(reg)s and all its components have been deleted.", reg=reg), "success")
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
        errors.append(_("Component type is required."))
    if not make:
        errors.append(_("Manufacturer is required."))
    if not model:
        errors.append(_("Model is required."))

    time_at_install = None
    if time_raw:
        try:
            time_at_install = float(time_raw)
            if time_at_install < 0:
                raise ValueError
        except ValueError:
            errors.append(_("Time at install must be a positive number."))

    def _parse_date(raw, label):
        if not raw:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError:
            errors.append(_("%(label)s must be a valid date (YYYY-MM-DD).", label=label))
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

    flash(_("%(make)s %(model)s saved.", make=comp.make, model=comp.model), "success")
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
    flash(_("%(label)s removed.", label=label), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Mass & Balance: config ────────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/wb/config", methods=["GET", "POST"])
@login_required
def wb_config(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    cfg = ac.wb_config

    if request.method == "POST":
        errors = []
        def _f(name):
            try:
                v = float(request.form.get(name, "").strip())
                if v < 0:
                    raise ValueError
                return v
            except ValueError:
                errors.append(_("%(field)s must be a positive number.", field=name))
                return None

        empty_weight       = _f("empty_weight")
        empty_cg_arm       = _f("empty_cg_arm")
        max_takeoff_weight = _f("max_takeoff_weight")
        forward_cg_limit   = _f("forward_cg_limit")
        aft_cg_limit       = _f("aft_cg_limit")
        datum_note         = request.form.get("datum_note", "").strip() or None

        fuel_unit = request.form.get("fuel_unit", "L").strip()
        if fuel_unit not in ("L", "gal"):
            fuel_unit = "L"

        # Stations: label[], arm[], station_limit[] (capacity for fuel, max_weight for non-fuel), is_fuel[]
        labels   = request.form.getlist("station_label[]")
        arms     = request.form.getlist("station_arm[]")
        limits   = request.form.getlist("station_limit[]")
        is_fuels = request.form.getlist("station_is_fuel[]")  # index values of checked boxes

        if not labels or all(l.strip() == "" for l in labels):
            errors.append(_("At least one loading station is required."))

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("aircraft/wb_config.html", aircraft=ac, config=cfg)

        if cfg is None:
            cfg = WeightBalanceConfig(aircraft_id=ac.id)
            db.session.add(cfg)

        cfg.empty_weight       = empty_weight
        cfg.empty_cg_arm       = empty_cg_arm
        cfg.max_takeoff_weight = max_takeoff_weight
        cfg.forward_cg_limit   = forward_cg_limit
        cfg.aft_cg_limit       = aft_cg_limit
        cfg.fuel_unit          = fuel_unit
        cfg.datum_note         = datum_note

        # Replace stations
        for s in list(cfg.stations):
            db.session.delete(s)
        db.session.flush()

        for i, label in enumerate(labels):
            label = label.strip()
            if not label:
                continue
            try:
                arm = float(arms[i])
            except (ValueError, IndexError):
                continue
            limit_val = None
            try:
                lim_raw = limits[i].strip()
                if lim_raw:
                    limit_val = float(lim_raw)
            except (ValueError, IndexError):
                pass
            is_fuel = str(i) in is_fuels
            db.session.add(WeightBalanceStation(
                config_id=cfg.id,
                label=label,
                arm=arm,
                max_weight=None if is_fuel else limit_val,
                capacity=limit_val if is_fuel else None,
                is_fuel=is_fuel,
                position=i,
            ))

        db.session.commit()
        flash(_("W&B configuration saved."), "success")
        return redirect(url_for("aircraft.detail", aircraft_id=ac.id))

    return render_template("aircraft/wb_config.html", aircraft=ac, config=cfg)


# ── Mass & Balance: entry list ────────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/wb/")
@login_required
def wb_list(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if not ac.wb_config:
        flash(_("Configure W&B envelope first."), "warning")
        return redirect(url_for("aircraft.wb_config", aircraft_id=ac.id))
    entries = (
        WeightBalanceEntry.query
        .filter_by(config_id=ac.wb_config.id)
        .order_by(WeightBalanceEntry.date.desc(), WeightBalanceEntry.id.desc())
        .all()
    )
    return render_template("aircraft/wb_list.html", aircraft=ac, config=ac.wb_config, entries=entries)


# ── Mass & Balance: new / edit entry ─────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/wb/new", methods=["GET", "POST"])
@aircraft_bp.route("/<int:aircraft_id>/wb/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def wb_entry(aircraft_id, entry_id=None):
    ac = _get_aircraft_or_404(aircraft_id)
    if not ac.wb_config:
        flash(_("Configure W&B envelope first."), "warning")
        return redirect(url_for("aircraft.wb_config", aircraft_id=ac.id))
    cfg = ac.wb_config

    entry = None
    if entry_id is not None:
        entry = db.session.get(WeightBalanceEntry, entry_id)
        if not entry or entry.config_id != cfg.id:
            abort(404)

    if request.method == "POST":
        from datetime import date as _date
        errors = []
        date_raw = request.form.get("date", "").strip()
        label    = request.form.get("label", "").strip() or None
        try:
            entry_date = _date.fromisoformat(date_raw)
        except ValueError:
            errors.append(_("A valid date is required."))
            entry_date = None

        # Per-station values: fuel stations store volume (L/gal), non-fuel store kg
        station_weights = {}
        for st in cfg.stations:
            if st.is_fuel:
                raw = request.form.get(f"volume_{st.id}", "").strip()
                try:
                    vol = float(raw) if raw else 0.0
                    if vol < 0:
                        raise ValueError
                    if st.capacity is not None and vol > float(st.capacity):
                        errors.append(_("Volume for %(station)s exceeds tank capacity.", station=st.label))
                    station_weights[str(st.id)] = vol
                except ValueError:
                    errors.append(_("Volume for %(station)s must be a non-negative number.", station=st.label))
            else:
                raw = request.form.get(f"weight_{st.id}", "").strip()
                try:
                    w = float(raw) if raw else 0.0
                    if w < 0:
                        raise ValueError
                    station_weights[str(st.id)] = w
                except ValueError:
                    errors.append(_("Weight for %(station)s must be a non-negative number.", station=st.label))

        # CG computation — fuel stations: convert volume → kg
        empty_w   = float(cfg.empty_weight)
        empty_arm = float(cfg.empty_cg_arm)
        total_moment = empty_w * empty_arm
        total_weight = empty_w
        fuel_density = FUEL_DENSITY.get(ac.fuel_type, 0.72)
        gal_factor   = GAL_TO_L if cfg.fuel_unit == "gal" else 1.0
        for st in cfg.stations:
            val = station_weights.get(str(st.id), 0.0)
            w_kg = val * fuel_density * gal_factor if st.is_fuel else val
            total_weight += w_kg
            total_moment += w_kg * float(st.arm)
        loaded_cg = total_moment / total_weight if total_weight else 0.0

        mtow   = float(cfg.max_takeoff_weight)
        fwd    = float(cfg.forward_cg_limit)
        aft    = float(cfg.aft_cg_limit)
        in_env = (total_weight <= mtow and fwd <= loaded_cg <= aft)

        # Optional flight link
        flight_entry_id = None
        fid_raw = request.form.get("flight_entry_id", "").strip()
        if fid_raw:
            try:
                fid = int(fid_raw)
                fe = db.session.get(FlightEntry, fid)
                if fe and fe.aircraft_id == ac.id:
                    flight_entry_id = fid
            except ValueError:
                pass

        if errors:
            for msg in errors:
                flash(msg, "danger")
            flights = FlightEntry.query.filter_by(aircraft_id=ac.id).order_by(FlightEntry.date.desc()).limit(50).all()
            return render_template("aircraft/wb_entry.html", aircraft=ac, config=cfg,
                                   entry=entry, flights=flights, fuel_density=FUEL_DENSITY)

        if entry is None:
            entry = WeightBalanceEntry(config_id=cfg.id)
            db.session.add(entry)

        entry.date            = entry_date
        entry.label           = label
        entry.total_weight    = round(total_weight, 2)
        entry.loaded_cg       = round(loaded_cg, 2)
        entry.is_in_envelope  = in_env
        entry.flight_entry_id = flight_entry_id
        entry.station_weights = station_weights
        db.session.commit()
        flash(_("W&B calculation saved."), "success")
        return redirect(url_for("aircraft.wb_list", aircraft_id=ac.id))

    flights = FlightEntry.query.filter_by(aircraft_id=ac.id).order_by(FlightEntry.date.desc()).limit(50).all()
    return render_template("aircraft/wb_entry.html", aircraft=ac, config=cfg,
                           entry=entry, flights=flights, fuel_density=FUEL_DENSITY)


# ── Mass & Balance: delete entry ──────────────────────────────────────────────

@aircraft_bp.route("/<int:aircraft_id>/wb/<int:entry_id>/delete", methods=["POST"])
@login_required
def wb_entry_delete(aircraft_id, entry_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if not ac.wb_config:
        abort(404)
    entry = db.session.get(WeightBalanceEntry, entry_id)
    if not entry or entry.config_id != ac.wb_config.id:
        abort(404)
    db.session.delete(entry)
    db.session.commit()
    flash(_("W&B calculation deleted."), "success")
    return redirect(url_for("aircraft.wb_list", aircraft_id=ac.id))
