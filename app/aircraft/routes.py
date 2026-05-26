from typing import Any

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]

from flask_babel import gettext as _, ngettext  # pyright: ignore[reportMissingImports]
from werkzeug.utils import secure_filename  # pyright: ignore[reportMissingImports]

import json
import os
import uuid as _uuid_mod

from models import (
    Aircraft,
    AircraftGpsImportBatch,
    AppSetting,
    Component,
    ComponentType,
    DocType,
    Document,
    Expense,
    ExpenseType,
    FlightEntry,
    FUEL_DENSITY,
    GAL_TO_L,
    MaintenanceTrigger,
    PilotLogbookEntry,
    Reservation,
    ReservationStatus,
    Role,
    Snag,
    TenantUser,
    WeightBalanceConfig,
    WeightBalanceEntry,
    WeightBalanceStation,
    db,
)  # pyright: ignore[reportMissingImports]
from aircraft.gps_import import (  # pyright: ignore[reportMissingImports]
    detect_segments,
    merge_and_sort,
    parse_gps_file,
    round_flight_time,
)
from utils import (
    accessible_aircraft,
    compute_aircraft_statuses,
    login_required,
    require_role,
    user_can_access_aircraft,
)  # pyright: ignore[reportMissingImports]

aircraft_bp = Blueprint("aircraft", __name__, url_prefix="/aircraft")

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)
_PILOT_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT)


def _tenant_id() -> int:
    """Return the tenant ID for the currently logged-in user."""
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    """Fetch an aircraft that belongs to the current tenant and is accessible to the user."""
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
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
def list_aircraft() -> ResponseReturnValue:
    aircraft = accessible_aircraft(_tenant_id()).all()
    aircraft_ids = [ac.id for ac in aircraft]
    hobbs_by_id = {ac.id: ac.total_engine_hours for ac in aircraft}
    triggers = (
        (
            MaintenanceTrigger.query.filter(
                MaintenanceTrigger.aircraft_id.in_(aircraft_ids)
            ).all()
        )
        if aircraft_ids
        else []
    )
    aircraft_status = compute_aircraft_statuses(aircraft, triggers, hobbs_by_id)
    wb_configured_ids = {ac.id for ac in aircraft if ac.wb_config is not None}
    return render_template(
        "aircraft/list.html",
        aircraft=aircraft,
        aircraft_status=aircraft_status,
        wb_configured_ids=wb_configured_ids,
    )


# ── Add aircraft ──────────────────────────────────────────────────────────────


@aircraft_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def new_aircraft() -> ResponseReturnValue:
    if request.method == "POST":
        return _save_aircraft(None)
    return render_template("aircraft/aircraft_form.html", aircraft=None)


# ── Aircraft detail ───────────────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>")
@login_required
def detail(aircraft_id: int) -> ResponseReturnValue:
    from models import FlightEntry, MaintenanceTrigger

    ac = _get_aircraft_or_404(aircraft_id)
    components_by_type: dict[Any, list[Any]] = {}
    for comp in sorted(ac.components, key=lambda c: (c.type, c.position or "")):
        components_by_type.setdefault(comp.type, []).append(comp)
    recent_flights = (
        FlightEntry.query.filter_by(aircraft_id=ac.id)
        .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
        .limit(3)
        .all()
    )
    current_hobbs = ac.total_engine_hours
    triggers = MaintenanceTrigger.query.filter_by(aircraft_id=ac.id).all()
    maintenance_summary = [(t, t.status(current_hobbs)) for t in triggers]
    recent_expenses = (
        Expense.query.filter_by(aircraft_id=ac.id)
        .order_by(Expense.date.desc(), Expense.id.desc())
        .limit(3)
        .all()
    )
    recent_documents = (
        Document.query.filter_by(aircraft_id=ac.id, is_sensitive=False)
        .order_by(Document.uploaded_at.desc())
        .limit(3)
        .all()
    )
    document_count = Document.query.filter_by(aircraft_id=ac.id).count()
    active_insurance_cert = (
        Document.query.filter_by(
            aircraft_id=ac.id,
            doc_type=DocType.INSURANCE_CERT,
        )
        .filter(Document.superseded_by_id.is_(None))
        .first()
    )
    open_snags = (
        Snag.query.filter_by(aircraft_id=ac.id, resolved_at=None)
        .order_by(Snag.is_grounding.desc(), Snag.reported_at.desc())
        .all()
    )
    wb_cfg = ac.wb_config
    last_wb_entry = None
    if wb_cfg:
        last_wb_entry = (
            WeightBalanceEntry.query.filter_by(config_id=wb_cfg.id)
            .order_by(WeightBalanceEntry.date.desc(), WeightBalanceEntry.id.desc())
            .first()
        )
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc)
    upcoming_reservations = (
        Reservation.query.filter(
            Reservation.aircraft_id == ac.id,
            Reservation.status.in_(
                [ReservationStatus.CONFIRMED, ReservationStatus.PENDING]
            ),
            Reservation.end_dt >= now,
        )
        .order_by(Reservation.start_dt)
        .limit(5)
        .all()
    )
    return render_template(
        "aircraft/detail.html",
        aircraft=ac,
        components_by_type=components_by_type,
        component_types=ComponentType,
        recent_flights=recent_flights,
        maintenance_summary=maintenance_summary,
        recent_expenses=recent_expenses,
        expense_type_labels=ExpenseType.LABELS,
        recent_documents=recent_documents,
        document_count=document_count,
        active_insurance_cert=active_insurance_cert,
        open_snags=open_snags,
        wb_config=wb_cfg,
        last_wb_entry=last_wb_entry,
        upcoming_reservations=upcoming_reservations,
        ReservationStatus=ReservationStatus,
    )


# ── Edit aircraft ─────────────────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def edit_aircraft(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_aircraft(ac)
    return render_template("aircraft/aircraft_form.html", aircraft=ac)


def _save_aircraft(ac: Aircraft | None) -> ResponseReturnValue:
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
    insurance_expiry_raw = request.form.get("insurance_expiry", "").strip()
    logbook_time_precision = request.form.get(
        "logbook_time_precision", "tenth_hour"
    ).strip()
    if logbook_time_precision not in ("tenth_hour", "minute"):
        logbook_time_precision = "tenth_hour"

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

    insurance_expiry = None
    if insurance_expiry_raw:
        from datetime import date as _date

        try:
            insurance_expiry = _date.fromisoformat(insurance_expiry_raw)
        except ValueError:
            errors.append(_("Insurance expiry must be a valid date (YYYY-MM-DD)."))

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
    ac.insurance_expiry = insurance_expiry
    ac.logbook_time_precision = logbook_time_precision
    db.session.commit()

    flash(_("%(reg)s saved.", reg=ac.registration), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Delete aircraft ───────────────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/delete", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def delete_aircraft(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    reg = ac.registration
    db.session.delete(ac)
    db.session.commit()
    flash(_("%(reg)s and all its components have been deleted.", reg=reg), "success")
    return redirect(url_for("aircraft.list_aircraft"))


# ── Add component ─────────────────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/components/new", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def new_component(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_component(ac, None)
    return render_template(
        "aircraft/component_form.html",
        aircraft=ac,
        component=None,
        component_types=ComponentType,
    )


# ── Edit component ────────────────────────────────────────────────────────────


@aircraft_bp.route(
    "/<int:aircraft_id>/components/<int:component_id>/edit", methods=["GET", "POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def edit_component(aircraft_id: int, component_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    comp = _get_component_or_404(ac, component_id)
    if request.method == "POST":
        return _save_component(ac, comp)
    return render_template(
        "aircraft/component_form.html",
        aircraft=ac,
        component=comp,
        component_types=ComponentType,
    )


def _save_component(ac: Aircraft, comp: Component | None) -> ResponseReturnValue:
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

    def _parse_date(raw: str, label: str) -> Any:
        if not raw:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError:
            errors.append(
                _("%(label)s must be a valid date (YYYY-MM-DD).", label=label)
            )
            return None

    installed_at = _parse_date(installed_raw, "Install date")
    removed_at = _parse_date(removed_raw, "Removal date")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "aircraft/component_form.html",
            aircraft=ac,
            component=comp,
            component_types=ComponentType,
        )

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


@aircraft_bp.route(
    "/<int:aircraft_id>/components/<int:component_id>/delete", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_component(aircraft_id: int, component_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    comp = _get_component_or_404(ac, component_id)
    label = f"{comp.make} {comp.model}"
    db.session.delete(comp)
    db.session.commit()
    flash(_("%(label)s removed.", label=label), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Mass & Balance: helpers ───────────────────────────────────────────────────


def _point_in_polygon(cg: float, weight: float, points: Any) -> bool:
    """Ray-casting point-in-polygon test. points: list of [arm, weight] pairs."""
    n = len(points)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(points[i][0]), float(points[i][1])
        xj, yj = float(points[j][0]), float(points[j][1])
        if ((yi > weight) != (yj > weight)) and (
            cg < (xj - xi) * (weight - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


# ── Mass & Balance: config ────────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/wb/config", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def wb_config(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    cfg: WeightBalanceConfig | None = ac.wb_config  # type: ignore[assignment]

    if request.method == "POST":
        errors = []

        def _f(name: str) -> float | None:
            try:
                v = float(request.form.get(name, "").strip())
                if v < 0:
                    raise ValueError
                return v
            except ValueError:
                errors.append(_("%(field)s must be a positive number.", field=name))
                return None

        empty_weight = _f("empty_weight")
        empty_cg_arm = _f("empty_cg_arm")
        max_takeoff_weight = _f("max_takeoff_weight")
        forward_cg_limit = _f("forward_cg_limit")
        aft_cg_limit = _f("aft_cg_limit")
        datum_note = request.form.get("datum_note", "").strip() or None

        fuel_unit = request.form.get("fuel_unit", "L").strip()
        if fuel_unit not in ("L", "gal"):
            fuel_unit = "L"

        # Stations: label[], arm[], station_limit[] (capacity for fuel, max_weight for non-fuel), is_fuel[]
        labels = request.form.getlist("station_label[]")
        arms = request.form.getlist("station_arm[]")
        limits = request.form.getlist("station_limit[]")
        is_fuels = request.form.getlist(
            "station_is_fuel[]"
        )  # index values of checked boxes

        if not labels or all(lbl.strip() == "" for lbl in labels):
            errors.append(_("At least one loading station is required."))

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("aircraft/wb_config.html", aircraft=ac, config=cfg)

        if cfg is None:
            cfg = WeightBalanceConfig(aircraft_id=ac.id)
            db.session.add(cfg)

        # Optional envelope polygon: env_arm[], env_weight[]
        env_arms = request.form.getlist("env_arm[]")
        env_weights = request.form.getlist("env_weight[]")
        envelope_points = []
        for arm_s, w_s in zip(env_arms, env_weights):
            try:
                a = float(arm_s.strip())
                w = float(w_s.strip())
                if a >= 0 and w >= 0:
                    envelope_points.append([round(a, 4), round(w, 2)])
            except (ValueError, AttributeError):
                continue

        cfg.empty_weight = empty_weight
        cfg.empty_cg_arm = empty_cg_arm
        cfg.max_takeoff_weight = max_takeoff_weight
        cfg.forward_cg_limit = forward_cg_limit
        cfg.aft_cg_limit = aft_cg_limit
        cfg.fuel_unit = fuel_unit
        cfg.datum_note = datum_note
        cfg.envelope_points = envelope_points if len(envelope_points) >= 3 else None

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
                limit_val = None
            is_fuel = str(i) in is_fuels
            db.session.add(
                WeightBalanceStation(
                    config_id=cfg.id,
                    label=label,
                    arm=arm,
                    max_weight=None if is_fuel else limit_val,
                    capacity=limit_val if is_fuel else None,
                    is_fuel=is_fuel,
                    position=i,
                )
            )

        db.session.commit()
        flash(_("W&B configuration saved."), "success")
        return redirect(url_for("aircraft.detail", aircraft_id=ac.id))

    return render_template("aircraft/wb_config.html", aircraft=ac, config=cfg)


# ── Mass & Balance: entry list ────────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/wb/")
@login_required
def wb_list(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if not ac.wb_config:
        flash(_("Configure W&B envelope first."), "warning")
        return redirect(url_for("aircraft.wb_config", aircraft_id=ac.id))
    entries = (
        WeightBalanceEntry.query.filter_by(config_id=ac.wb_config.id)
        .order_by(WeightBalanceEntry.date.desc(), WeightBalanceEntry.id.desc())
        .all()
    )
    return render_template(
        "aircraft/wb_list.html", aircraft=ac, config=ac.wb_config, entries=entries
    )


# ── Mass & Balance: new / edit entry ─────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/wb/new", methods=["GET", "POST"])
@aircraft_bp.route("/<int:aircraft_id>/wb/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(*_PILOT_ROLES)
def wb_entry(aircraft_id: int, entry_id: int | None = None) -> ResponseReturnValue:
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
        label = request.form.get("label", "").strip() or None
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
                        errors.append(
                            _(
                                "Volume for %(station)s exceeds tank capacity.",
                                station=st.label,
                            )
                        )
                    station_weights[str(st.id)] = vol
                except ValueError:
                    errors.append(
                        _(
                            "Volume for %(station)s must be a non-negative number.",
                            station=st.label,
                        )
                    )
            else:
                raw = request.form.get(f"weight_{st.id}", "").strip()
                try:
                    w = float(raw) if raw else 0.0
                    if w < 0:
                        raise ValueError
                    station_weights[str(st.id)] = w
                except ValueError:
                    errors.append(
                        _(
                            "Weight for %(station)s must be a non-negative number.",
                            station=st.label,
                        )
                    )

        # CG computation — fuel stations: convert volume → kg
        empty_w = float(cfg.empty_weight)
        empty_arm = float(cfg.empty_cg_arm)
        total_moment = empty_w * empty_arm
        total_weight = empty_w
        fuel_density = FUEL_DENSITY.get(ac.fuel_type, 0.72)
        gal_factor = GAL_TO_L if cfg.fuel_unit == "gal" else 1.0
        for st in cfg.stations:
            val = station_weights.get(str(st.id), 0.0)
            w_kg = val * fuel_density * gal_factor if st.is_fuel else val
            total_weight += w_kg
            total_moment += w_kg * float(st.arm)
        loaded_cg = total_moment / total_weight if total_weight else 0.0

        if cfg.envelope_points and len(cfg.envelope_points) >= 3:
            in_env = _point_in_polygon(loaded_cg, total_weight, cfg.envelope_points)
        else:
            mtow = float(cfg.max_takeoff_weight)
            fwd = float(cfg.forward_cg_limit)
            aft = float(cfg.aft_cg_limit)
            in_env = total_weight <= mtow and fwd <= loaded_cg <= aft

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "aircraft/wb_entry.html",
                aircraft=ac,
                config=cfg,
                entry=entry,
                fuel_density=FUEL_DENSITY,
            )

        if entry is None:
            entry = WeightBalanceEntry(config_id=cfg.id)
            db.session.add(entry)

        entry.date = entry_date
        entry.label = label
        entry.total_weight = round(total_weight, 2)
        entry.loaded_cg = round(loaded_cg, 2)
        entry.is_in_envelope = in_env
        entry.station_weights = station_weights
        db.session.commit()
        flash(_("W&B calculation saved."), "success")
        return redirect(url_for("aircraft.wb_list", aircraft_id=ac.id))

    return render_template(
        "aircraft/wb_entry.html",
        aircraft=ac,
        config=cfg,
        entry=entry,
        fuel_density=FUEL_DENSITY,
    )


# ── Mass & Balance: delete entry ──────────────────────────────────────────────


@aircraft_bp.route("/<int:aircraft_id>/wb/<int:entry_id>/delete", methods=["POST"])
@login_required
@require_role(*_PILOT_ROLES)
def wb_entry_delete(aircraft_id: int, entry_id: int) -> ResponseReturnValue:
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


# ── Phase 30: GPS Log Import ──────────────────────────────────────────────────

_GPS_ALLOWED_EXTS = {".gpx", ".kml", ".csv"}
_GPS_MAX_BYTES = 20 * 1024 * 1024  # 20 MB per file


def _gps_tmp_dir() -> str:
    """Return (and create if needed) the tmp directory for GPS uploads."""
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "/tmp")
    d = os.path.join(upload_folder, "gps_import_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _segment_to_dict(seg: Any, idx: int) -> dict[str, Any]:
    """Serialise a FlightSegment for template rendering (includes track_geojson)."""
    return {
        "idx": idx,
        "block_off_utc": seg.block_off_utc.isoformat(),
        "block_on_utc": seg.block_on_utc.isoformat(),
        "takeoff_utc": seg.takeoff_utc.isoformat() if seg.takeoff_utc else None,
        "landing_utc": seg.landing_utc.isoformat() if seg.landing_utc else None,
        "departure_icao": seg.departure_icao or "",
        "arrival_icao": seg.arrival_icao or "",
        "flight_time_raw_h": seg.flight_time_raw_h,
        "flight_time_rounded_h": seg.flight_time_rounded_h,
        "landing_count": seg.landing_count,
        "is_ground_only": seg.is_ground_only,
        "track_geojson": seg.track_geojson,
    }


def _load_segment_geojson(seg: dict[str, Any]) -> Any:
    """Read the GeoJSON dict back from the tmp file written by _segment_for_session."""
    path = seg.get("geojson_path")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _segment_for_session(seg_dict: dict[str, Any], tmp_dir: str) -> dict[str, Any]:
    """Return a copy of seg_dict safe for cookie-session storage.

    track_geojson can be hundreds of KB — too large for Flask's 4 KB cookie
    limit.  We spill it to a tmp file and store the path instead.
    """
    s = {k: v for k, v in seg_dict.items() if k != "track_geojson"}
    geojson = seg_dict.get("track_geojson")
    if geojson is not None:
        fname = f"seg_{seg_dict['idx']}_{_uuid_mod.uuid4().hex}.geojson"
        path = os.path.join(tmp_dir, fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(geojson, fh)
        s["geojson_path"] = path
    return s


@aircraft_bp.route("/<int:aircraft_id>/gps-import", methods=["GET", "POST"])
@login_required
@require_role(*_PILOT_ROLES)
def gps_import_upload(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    if request.method == "GET":
        return render_template("aircraft/gps_import_upload.html", aircraft=ac)

    files = request.files.getlist("gps_files")
    if not files or all(f.filename == "" for f in files):
        flash(_("Please select at least one GPS log file."), "warning")
        return render_template("aircraft/gps_import_upload.html", aircraft=ac)

    tmp_dir = _gps_tmp_dir()
    parsed_meta: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped_empty = 0
    formats: list[str] = []

    for f in files:
        if not f.filename:
            continue
        ext = os.path.splitext(f.filename.lower())[1]
        if ext not in _GPS_ALLOWED_EXTS:
            errors.append(
                _(
                    "%(fn)s: unsupported file type (use .gpx, .kml, or .csv).",
                    fn=f.filename,
                )
            )
            continue

        data = f.read(_GPS_MAX_BYTES + 1)
        if len(data) > _GPS_MAX_BYTES:
            errors.append(_("%(fn)s: file too large (20 MB limit).", fn=f.filename))
            continue

        try:
            parsed = parse_gps_file(data, f.filename)
        except ValueError as exc:
            errors.append(_("%(fn)s: %(err)s", fn=f.filename, err=str(exc)))
            continue

        if parsed.classification == "empty":
            skipped_empty += 1
            continue

        # Save raw bytes to tmp
        uid = _uuid_mod.uuid4().hex
        safe_name = f"{uid}_{secure_filename(f.filename)}"
        tmp_path = os.path.join(tmp_dir, safe_name)
        with open(tmp_path, "wb") as fh:
            fh.write(data)

        parsed_meta.append(
            {
                "tmp_path": tmp_path,
                "original_filename": f.filename,
                "format": parsed.format,
                "classification": parsed.classification,
                "trkpt_count": len(parsed.trackpoints),
                "hint_dep": parsed.hint_departure_icao,
                "hint_arr": parsed.hint_arrival_icao,
            }
        )
        formats.append(parsed.format)

    if errors:
        for e in errors:
            flash(e, "danger")
    if skipped_empty:
        flash(
            ngettext(
                "%(n)s file skipped — no movement detected.",
                "%(n)s files skipped — no movement detected.",
                skipped_empty,
                n=skipped_empty,
            ),
            "info",
        )

    if not parsed_meta:
        flash(_("No valid GPS files to import."), "warning")
        return render_template("aircraft/gps_import_upload.html", aircraft=ac)

    session["gps_import"] = {
        "user_id": session["user_id"],
        "aircraft_id": aircraft_id,
        "files": parsed_meta,
        "skipped_empty": skipped_empty,
    }
    return redirect(url_for("aircraft.gps_import_review", aircraft_id=aircraft_id))


@aircraft_bp.route("/<int:aircraft_id>/gps-import/review", methods=["GET"])
@login_required
@require_role(*_PILOT_ROLES)
def gps_import_review(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    state = session.get("gps_import")
    if not state or state.get("aircraft_id") != aircraft_id:
        flash(_("Session expired — please upload your GPS files again."), "warning")
        return redirect(url_for("aircraft.gps_import_upload", aircraft_id=aircraft_id))

    file_metas = state["files"]

    # Re-parse each tmp file and build combined trackpoint list
    from aircraft.gps_import import ParsedGpsFile  # pyright: ignore[reportMissingImports]

    all_parsed: list[ParsedGpsFile] = []
    for meta in file_metas:
        try:
            with open(meta["tmp_path"], "rb") as fh:
                data = fh.read()
            parsed = parse_gps_file(data, meta["original_filename"])
            parsed.hint_departure_icao = meta.get("hint_dep")
            parsed.hint_arrival_icao = meta.get("hint_arr")
            all_parsed.append(parsed)
        except (OSError, ValueError):
            flash(
                _(
                    "Could not read %(fn)s — please upload again.",
                    fn=meta["original_filename"],
                ),
                "warning",
            )
            return redirect(
                url_for("aircraft.gps_import_upload", aircraft_id=aircraft_id)
            )

    merged = merge_and_sort(all_parsed)

    # Collect ICAO hints from all files
    hint_dep = next(
        (p.hint_departure_icao for p in all_parsed if p.hint_departure_icao), None
    )
    hint_arr = next(
        (p.hint_arrival_icao for p in all_parsed if p.hint_arrival_icao), None
    )

    segments = detect_segments(
        merged,
        aircraft_precision=ac.logbook_time_precision,
        hint_dep=hint_dep,
        hint_arr=hint_arr,
    )

    # Build full dicts (with track_geojson) for template rendering.
    # Spill GeoJSON to tmp files for the session — track_geojson can be
    # hundreds of KB and silently overflows Flask's 4 KB cookie session.
    full_segs = [_segment_to_dict(seg, i) for i, seg in enumerate(segments)]

    # Duplicate detection: find existing FlightEntry records that overlap each segment.
    from datetime import datetime as _dt  # noqa: PLC0415

    for seg in full_segs:
        block_off = _dt.fromisoformat(seg["block_off_utc"])
        block_on = _dt.fromisoformat(seg["block_on_utc"])
        matched = (
            FlightEntry.query.filter(
                FlightEntry.aircraft_id == aircraft_id,
                FlightEntry.block_off_utc.isnot(None),
                FlightEntry.block_on_utc.isnot(None),
                FlightEntry.block_off_utc < block_on,
                FlightEntry.block_on_utc > block_off,
            )
            .first()
        )
        if matched:
            seg["matched_flight_id"] = matched.id
            seg["matched_flight_str"] = (
                f"#{matched.id} — {matched.date} "
                f"{matched.departure_icao} → {matched.arrival_icao}"
            )
        else:
            seg["matched_flight_id"] = None
            seg["matched_flight_str"] = None

    tmp_dir = _gps_tmp_dir()
    session["gps_import"]["segments"] = [
        _segment_for_session(s, tmp_dir) for s in full_segs
    ]
    session.modified = True

    # Get OpenAIP API key for map tiles
    tile_setting = db.session.get(AppSetting, "openaip_api_key")
    openaip_key = tile_setting.value if tile_setting and tile_setting.value else None

    return render_template(
        "aircraft/gps_import_review.html",
        aircraft=ac,
        segments=full_segs,
        skipped_empty=state.get("skipped_empty", 0),
        openaip_key=openaip_key,
    )


@aircraft_bp.route("/<int:aircraft_id>/gps-import/confirm", methods=["POST"])
@login_required
@require_role(*_PILOT_ROLES)
def gps_import_confirm(aircraft_id: int) -> ResponseReturnValue:
    from datetime import datetime as _dt  # noqa: PLC0415
    import decimal  # noqa: PLC0415

    ac = _get_aircraft_or_404(aircraft_id)
    state = session.get("gps_import")
    if not state or state.get("aircraft_id") != aircraft_id:
        flash(_("Session expired — please upload your GPS files again."), "warning")
        return redirect(url_for("aircraft.gps_import_upload", aircraft_id=aircraft_id))

    segments_data: list[dict[str, Any]] = state.get("segments", [])
    if not segments_data:
        flash(_("No segments to import."), "warning")
        return redirect(url_for("aircraft.gps_import_upload", aircraft_id=aircraft_id))

    # pilot_role: 'pic' | 'dual' | 'none'
    pilot_role = request.form.get("pilot_role", "none")
    if pilot_role not in ("pic", "dual", "none"):
        pilot_role = "none"
    create_pilot_entries = pilot_role in ("pic", "dual")
    file_metas = state["files"]

    # Determine format label
    formats = {m["format"] for m in file_metas}
    format_label = formats.pop() if len(formats) == 1 else "mixed"

    # Create the batch record
    batch = AircraftGpsImportBatch(
        aircraft_id=aircraft_id,
        pilot_user_id=int(session["user_id"]) if create_pilot_entries else None,
        source_filenames=[m["original_filename"] for m in file_metas],
        format_detected=format_label,
        segments_found=len(segments_data),
        linked_flight_entry_ids=[],
        pilot_role=pilot_role,
    )
    db.session.add(batch)
    db.session.flush()  # get batch.id

    imported = 0
    linked_ids: list[int] = []
    for i, seg in enumerate(segments_data):
        # Check whether this segment was kept (form checkbox per segment)
        if not request.form.get(f"keep_segment_{i}"):
            continue

        # Override ICAOs from review form
        dep_icao = (
            (request.form.get(f"dep_icao_{i}") or seg["departure_icao"] or "")
            .strip()
            .upper()[:4]
        )
        arr_icao = (
            (request.form.get(f"arr_icao_{i}") or seg["arrival_icao"] or "")
            .strip()
            .upper()[:4]
        )
        if not dep_icao:
            dep_icao = "????"
        if not arr_icao:
            arr_icao = "????"

        block_off = _dt.fromisoformat(seg["block_off_utc"])
        block_on = _dt.fromisoformat(seg["block_on_utc"])

        # departure_time / arrival_time as time objects (UTC, stored naive)
        dep_time = block_off.time().replace(tzinfo=None)
        arr_time = block_on.time().replace(tzinfo=None)

        flight_time_h = round_flight_time(
            seg["flight_time_raw_h"], ac.logbook_time_precision
        )

        matched_id = seg.get("matched_flight_id")
        if matched_id:
            # Link GPS track to pre-existing flight — preserve all existing fields.
            entry = db.session.get(FlightEntry, matched_id)
            if entry and entry.aircraft_id == aircraft_id:
                entry.block_off_utc = block_off
                entry.block_on_utc = block_on
                entry.track_geojson = _load_segment_geojson(seg)
                linked_ids.append(entry.id)
                db.session.flush()
            else:
                matched_id = None  # fall through to create

        if not matched_id:
            entry = FlightEntry(
                aircraft_id=aircraft_id,
                date=block_off.date(),
                departure_icao=dep_icao,
                arrival_icao=arr_icao,
                departure_time=dep_time,
                arrival_time=arr_time,
                flight_time=decimal.Decimal(str(flight_time_h)),
                landing_count=seg.get("landing_count") or 0,
                source="gps_import",
                gps_import_batch_id=batch.id,
                block_off_utc=block_off,
                block_on_utc=block_on,
                track_geojson=_load_segment_geojson(seg),
            )
            db.session.add(entry)
            db.session.flush()

        if create_pilot_entries:
            # Aircraft model has no category field yet; default to SEP
            ac_category = getattr(ac, "category", "SEP")
            single_pilot_se = (
                decimal.Decimal(str(flight_time_h))
                if ac_category in ("SEP", "SET", "")
                else None
            )
            single_pilot_me = (
                decimal.Decimal(str(flight_time_h))
                if ac_category in ("MEP", "MET")
                else None
            )

            pentry = PilotLogbookEntry(
                pilot_user_id=int(session["user_id"]),
                flight_id=entry.id,
                date=block_off.date(),
                aircraft_type=f"{ac.make} {ac.model}".strip(),
                aircraft_registration=ac.registration,
                departure_place=dep_icao,
                departure_time=dep_time,
                arrival_place=arr_icao,
                arrival_time=arr_time,
                single_pilot_se=single_pilot_se,
                single_pilot_me=single_pilot_me,
                function_pic=(
                    decimal.Decimal(str(flight_time_h)) if pilot_role == "pic" else None
                ),
                function_dual=(
                    decimal.Decimal(str(flight_time_h)) if pilot_role == "dual" else None
                ),
                landings_day=seg.get("landing_count") or 0,
                source="gps_import",
                gps_batch_id=batch.id,
            )
            db.session.add(pentry)

        imported += 1

    batch.segments_imported = imported
    batch.linked_flight_entry_ids = linked_ids
    db.session.commit()

    # Clean up tmp files (uploaded GPS files and spilled GeoJSON files)
    for meta in file_metas:
        try:
            os.unlink(meta["tmp_path"])
        except OSError as exc:
            current_app.logger.debug("cleanup GPS tmp file: %s", exc)
    for seg in segments_data:
        gj_path = seg.get("geojson_path")
        if gj_path:
            try:
                os.unlink(gj_path)
            except OSError as exc:
                current_app.logger.debug("cleanup GPS geojson tmp: %s", exc)
    session.pop("gps_import", None)

    flash(
        ngettext(
            "%(n)s flight imported successfully.",
            "%(n)s flights imported successfully.",
            imported,
            n=imported,
        ),
        "success",
    )
    return redirect(url_for("aircraft.gps_import_history", aircraft_id=aircraft_id))


@aircraft_bp.route("/<int:aircraft_id>/gps-import/history", methods=["GET"])
@login_required
@require_role(*_PILOT_ROLES)
def gps_import_history(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    batches = (
        AircraftGpsImportBatch.query.filter_by(aircraft_id=aircraft_id)
        .order_by(AircraftGpsImportBatch.imported_at.desc())
        .all()
    )
    return render_template(
        "aircraft/gps_import_history.html", aircraft=ac, batches=batches
    )


@aircraft_bp.route(
    "/<int:aircraft_id>/gps-import/<int:batch_id>/rollback", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def gps_import_rollback(aircraft_id: int, batch_id: int) -> ResponseReturnValue:
    _get_aircraft_or_404(aircraft_id)
    batch = db.session.get(AircraftGpsImportBatch, batch_id)
    if not batch or batch.aircraft_id != aircraft_id:
        abort(404)

    # Delete pilot logbook entries created by this batch.
    PilotLogbookEntry.query.filter_by(gps_batch_id=batch.id).delete(
        synchronize_session="fetch"
    )

    # Flights created by this batch — delete them entirely.
    FlightEntry.query.filter_by(gps_import_batch_id=batch.id).delete(
        synchronize_session="fetch"
    )

    # Flights that were pre-existing but got a GPS track linked — unlink only.
    linked_ids = batch.linked_flight_entry_ids or []
    if linked_ids:
        FlightEntry.query.filter(FlightEntry.id.in_(linked_ids)).update(
            {
                "track_geojson": None,
                "block_off_utc": None,
                "block_on_utc": None,
            },
            synchronize_session="fetch",
        )

    db.session.delete(batch)
    db.session.commit()
    flash(
        _("GPS import batch rolled back and all linked flight entries removed."),
        "success",
    )
    return redirect(url_for("aircraft.gps_import_history", aircraft_id=aircraft_id))


@aircraft_bp.route("/<int:aircraft_id>/flights/<int:flight_id>", methods=["GET"])
@login_required
@require_role(*_PILOT_ROLES)
def flight_detail(aircraft_id: int, flight_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    entry = db.session.get(FlightEntry, flight_id)
    if not entry or entry.aircraft_id != aircraft_id:
        abort(404)

    tile_setting = db.session.get(AppSetting, "openaip_api_key")
    openaip_key = tile_setting.value if tile_setting and tile_setting.value else None

    return render_template(
        "aircraft/flight_detail.html",
        aircraft=ac,
        entry=entry,
        openaip_key=openaip_key,
    )


@aircraft_bp.route("/<int:aircraft_id>/tracks", methods=["GET"])
@login_required
@require_role(*_PILOT_ROLES)
def flight_tracks(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    entries_with_tracks = (
        FlightEntry.query.filter_by(aircraft_id=aircraft_id)
        .filter(FlightEntry.track_geojson.isnot(None))
        .order_by(FlightEntry.date.desc())
        .all()
    )

    tile_setting = db.session.get(AppSetting, "openaip_api_key")
    openaip_key = tile_setting.value if tile_setting and tile_setting.value else None

    return render_template(
        "aircraft/flight_tracks.html",
        aircraft=ac,
        entries=entries_with_tracks,
        openaip_key=openaip_key,
    )
