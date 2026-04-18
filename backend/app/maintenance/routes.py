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

from models import Aircraft, MaintenanceRecord, MaintenanceTrigger, TenantUser, TriggerType, db  # pyright: ignore[reportMissingImports]
from utils import login_required  # pyright: ignore[reportMissingImports]

maintenance_bp = Blueprint("maintenance", __name__)


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


# ── Trigger list ──────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance")
@login_required
def list_triggers(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    current_hobbs = ac.total_hobbs
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
    due_hobbs_raw = request.form.get("due_hobbs", "").strip()
    interval_hours_raw = request.form.get("interval_hours", "").strip()
    notes = request.form.get("notes", "").strip() or None

    errors = []
    if not name:
        errors.append("Name is required.")
    if trigger_type not in TriggerType.ALL:
        errors.append("Trigger type must be 'calendar' or 'hours'.")

    due_date = interval_days = due_hobbs = interval_hours = None

    if trigger_type == TriggerType.CALENDAR:
        if not due_date_raw:
            errors.append("Due date is required for calendar triggers.")
        else:
            try:
                due_date = _date.fromisoformat(due_date_raw)
            except ValueError:
                errors.append("Due date must be a valid date (YYYY-MM-DD).")
        if interval_days_raw:
            try:
                interval_days = int(interval_days_raw)
                if interval_days <= 0:
                    raise ValueError
            except ValueError:
                errors.append("Interval (days) must be a positive integer.")

    elif trigger_type == TriggerType.HOURS:
        if not due_hobbs_raw:
            errors.append("Due hobbs is required for hours triggers.")
        else:
            try:
                due_hobbs = float(due_hobbs_raw)
                if due_hobbs < 0:
                    raise ValueError
            except ValueError:
                errors.append("Due hobbs must be a positive number.")
        if interval_hours_raw:
            try:
                interval_hours = float(interval_hours_raw)
                if interval_hours <= 0:
                    raise ValueError
            except ValueError:
                errors.append("Interval (hours) must be a positive number.")

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
    t.due_hobbs = due_hobbs
    t.interval_hours = interval_hours
    t.notes = notes
    db.session.commit()

    flash(f"Maintenance item '{t.name}' saved.", "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Delete trigger ────────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/<int:trigger_id>/delete",
                      methods=["POST"])
@login_required
def delete_trigger(aircraft_id, trigger_id):
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)
    name = t.name
    db.session.delete(t)
    db.session.commit()
    flash(f"'{name}' deleted.", "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Mark as serviced ──────────────────────────────────────────────────────────

@maintenance_bp.route("/aircraft/<int:aircraft_id>/maintenance/<int:trigger_id>/service",
                      methods=["GET", "POST"])
@login_required
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
            errors.append("Service date is required.")
        else:
            try:
                performed_at = _date.fromisoformat(performed_raw)
            except ValueError:
                errors.append("Service date must be a valid date (YYYY-MM-DD).")

        hobbs_at_service = None
        if t.trigger_type == TriggerType.HOURS:
            if not hobbs_raw:
                errors.append("Hobbs at service is required for hours-based triggers.")
            else:
                try:
                    hobbs_at_service = float(hobbs_raw)
                    if hobbs_at_service < 0:
                        raise ValueError
                except ValueError:
                    errors.append("Hobbs at service must be a positive number.")
        elif hobbs_raw:
            try:
                hobbs_at_service = float(hobbs_raw)
            except ValueError:
                pass

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template("maintenance/service_form.html", aircraft=ac,
                                   trigger=t, current_hobbs=ac.total_hobbs,
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
            t.due_hobbs = hobbs_at_service + float(t.interval_hours)

        db.session.commit()
        flash(f"'{t.name}' marked as serviced.", "success")
        return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))

    return render_template("maintenance/service_form.html", aircraft=ac,
                           trigger=t, current_hobbs=ac.total_hobbs,
                           today=_date.today().isoformat())
