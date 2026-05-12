from datetime import date as _date, timedelta

from flask import (  # pyright: ignore[reportMissingImports]
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

from models import Aircraft, MaintenanceRecord, MaintenanceTrigger, Role, Snag, TenantUser, TriggerType, db  # pyright: ignore[reportMissingImports]
from utils import compute_aircraft_statuses, login_required, require_role  # pyright: ignore[reportMissingImports]

maintenance_bp = Blueprint("maintenance", __name__)

_MAINT_ROLES = (Role.ADMIN, Role.OWNER, Role.MAINTENANCE)


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


def _get_trigger_or_404(aircraft: Aircraft, trigger_id: int) -> MaintenanceTrigger:
    t = db.session.get(MaintenanceTrigger, trigger_id)
    if not t or t.aircraft_id != aircraft.id:
        abort(404)
    return t


# ── Fleet maintenance overview ────────────────────────────────────────────────

@maintenance_bp.route("/maintenance")
@login_required
def fleet_overview():
    aircraft = (
        Aircraft.query
        .filter_by(tenant_id=_tenant_id())
        .order_by(Aircraft.registration)
        .all()
    )
    aircraft_ids = [ac.id for ac in aircraft]
    ac_by_id = {ac.id: ac for ac in aircraft}
    hobbs_by_id = {ac.id: ac.total_engine_hours for ac in aircraft}

    triggers = (
        MaintenanceTrigger.query
        .filter(MaintenanceTrigger.aircraft_id.in_(aircraft_ids))
        .all()
    ) if aircraft_ids else []

    from datetime import date as _date_cls, datetime as _datetime

    # Annotate each trigger with its status
    trigger_rows = [
        (t, t.status(hobbs_by_id.get(t.aircraft_id)), ac_by_id[t.aircraft_id])
        for t in triggers
    ]

    # Sort: overdue → due_soon → ok; within status: calendar triggers by due_date asc,
    # hours-based triggers (no reliable date) after calendar ones.
    _status_order = {"overdue": 0, "due_soon": 1, "ok": 2}
    _far_future = _date_cls(9999, 12, 31)

    def _trigger_sort_key(row):
        t, status, ac = row
        due = t.due_date if t.trigger_type == TriggerType.CALENDAR and t.due_date else _far_future
        return (_status_order[status], due)

    trigger_rows.sort(key=_trigger_sort_key)

    # Open grounding snags — oldest reported first (most overdue on top)
    grounding_snags = (
        Snag.query
        .filter(
            Snag.aircraft_id.in_(aircraft_ids),
            Snag.is_grounding.is_(True),
            Snag.resolved_at.is_(None),
        )
        .order_by(Snag.reported_at.asc())
        .all()
    ) if aircraft_ids else []
    grounding_snag_rows = [(s, ac_by_id[s.aircraft_id]) for s in grounding_snags]

    # Open non-grounding snags — oldest reported first
    open_snags = (
        Snag.query
        .filter(
            Snag.aircraft_id.in_(aircraft_ids),
            Snag.is_grounding.is_(False),
            Snag.resolved_at.is_(None),
        )
        .order_by(Snag.reported_at.asc())
        .all()
    ) if aircraft_ids else []
    open_snag_rows = [(s, ac_by_id[s.aircraft_id]) for s in open_snags]

    aircraft_status = compute_aircraft_statuses(aircraft, triggers, hobbs_by_id)

    # Chronological view: single list sorted by due/reported date asc.
    # Hours-based triggers have no reliable date → sorted after all dated items.
    # Tuple structure: (sort_date, kind_order, label, obj, ac, extra)
    # kind_order: grounding=0, snag=1, maintenance=2 (tiebreak within same date)
    _far_dt = _datetime(_far_future.year, _far_future.month, _far_future.day)
    chron_items = []
    for s, ac in grounding_snag_rows:
        dt = _datetime.combine(s.reported_at.date() if hasattr(s.reported_at, 'date') else s.reported_at, _datetime.min.time())
        chron_items.append(("grounding", dt, s, ac, None))
    for s, ac in open_snag_rows:
        dt = _datetime.combine(s.reported_at.date() if hasattr(s.reported_at, 'date') else s.reported_at, _datetime.min.time())
        chron_items.append(("snag", dt, s, ac, None))
    for t, status, ac in trigger_rows:
        if status in ("overdue", "due_soon"):
            if t.trigger_type == TriggerType.CALENDAR and t.due_date:
                dt = _datetime(t.due_date.year, t.due_date.month, t.due_date.day)
            else:
                dt = _far_dt  # hours-based: push to end
            chron_items.append(("maintenance", dt, t, ac, status))

    _kind_order = {"grounding": 0, "snag": 1, "maintenance": 2}
    chron_items.sort(key=lambda x: (x[1], _kind_order[x[0]]))

    view = request.args.get("view", "by-type")

    return render_template(
        "maintenance/fleet.html",
        aircraft=aircraft,
        aircraft_status=aircraft_status,
        trigger_rows=trigger_rows,
        grounding_snag_rows=grounding_snag_rows,
        open_snag_rows=open_snag_rows,
        chron_items=chron_items,
        hobbs_by_id=hobbs_by_id,
        view=view,
    )


# ── Trigger list ──────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance")
@login_required
def list_triggers(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    current_hobbs = ac.total_engine_hours
    triggers = (
        MaintenanceTrigger.query
        .filter_by(aircraft_id=ac.id)
        .order_by(MaintenanceTrigger.name)
        .all()
    )
    trigger_rows = [(t, t.status(current_hobbs)) for t in triggers]
    return render_template("maintenance/list.html", aircraft=ac,
                           trigger_rows=trigger_rows, current_hobbs=current_hobbs)


# ── Add trigger ───────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/new",
                      methods=["GET", "POST"])
@login_required
@require_role(*_MAINT_ROLES)
def new_trigger(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_trigger(ac, None)
    return render_template("maintenance/trigger_form.html", aircraft=ac,
                           trigger=None, trigger_types=TriggerType)


# ── Edit trigger ──────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/<int:trigger_id>/edit",
                      methods=["GET", "POST"])
@login_required
@require_role(*_MAINT_ROLES)
def edit_trigger(aircraft_id, trigger_id):
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)
    if request.method == "POST":
        return _save_trigger(ac, t)
    return render_template("maintenance/trigger_form.html", aircraft=ac,
                           trigger=t, trigger_types=TriggerType)


def _save_trigger(ac: Aircraft, t: MaintenanceTrigger | None):
    name = request.form.get("name", "").strip()
    trigger_type = request.form.get("trigger_type", "").strip()
    due_date_raw = request.form.get("due_date", "").strip()
    interval_days_raw = request.form.get("interval_days", "").strip()
    due_engine_hours_raw = request.form.get("due_engine_hours", "").strip()
    interval_hours_raw = request.form.get("interval_hours", "").strip()
    notes = request.form.get("notes", "").strip() or None

    errors = []
    if not name:
        errors.append(_("Name is required."))
    if trigger_type not in TriggerType.ALL:
        errors.append(_("Trigger type must be 'calendar' or 'hours'."))

    due_date = interval_days = due_engine_hours = interval_hours = None

    if trigger_type == TriggerType.CALENDAR:
        if not due_date_raw:
            errors.append(_("Due date is required for calendar triggers."))
        else:
            try:
                due_date = _date.fromisoformat(due_date_raw)
            except ValueError:
                errors.append(_("Due date must be a valid date (YYYY-MM-DD)."))
        if interval_days_raw:
            try:
                interval_days = int(interval_days_raw)
                if interval_days <= 0:
                    raise ValueError
            except ValueError:
                errors.append(_("Interval (days) must be a positive integer."))

    elif trigger_type == TriggerType.HOURS:
        if not due_engine_hours_raw:
            errors.append(_("Due engine hours is required for hours triggers."))
        else:
            try:
                due_engine_hours = float(due_engine_hours_raw)
                if due_engine_hours < 0:
                    raise ValueError
            except ValueError:
                errors.append(_("Due engine hours must be a positive number."))
        if interval_hours_raw:
            try:
                interval_hours = float(interval_hours_raw)
                if interval_hours <= 0:
                    raise ValueError
            except ValueError:
                errors.append(_("Interval (hours) must be a positive number."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("maintenance/trigger_form.html", aircraft=ac,
                               trigger=t, trigger_types=TriggerType)

    if t is None:
        t = MaintenanceTrigger(aircraft_id=ac.id)
        db.session.add(t)

    t.name = name
    t.trigger_type = trigger_type
    t.due_date = due_date
    t.interval_days = interval_days
    t.due_engine_hours = due_engine_hours
    t.interval_hours = interval_hours
    t.notes = notes
    db.session.commit()

    flash(_("Maintenance item '%(name)s' saved.", name=t.name), "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Delete trigger ────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/<int:trigger_id>/delete",
                      methods=["POST"])
@login_required
@require_role(*_MAINT_ROLES)
def delete_trigger(aircraft_id, trigger_id):
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)
    name = t.name
    db.session.delete(t)
    db.session.commit()
    flash(_("'%(name)s' deleted.", name=name), "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Mark as serviced ──────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/<int:trigger_id>/service",
                      methods=["GET", "POST"])
@login_required
@require_role(*_MAINT_ROLES)
def service_trigger(aircraft_id, trigger_id):
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)

    if request.method == "POST":
        performed_raw = request.form.get("performed_at", "").strip()
        hobbs_raw = request.form.get("hobbs_at_service", "").strip()
        notes = request.form.get("notes", "").strip() or None

        errors = []
        performed_at = None
        if not performed_raw:
            errors.append(_("Service date is required."))
        else:
            try:
                performed_at = _date.fromisoformat(performed_raw)
            except ValueError:
                errors.append(_("Service date must be a valid date (YYYY-MM-DD)."))

        hobbs_at_service = None
        if t.trigger_type == TriggerType.HOURS:
            if not hobbs_raw:
                errors.append(_("Hobbs at service is required for hours-based triggers."))
            else:
                try:
                    hobbs_at_service = float(hobbs_raw)
                    if hobbs_at_service < 0:
                        raise ValueError
                except ValueError:
                    errors.append(_("Hobbs at service must be a positive number."))
        elif hobbs_raw:
            try:
                hobbs_at_service = float(hobbs_raw)
            except ValueError:
                pass

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("maintenance/service_form.html", aircraft=ac,
                                   trigger=t, current_hobbs=ac.total_engine_hours,
                                   today=_date.today().isoformat())

        record = MaintenanceRecord(
            trigger_id=t.id,
            performed_at=performed_at,
            hobbs_at_service=hobbs_at_service,
            notes=notes,
        )
        db.session.add(record)

        # Advance the trigger's due value if an interval is configured
        if t.trigger_type == TriggerType.CALENDAR and t.interval_days and performed_at:
            t.due_date = performed_at + timedelta(days=t.interval_days)
        elif t.trigger_type == TriggerType.HOURS and t.interval_hours and hobbs_at_service is not None:
            t.due_engine_hours = hobbs_at_service + float(t.interval_hours)

        db.session.commit()
        flash(_("'%(name)s' marked as serviced.", name=t.name), "success")
        return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))

    return render_template("maintenance/service_form.html", aircraft=ac,
                           trigger=t, current_hobbs=ac.total_engine_hours,
                           today=_date.today().isoformat())
