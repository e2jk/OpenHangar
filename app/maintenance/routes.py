from datetime import date as _date
from datetime import timedelta
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
from models import (
    Aircraft,
    AmpBasis,
    AmpCertifyingPartyKind,
    AmpDeclaration,
    AmpDeclarationType,
    Component,
    HoursBasis,
    MaintenanceRecord,
    MaintenanceTrigger,
    Role,
    Snag,
    TenantUser,
    TriggerType,
    db,
)  # pyright: ignore[reportMissingImports]
from services.authorization import (
    AuthorizationService,  # pyright: ignore[reportMissingImports]
)
from utils import (
    accessible_aircraft,
    activity,
    compute_aircraft_statuses,
    login_required,
    require_maint_access,
    require_role,
    user_can_access_aircraft,
)  # pyright: ignore[reportMissingImports]

from maintenance.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_service_fields,
    parse_trigger_fields,
)

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
    landings_by_id = Aircraft.landings_by_id(aircraft_ids)
    flight_hours_by_id = Aircraft.flight_hours_by_id(aircraft_ids)

    triggers = (
        (
            MaintenanceTrigger.query.filter(
                MaintenanceTrigger.aircraft_id.in_(aircraft_ids)
            ).all()
        )
        if aircraft_ids
        else []
    )

    from datetime import date as _date_cls
    from datetime import datetime as _datetime

    # Annotate each trigger with its status
    trigger_rows = [
        (
            t,
            t.status(
                current_engine_hours=hobbs_by_id.get(t.aircraft_id),
                current_landings=landings_by_id.get(t.aircraft_id),
                current_flight_hours=flight_hours_by_id.get(t.aircraft_id),
            ),
            ac_by_id[t.aircraft_id],
        )
        for t in triggers
    ]

    # Sort: overdue → due_soon → ok; within status: calendar triggers by due_date asc,
    # hours-based triggers (no reliable date) after calendar ones.
    _status_order = {"overdue": 0, "due_soon": 1, "ok": 2}
    _far_future = _date_cls(9999, 12, 31)

    def _trigger_sort_key(row: Any) -> Any:
        t, status, _ac = row
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

    aircraft_status = compute_aircraft_statuses(
        aircraft, triggers, hobbs_by_id, landings_by_id, flight_hours_by_id
    )

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
        if status in ("overdue", "due_soon") or t.needs_review:
            if t.due_date:
                dt = _datetime(t.due_date.year, t.due_date.month, t.due_date.day)
            else:
                dt = _far_dt  # no calendar due date: push to end
            chron_items.append(("maintenance", dt, t, ac, status))

    _kind_order = {"grounding": 0, "snag": 1, "maintenance": 2}
    chron_items.sort(key=lambda x: (x[1], _kind_order[x[0]]))

    view = request.args.get("view", "by-type")

    # Component TBO / calendar life limits that need attention
    from services.component_limits import (
        aircraft_limit_infos,  # pyright: ignore[reportMissingImports]
    )

    component_limit_rows = []
    for ac in aircraft:
        for info in aircraft_limit_infos(ac):
            if info["status"] in ("overdue", "due_soon"):
                component_limit_rows.append((info, ac))
    component_limit_rows.sort(key=lambda row: 0 if row[0]["status"] == "overdue" else 1)

    any_needs_review = any(t.needs_review for t, _status, _ac in trigger_rows)

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
        landings_by_id=landings_by_id,
        flight_hours_by_id=flight_hours_by_id,
        any_needs_review=any_needs_review,
        view=view,
    )


# ── Trigger list ──────────────────────────────────────────────────────────────


def _group_trigger_rows_by_component(
    trigger_rows: list[tuple[MaintenanceTrigger, str]],
) -> list[tuple[Component | None, list[tuple[MaintenanceTrigger, str]]]]:
    """Group (trigger, status) rows by trigger.component — unscoped
    ("Airframe / general") rows first, then installed components in the
    same (type, position) order used for the components list on
    aircraft/detail.html. A component only gets a section if it actually
    has at least one trigger."""
    general: list[tuple[MaintenanceTrigger, str]] = []
    by_component: dict[int, tuple[Component, list[tuple[MaintenanceTrigger, str]]]] = {}
    for t, status in trigger_rows:
        if t.component is None:
            general.append((t, status))
        else:
            entry = by_component.setdefault(
                t.component.id,
                (t.component, []),  # type: ignore[arg-type]
            )
            entry[1].append((t, status))

    groups: list[tuple[Component | None, list[tuple[MaintenanceTrigger, str]]]] = []
    if general:
        groups.append((None, general))
    for comp, rows in sorted(
        by_component.values(), key=lambda cr: (cr[0].type, cr[0].position or "")
    ):
        groups.append((comp, rows))
    return groups


@maintenance_bp.route("/aircraft/<aircraft_ref:aircraft_id>/maintenance")
@login_required
def list_triggers(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    current_hobbs = ac.total_engine_hours
    current_landings = ac.total_landings
    current_flight_hours = ac.total_flight_hours
    all_triggers = (
        MaintenanceTrigger.query.filter_by(aircraft_id=ac.id)
        .order_by(MaintenanceTrigger.name)
        .all()
    )
    tid = _tenant_id()
    uid = session["user_id"]
    maint_view = AuthorizationService.maintenance_view_level(uid, aircraft_id, tid)

    def _status(t: MaintenanceTrigger) -> str:
        return t.status(
            current_engine_hours=current_hobbs,
            current_landings=current_landings,
            current_flight_hours=current_flight_hours,
        )

    # Limited view: show only overdue and due-soon items
    if maint_view == "limited":
        triggers = [t for t in all_triggers if _status(t) in ("overdue", "due_soon")]
    else:
        triggers = all_triggers
    trigger_rows = [(t, _status(t)) for t in triggers]
    component_groups = _group_trigger_rows_by_component(trigger_rows)
    return render_template(
        "maintenance/list.html",
        aircraft=ac,
        trigger_rows=trigger_rows,
        component_groups=component_groups,
        current_hobbs=current_hobbs,
        current_landings=current_landings,
        current_flight_hours=current_flight_hours,
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
        hours_basis=HoursBasis,
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
        hours_basis=HoursBasis,
    )


def _save_trigger(ac: Aircraft, t: MaintenanceTrigger | None) -> ResponseReturnValue:
    values, errors = parse_trigger_fields(request.form)

    component_id = values.get("component_id")
    if component_id is not None:
        comp = db.session.get(Component, component_id)
        if comp is None or comp.aircraft_id != ac.id:
            errors.append(_("Component selection is invalid."))
            component_id = None

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "maintenance/trigger_form.html",
            aircraft=ac,
            trigger=t,
            trigger_types=TriggerType,
            hours_basis=HoursBasis,
        )

    if t is None:
        t = MaintenanceTrigger(aircraft_id=ac.id)
        db.session.add(t)

    t.name = values["name"]
    t.trigger_type = values["trigger_type"]
    t.component_id = component_id
    t.due_date = values["due_date"]
    t.interval_days = values["interval_days"]
    t.warn_days = values["warn_days"]
    t.due_engine_hours = values["due_engine_hours"]
    t.interval_hours = values["interval_hours"]
    t.warn_hours = values["warn_hours"]
    t.hours_basis = values["hours_basis"]
    t.due_landings = values["due_landings"]
    t.interval_landings = values["interval_landings"]
    t.warn_landings = values["warn_landings"]
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

    # Phase 40: which readings are required depends on which due-field
    # groups are actually populated on this trigger, not on trigger_type — a
    # combined-interval trigger can have more than one group populated.
    requires_hobbs = t.due_engine_hours is not None
    requires_landings = t.due_landings is not None

    if request.method == "POST":
        values, errors = parse_service_fields(
            request.form, requires_hobbs, requires_landings
        )
        performed_at = values["performed_at"]
        hobbs_at_service = values["hobbs_at_service"]
        landings_at_service = values["landings_at_service"]

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "maintenance/service_form.html",
                aircraft=ac,
                trigger=t,
                current_hobbs=ac.total_engine_hours,
                current_landings=ac.total_landings,
                current_flight_hours=ac.total_flight_hours,
                today=_date.today().isoformat(),
            )

        record = MaintenanceRecord(
            trigger_id=t.id,
            performed_at=performed_at,
            hobbs_at_service=hobbs_at_service,
            landings_at_service=landings_at_service,
            notes=values["notes"],
        )
        db.session.add(record)

        # Advance every populated due-field group independently — a
        # combined-interval trigger (both calendar and hours set) advances
        # both together from the same service record.
        if t.due_date is not None and t.interval_days and performed_at:
            t.due_date = performed_at + timedelta(days=t.interval_days)
        if (
            t.due_engine_hours is not None
            and t.interval_hours
            and hobbs_at_service is not None
        ):
            t.due_engine_hours = hobbs_at_service + float(t.interval_hours)
        if (
            t.due_landings is not None
            and t.interval_landings
            and landings_at_service is not None
        ):
            t.due_landings = landings_at_service + t.interval_landings

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
        current_landings=ac.total_landings,
        current_flight_hours=ac.total_flight_hours,
        today=_date.today().isoformat(),
    )


# ── AMP declaration profile ─────────────────────────────────────────────────


@maintenance_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/amp/edit", methods=["GET", "POST"]
)
@login_required
@require_role(*_MAINT_ROLES)
def edit_amp_declaration(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    decl: AmpDeclaration | None = ac.amp_declaration  # type: ignore[assignment]
    if request.method == "POST":
        return _save_amp_declaration(ac, decl)
    return render_template(
        "maintenance/amp_declaration_form.html",
        aircraft=ac,
        decl=decl,
        amp_basis=AmpBasis,
        declaration_types=AmpDeclarationType,
        certifying_party_kinds=AmpCertifyingPartyKind,
    )


def _save_amp_declaration(
    ac: Aircraft, decl: AmpDeclaration | None
) -> ResponseReturnValue:
    def _text(key: str) -> str | None:
        v = (request.form.get(key) or "").strip()
        return v or None

    basis = request.form.get("basis", "").strip() or AmpBasis.DAH_ICA
    declaration_type = (
        request.form.get("declaration_type", "").strip() or AmpDeclarationType.OWNER
    )
    certifying_party_kind = (
        request.form.get("certifying_party_kind", "").strip()
        or AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR
    )

    errors = []
    if basis not in AmpBasis.ALL:
        errors.append(_("Invalid programme basis selected."))
    if declaration_type not in AmpDeclarationType.ALL:
        errors.append(_("Invalid declaration type selected."))
    if certifying_party_kind not in AmpCertifyingPartyKind.ALL:
        errors.append(_("Invalid certifying party selected."))

    revision_date_raw = (request.form.get("revision_date") or "").strip()
    revision_date = None
    if revision_date_raw:
        try:
            revision_date = _date.fromisoformat(revision_date_raw)
        except ValueError:
            errors.append(_("Revision date must be a valid date (YYYY-MM-DD)."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "maintenance/amp_declaration_form.html",
            aircraft=ac,
            decl=decl,
            amp_basis=AmpBasis,
            declaration_types=AmpDeclarationType,
            certifying_party_kinds=AmpCertifyingPartyKind,
        )

    if decl is None:
        decl = AmpDeclaration(aircraft_id=ac.id)
        db.session.add(decl)

    decl.basis = basis
    decl.mip_details = _text("mip_details")
    decl.dah_ica_airframe_ref = _text("dah_ica_airframe_ref")
    decl.dah_ica_engine_ref = _text("dah_ica_engine_ref")
    decl.dah_ica_propeller_ref = _text("dah_ica_propeller_ref")
    decl.pilot_owner_maintenance = request.form.get("pilot_owner_maintenance") == "on"
    decl.pilot_owner_name = _text("pilot_owner_name")
    decl.pilot_owner_licence_number = _text("pilot_owner_licence_number")
    decl.declaration_type = declaration_type
    decl.camo_cao_approval_reference = _text("camo_cao_approval_reference")
    decl.certifying_party_kind = certifying_party_kind
    decl.certifying_party_name = _text("certifying_party_name")
    decl.certifying_party_address = _text("certifying_party_address")
    decl.certifying_party_phone = _text("certifying_party_phone")
    decl.certifying_party_email = _text("certifying_party_email")
    decl.appendix_d_notes = _text("appendix_d_notes")
    decl.revision_number = _text("revision_number")
    decl.revision_content = _text("revision_content")
    decl.revision_date = revision_date

    db.session.commit()
    flash(_("AMP declaration saved."), "success")
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))
