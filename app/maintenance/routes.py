from datetime import date as _date, timedelta

from typing import Any

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
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from maintenance.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_service_fields,
    parse_trigger_fields,
)
from models import (
    Aircraft,
    MaintenanceRecord,
    MaintenanceTrigger,
    Role,
    Snag,
    TenantUser,
    TriggerType,
    db,
)  # pyright: ignore[reportMissingImports]
from services.authorization import AuthorizationService  # pyright: ignore[reportMissingImports]
from utils import (
    accessible_aircraft,
    activity,
    compute_aircraft_statuses,
    login_required,
    require_maint_access,
    require_role,
    user_can_access_aircraft,
)  # pyright: ignore[reportMissingImports]

maintenance_bp = Blueprint("maintenance", __name__)

_MAINT_ROLES = (Role.ADMIN, Role.OWNER, Role.MAINTENANCE)


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
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
@require_maint_access
def fleet_overview() -> ResponseReturnValue:
    aircraft = accessible_aircraft(_tenant_id()).all()
    aircraft_ids = [ac.id for ac in aircraft]
    ac_by_id = {ac.id: ac for ac in aircraft}
    hobbs_by_id = Aircraft.engine_hours_by_id(aircraft_ids)

    triggers = (
        (
            MaintenanceTrigger.query.filter(
                MaintenanceTrigger.aircraft_id.in_(aircraft_ids)
            ).all()
        )
        if aircraft_ids
        else []
    )

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

    def _trigger_sort_key(row: Any) -> Any:
        t, status, ac = row
        due = (
            t.due_date
            if t.trigger_type == TriggerType.CALENDAR and t.due_date
            else _far_future
        )
        return (_status_order[status], due)

    trigger_rows.sort(key=_trigger_sort_key)

    # Open grounding snags — oldest reported first (most overdue on top)
    grounding_snags = (
        (
            Snag.query.filter(
                Snag.aircraft_id.in_(aircraft_ids),
                Snag.is_grounding.is_(True),
                Snag.resolved_at.is_(None),
            )
            .order_by(Snag.reported_at.asc())
            .all()
        )
        if aircraft_ids
        else []
    )
    grounding_snag_rows = [(s, ac_by_id[s.aircraft_id]) for s in grounding_snags]

    # Open non-grounding snags — oldest reported first
    open_snags = (
        (
            Snag.query.filter(
                Snag.aircraft_id.in_(aircraft_ids),
                Snag.is_grounding.is_(False),
                Snag.resolved_at.is_(None),
            )
            .order_by(Snag.reported_at.asc())
            .all()
        )
        if aircraft_ids
        else []
    )
    open_snag_rows = [(s, ac_by_id[s.aircraft_id]) for s in open_snags]

    aircraft_status = compute_aircraft_statuses(aircraft, triggers, hobbs_by_id)

    # Chronological view: single list sorted by due/reported date asc.
    # Hours-based triggers have no reliable date → sorted after all dated items.
    # Tuple structure: (sort_date, kind_order, label, obj, ac, extra)
    # kind_order: grounding=0, snag=1, maintenance=2 (tiebreak within same date)
    _far_dt = _datetime(_far_future.year, _far_future.month, _far_future.day)
    chron_items = []
    for s, ac in grounding_snag_rows:
        dt = _datetime.combine(
            s.reported_at.date() if hasattr(s.reported_at, "date") else s.reported_at,
            _datetime.min.time(),
        )
        chron_items.append(("grounding", dt, s, ac, None))
    for s, ac in open_snag_rows:
        dt = _datetime.combine(
            s.reported_at.date() if hasattr(s.reported_at, "date") else s.reported_at,
            _datetime.min.time(),
        )
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

    # Component TBO / calendar life limits that need attention
    from services.component_limits import aircraft_limit_infos  # pyright: ignore[reportMissingImports]

    component_limit_rows = []
    for ac in aircraft:
        for info in aircraft_limit_infos(ac):
            if info["status"] in ("overdue", "due_soon"):
                component_limit_rows.append((info, ac))
    component_limit_rows.sort(key=lambda row: 0 if row[0]["status"] == "overdue" else 1)

    return render_template(
        "maintenance/fleet.html",
        aircraft=aircraft,
        aircraft_status=aircraft_status,
        component_limit_rows=component_limit_rows,
        trigger_rows=trigger_rows,
        grounding_snag_rows=grounding_snag_rows,
        open_snag_rows=open_snag_rows,
        chron_items=chron_items,
        hobbs_by_id=hobbs_by_id,
        view=view,
    )


# ── Trigger list ──────────────────────────────────────────────────────────────


@maintenance_bp.route("/aircraft/<aircraft_ref:aircraft_id>/maintenance")
@login_required
def list_triggers(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    current_hobbs = ac.total_engine_hours
    all_triggers = (
        MaintenanceTrigger.query.filter_by(aircraft_id=ac.id)
        .order_by(MaintenanceTrigger.name)
        .all()
    )
    tid = _tenant_id()
    uid = session["user_id"]
    maint_view = AuthorizationService.maintenance_view_level(uid, aircraft_id, tid)
    # Limited view: show only overdue and due-soon items
    if maint_view == "limited":
        triggers = [
            t
            for t in all_triggers
            if t.status(current_hobbs) in ("overdue", "due_soon")
        ]
    else:
        triggers = all_triggers
    trigger_rows = [(t, t.status(current_hobbs)) for t in triggers]
    return render_template(
        "maintenance/list.html",
        aircraft=ac,
        trigger_rows=trigger_rows,
        current_hobbs=current_hobbs,
        maint_view=maint_view,
    )


# ── Add trigger ───────────────────────────────────────────────────────────────


@maintenance_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/maintenance/new", methods=["GET", "POST"]
)
@login_required
@require_role(*_MAINT_ROLES)
def new_trigger(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_trigger(ac, None)
    return render_template(
        "maintenance/trigger_form.html",
        aircraft=ac,
        trigger=None,
        trigger_types=TriggerType,
    )


# ── Edit trigger ──────────────────────────────────────────────────────────────


@maintenance_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/maintenance/<int:trigger_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_MAINT_ROLES)
def edit_trigger(aircraft_id: int, trigger_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)
    if request.method == "POST":
        return _save_trigger(ac, t)
    return render_template(
        "maintenance/trigger_form.html",
        aircraft=ac,
        trigger=t,
        trigger_types=TriggerType,
    )


def _save_trigger(ac: Aircraft, t: MaintenanceTrigger | None) -> ResponseReturnValue:
    values, errors = parse_trigger_fields(request.form)

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "maintenance/trigger_form.html",
            aircraft=ac,
            trigger=t,
            trigger_types=TriggerType,
        )

    if t is None:
        t = MaintenanceTrigger(aircraft_id=ac.id)
        db.session.add(t)

    t.name = values["name"]
    t.trigger_type = values["trigger_type"]
    t.due_date = values["due_date"]
    t.interval_days = values["interval_days"]
    t.due_engine_hours = values["due_engine_hours"]
    t.interval_hours = values["interval_hours"]
    t.notes = values["notes"]
    db.session.commit()

    flash(_("Maintenance item '%(name)s' saved.", name=t.name), "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Delete trigger ────────────────────────────────────────────────────────────


@maintenance_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/maintenance/<int:trigger_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_MAINT_ROLES)
def delete_trigger(aircraft_id: int, trigger_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)
    name = t.name
    db.session.delete(t)
    db.session.commit()
    flash(_("'%(name)s' deleted.", name=name), "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


# ── Mark as serviced ──────────────────────────────────────────────────────────


@maintenance_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/maintenance/<int:trigger_id>/service",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_MAINT_ROLES)
def service_trigger(aircraft_id: int, trigger_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    t = _get_trigger_or_404(ac, trigger_id)

    if request.method == "POST":
        values, errors = parse_service_fields(request.form, t.trigger_type)
        performed_at = values["performed_at"]
        hobbs_at_service = values["hobbs_at_service"]

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "maintenance/service_form.html",
                aircraft=ac,
                trigger=t,
                current_hobbs=ac.total_engine_hours,
                today=_date.today().isoformat(),
            )

        record = MaintenanceRecord(
            trigger_id=t.id,
            performed_at=performed_at,
            hobbs_at_service=hobbs_at_service,
            notes=values["notes"],
        )
        db.session.add(record)

        # Advance the trigger's due value if an interval is configured
        if t.trigger_type == TriggerType.CALENDAR and t.interval_days and performed_at:
            t.due_date = performed_at + timedelta(days=t.interval_days)
        elif (
            t.trigger_type == TriggerType.HOURS
            and t.interval_hours
            and hobbs_at_service is not None
        ):
            t.due_engine_hours = hobbs_at_service + float(t.interval_hours)

        db.session.commit()
        activity(
            "maintenance.serviced",
            trigger_id=t.id,
            aircraft_id=aircraft_id,
            trigger_name=t.name,
            record_id=record.id,
        )
        flash(_("'%(name)s' marked as serviced.", name=t.name), "success")
        return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))

    return render_template(
        "maintenance/service_form.html",
        aircraft=ac,
        trigger=t,
        current_hobbs=ac.total_engine_hours,
        today=_date.today().isoformat(),
    )
