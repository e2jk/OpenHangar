from datetime import UTC, date, datetime, time

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
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Role,
    Snag,
    TenantUser,
    db,
)
from utils import (  # pyright: ignore[reportMissingImports]
    activity,
    login_required,
    require_role,
    user_can_access_aircraft,
)

snags_bp = Blueprint("snags", __name__)

_CREW_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT, Role.MAINTENANCE)


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


def _get_snag_or_404(aircraft: Aircraft, snag_id: int) -> Snag:
    s = db.session.get(Snag, snag_id)
    if not s or s.aircraft_id != aircraft.id:
        abort(404)
    return s


def _parse_required_date(raw: str, label: str) -> tuple[date | None, str | None]:
    """Parse a required ``YYYY-MM-DD`` form field. Returns ``(value, None)``
    on success or ``(None, error_message)`` on the first failure — mirrors
    the ``(values, error)`` idiom used by other blueprints' form parsers."""
    raw = raw.strip()
    if not raw:
        return None, str(_("%(label)s is required.", label=label))
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None, str(_("%(label)s must be a valid date (YYYY-MM-DD).", label=label))
    if d > datetime.now(UTC).date():
        return None, str(_("%(label)s cannot be in the future.", label=label))
    return d, None


# ── Snag list ─────────────────────────────────────────────────────────────────


@snags_bp.route("/aircraft/<aircraft_ref:aircraft_id>/snags")
@login_required
def list_snags(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    open_snags = (
        Snag.query.filter_by(aircraft_id=ac.id, resolved_at=None)
        .order_by(Snag.is_grounding.desc(), Snag.reported_at.desc())
        .all()
    )
    closed_snags = (
        Snag.query.filter(Snag.aircraft_id == ac.id, Snag.resolved_at.isnot(None))
        .order_by(Snag.resolved_at.desc())
        .all()
    )
    return render_template(
        "snags/list.html", aircraft=ac, open_snags=open_snags, closed_snags=closed_snags
    )


# ── Add snag ──────────────────────────────────────────────────────────────────


@snags_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/snags/new", methods=["GET", "POST"]
)
@login_required
@require_role(*_CREW_ROLES)
def new_snag(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_snag(ac, None)
    return render_template(
        "snags/snag_form.html",
        aircraft=ac,
        snag=None,
        today=date.today().isoformat(),
    )


# ── Edit snag ─────────────────────────────────────────────────────────────────


@snags_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/snags/<int:snag_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def edit_snag(aircraft_id: int, snag_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if request.method == "POST":
        return _save_snag(ac, s)
    return render_template(
        "snags/snag_form.html", aircraft=ac, snag=s, today=date.today().isoformat()
    )


def _save_snag(ac: Aircraft, s: Snag | None) -> ResponseReturnValue:
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    reporter = request.form.get("reporter", "").strip() or None
    is_grounding = bool(request.form.get("is_grounding"))
    reported_at_raw = request.form.get("reported_at", "").strip()

    errors = []
    if not title:
        errors.append(_("Title is required."))

    reported_date, date_error = _parse_required_date(
        reported_at_raw, str(_("Date signalled"))
    )
    if date_error:
        errors.append(date_error)

    # Editing an already-closed snag also exposes the resolution fields, so a
    # mistake made when it was originally closed (wrong date, typo in the
    # note) can be fixed without leaving the snag stuck unresolved.
    editing_closed = s is not None and not s.is_open
    resolved_date = None
    resolution_note = None
    if editing_closed:
        resolved_at_raw = request.form.get("resolved_at", "").strip()
        resolution_note = request.form.get("resolution_note", "").strip()
        resolved_date, resolved_error = _parse_required_date(
            resolved_at_raw, str(_("Resolution date"))
        )
        if resolved_error:
            errors.append(resolved_error)
        if not resolution_note:
            errors.append(_("A resolution note is required."))
        if reported_date and resolved_date and resolved_date < reported_date:
            errors.append(_("Resolution date cannot be before the date signalled."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "snags/snag_form.html", aircraft=ac, snag=s, today=date.today().isoformat()
        )

    _snag_is_new = s is None
    old_values = None
    if s is not None and not s.is_open:
        old_values = {
            "title": s.title,
            "description": s.description,
            "reporter": s.reporter,
            "is_grounding": s.is_grounding,
            "reported_at": s.reported_at.isoformat(),
            "resolved_at": s.resolved_at.isoformat() if s.resolved_at else None,
            "resolution_note": s.resolution_note,
        }

    if s is None:
        s = Snag(aircraft_id=ac.id)
        db.session.add(s)

    s.title = title
    s.description = description
    s.reporter = reporter
    s.is_grounding = is_grounding
    assert reported_date is not None
    s.reported_at = datetime.combine(reported_date, time.min, tzinfo=UTC)
    if editing_closed:
        assert resolved_date is not None
        s.resolved_at = datetime.combine(resolved_date, time.min, tzinfo=UTC)
        s.resolution_note = resolution_note
    db.session.commit()

    if old_values is not None:
        activity(
            "snag.edited_closed",
            snag_id=s.id,
            aircraft_id=ac.id,
            old=old_values,
            new={
                "title": s.title,
                "description": s.description,
                "reporter": s.reporter,
                "is_grounding": s.is_grounding,
                "reported_at": s.reported_at.isoformat(),
                "resolved_at": s.resolved_at.isoformat() if s.resolved_at else None,
                "resolution_note": s.resolution_note,
            },
        )

    if _snag_is_new:
        activity(
            "snag.opened",
            snag_id=s.id,
            aircraft_id=ac.id,
            title=title,
            is_grounding=is_grounding,
        )
        try:
            from flask_babel import (
                lazy_gettext as _l,  # pyright: ignore[reportMissingImports]
            )
            from models import NotificationType  # pyright: ignore[reportMissingImports]
            from services.notification_service import (
                dispatch,  # pyright: ignore[reportMissingImports]
            )

            tid = _tenant_id()
            notif_type = (
                NotificationType.GROUNDING_SNAG_OPENED
                if is_grounding
                else NotificationType.SNAG_REPORTED
            )
            if is_grounding:
                subject_key = _l("Grounding snag reported: %(title)s — %(reg)s")
                title_key = _l("Grounding snag reported: %(title)s")
                message_key = _l("A grounding snag was reported on %(reg)s.")
            else:
                subject_key = _l("Snag reported: %(title)s — %(reg)s")
                title_key = _l("Snag reported: %(title)s")
                message_key = _l("A snag was reported on %(reg)s.")
            dispatch(
                notif_type,
                tid,
                {
                    "subject_key": subject_key,
                    "subject_args": {"title": title, "reg": ac.registration},
                    "notification_title_key": title_key,
                    "notification_title_args": {"title": title},
                    "notification_message_key": message_key,
                    "notification_message_args": {"reg": ac.registration},
                    "details": [
                        (_l("Aircraft"), ac.registration),
                        (_l("Title"), title),
                        (_l("Reporter"), s.reporter or "—"),
                    ],
                    "is_grounding": is_grounding,
                },
            )

            if is_grounding:
                from models import (  # pyright: ignore[reportMissingImports]
                    Reservation,
                    ReservationStatus,
                )

                now = datetime.now(UTC)
                affected = (
                    Reservation.query.filter(
                        Reservation.aircraft_id == ac.id,
                        Reservation.status == ReservationStatus.CONFIRMED,
                        Reservation.end_dt >= now,
                        Reservation.pilot_user_id.isnot(None),
                    )
                    .order_by(Reservation.start_dt)
                    .all()
                )
                pilot_ids = sorted({r.pilot_user_id for r in affected})
                if pilot_ids:
                    dates = ", ".join(
                        r.start_dt.strftime("%Y-%m-%d %H:%M") for r in affected
                    )
                    dispatch(
                        NotificationType.RESERVATION_AIRCRAFT_GROUNDED,
                        tid,
                        {
                            "subject_key": _l(
                                "Aircraft grounded — check your reservation: %(reg)s"
                            ),
                            "subject_args": {"reg": ac.registration},
                            "notification_title_key": _l("Aircraft grounded: %(reg)s"),
                            "notification_title_args": {"reg": ac.registration},
                            "notification_message_key": _l(
                                "A grounding snag (%(title)s) was reported on "
                                "%(reg)s, which you hold a confirmed "
                                "reservation for."
                            ),
                            "notification_message_args": {
                                "title": title,
                                "reg": ac.registration,
                            },
                            "details": [
                                (_l("Aircraft"), ac.registration),
                                (_l("Snag"), title),
                                (_l("Affected reservation date(s)"), dates),
                            ],
                        },
                        target_user_ids=pilot_ids,
                    )
        except Exception:
            import logging as _log

            _log.getLogger(__name__).exception("Failed to dispatch snag notification")

    flash(_("Snag '%(title)s' saved.", title=s.title), "success")
    return redirect(url_for("snags.list_snags", aircraft_id=ac.id))


# ── Resolve snag ──────────────────────────────────────────────────────────────


@snags_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/snags/<int:snag_id>/resolve",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def resolve_snag(aircraft_id: int, snag_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if not s.is_open:
        flash(_("Snag is already closed."), "danger")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))

    if request.method == "POST":
        note = request.form.get("resolution_note", "").strip()
        resolved_at_raw = request.form.get("resolved_at", "").strip()

        errors = []
        if not note:
            errors.append(_("A resolution note is required."))
        resolved_date, date_error = _parse_required_date(
            resolved_at_raw, str(_("Resolution date"))
        )
        if date_error:
            errors.append(date_error)
        if resolved_date and resolved_date < s.reported_at.date():
            errors.append(_("Resolution date cannot be before the date signalled."))

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "snags/resolve_form.html",
                aircraft=ac,
                snag=s,
                today=date.today().isoformat(),
            )

        assert resolved_date is not None
        s.resolved_at = datetime.combine(resolved_date, time.min, tzinfo=UTC)
        s.resolution_note = note
        db.session.commit()
        activity(
            "snag.resolved",
            snag_id=snag_id,
            aircraft_id=aircraft_id,
            title=s.title,
            resolved_at=s.resolved_at.isoformat(),
        )
        flash(_("Snag '%(title)s' closed.", title=s.title), "success")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))

    return render_template(
        "snags/resolve_form.html", aircraft=ac, snag=s, today=date.today().isoformat()
    )


# ── Reopen snag ───────────────────────────────────────────────────────────────


@snags_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/snags/<int:snag_id>/reopen",
    methods=["POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def reopen_snag(aircraft_id: int, snag_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if s.is_open:
        flash(_("Snag is already open."), "danger")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))

    previous_resolved_at = s.resolved_at
    s.resolved_at = None
    s.resolution_note = None
    db.session.commit()
    activity(
        "snag.reopened",
        snag_id=s.id,
        aircraft_id=ac.id,
        title=s.title,
        previous_resolved_at=(
            previous_resolved_at.isoformat() if previous_resolved_at else None
        ),
    )
    flash(_("Snag '%(title)s' reopened.", title=s.title), "warning")
    return redirect(url_for("snags.list_snags", aircraft_id=ac.id))


# ── Delete snag ───────────────────────────────────────────────────────────────


@snags_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/snags/<int:snag_id>/delete", methods=["POST"]
)
@login_required
@require_role(*_CREW_ROLES)
def delete_snag(aircraft_id: int, snag_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if not s.is_open:
        flash(_("Closed snags are archived and cannot be deleted."), "danger")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))
    title = s.title
    db.session.delete(s)
    db.session.commit()
    flash(_("Snag '%(title)s' deleted.", title=title), "success")
    return redirect(url_for("snags.list_snags", aircraft_id=ac.id))
