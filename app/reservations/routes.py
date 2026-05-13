"""
Reservations blueprint — aircraft booking calendar, create/edit/cancel,
owner approval workflow, and per-aircraft booking settings.
"""
import calendar
from datetime import datetime, timedelta, timezone

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

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, AircraftBookingSettings, Reservation, ReservationStatus,
    Role, TenantUser, User, db,
)
from utils import login_required, require_role  # pyright: ignore[reportMissingImports]

reservations_bp = Blueprint("reservations", __name__)

_OWNER_ROLES  = (Role.ADMIN, Role.OWNER)
_BOOKING_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover
    return tu.tenant_id


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if not ac or ac.tenant_id != _tenant_id():
        abort(404)
    return ac


def _get_reservation_or_404(ac: Aircraft, res_id: int) -> Reservation:
    r = db.session.get(Reservation, res_id)
    if not r or r.aircraft_id != ac.id:
        abort(404)
    return r


def _has_conflict(aircraft_id: int, start_dt: datetime, end_dt: datetime,
                  exclude_id: int | None = None) -> bool:
    """Return True if any confirmed reservation overlaps [start_dt, end_dt)."""
    q = Reservation.query.filter(
        Reservation.aircraft_id == aircraft_id,
        Reservation.status == ReservationStatus.CONFIRMED,
        Reservation.start_dt < end_dt,
        Reservation.end_dt > start_dt,
    )
    if exclude_id is not None:
        q = q.filter(Reservation.id != exclude_id)
    return q.first() is not None


def _parse_datetime(s: str) -> datetime | None:
    """Parse 'YYYY-MM-DDTHH:MM' (HTML datetime-local) → UTC-aware datetime."""
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _compute_cost(duration_hours: float,
                  settings: AircraftBookingSettings | None) -> tuple[float | None, float | None]:
    """Return (hourly_rate, estimated_cost) or (None, None) if no rate configured."""
    if not settings or settings.hourly_rate is None:
        return None, None
    rate = float(settings.hourly_rate)
    return rate, round(rate * duration_hours, 2)


def _build_calendar_grid(year: int, month: int):
    """Return a list of weeks; each week is a list of date objects (Mon–Sun).
    Days outside the month are included to complete the grid."""
    cal = calendar.Calendar(firstweekday=0)  # Monday first
    return cal.monthdatescalendar(year, month)


# ── Calendar view ─────────────────────────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/")
@login_required
def calendar_view(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    today = datetime.now(timezone.utc).date()

    try:
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month

    # Clamp to valid range
    if month < 1:  year -= 1; month = 12
    if month > 12: year += 1; month = 1

    # Month boundaries in UTC
    from datetime import date
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    month_end   = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    reservations = (
        Reservation.query
        .filter(
            Reservation.aircraft_id == ac.id,
            Reservation.start_dt <= month_end,
            Reservation.end_dt   >= month_start,
        )
        .order_by(Reservation.start_dt)
        .all()
    )

    # Build a dict day → list of reservations for fast template lookup
    from collections import defaultdict
    day_reservations: dict = defaultdict(list)
    for r in reservations:
        # A reservation may span multiple days — add it to each day it touches
        cur = r.start_dt.date()
        end = r.end_dt.date()
        while cur <= end:
            day_reservations[cur].append(r)
            cur += timedelta(days=1)

    # Prev / next month navigation
    prev_month = month - 1 or 12
    prev_year  = year - 1 if month == 1 else year
    next_month = month % 12 + 1
    next_year  = year + 1 if month == 12 else year

    weeks = _build_calendar_grid(year, month)

    return render_template(
        "reservations/calendar.html",
        aircraft=ac,
        weeks=weeks,
        day_reservations=day_reservations,
        year=year, month=month,
        month_name=datetime(year, month, 1).strftime("%B %Y"),
        today=today,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        ReservationStatus=ReservationStatus,
    )


# ── Create reservation ────────────────────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/new",
                        methods=["GET", "POST"])
@login_required
@require_role(*_BOOKING_ROLES)
def new_reservation(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    settings = ac.booking_settings
    if request.method == "POST":
        return _save_reservation(ac, None, settings)
    # Pre-fill start from query string (clicked day on calendar)
    prefill_start = request.args.get("date", "")
    return render_template("reservations/form.html",
                           aircraft=ac, reservation=None,
                           settings=settings, prefill_start=prefill_start)


# ── Edit reservation ──────────────────────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/<int:res_id>/edit",
                        methods=["GET", "POST"])
@login_required
def edit_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r  = _get_reservation_or_404(ac, res_id)

    # Pilots may only edit their own pending reservations
    role = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    user_role = role.role if role else None
    is_owner_role = user_role in _OWNER_ROLES
    if not is_owner_role:
        if r.pilot_user_id != session["user_id"] or r.status != ReservationStatus.PENDING:
            abort(403)

    settings = ac.booking_settings
    if request.method == "POST":
        return _save_reservation(ac, r, settings)
    return render_template("reservations/form.html",
                           aircraft=ac, reservation=r,
                           settings=settings, prefill_start="")


# ── Cancel reservation ────────────────────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/<int:res_id>/cancel",
                        methods=["POST"])
@login_required
def cancel_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r  = _get_reservation_or_404(ac, res_id)

    role = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    user_role = role.role if role else None
    is_owner_role = user_role in _OWNER_ROLES
    if not is_owner_role and r.pilot_user_id != session["user_id"]:
        abort(403)

    if r.status == ReservationStatus.CANCELLED:
        flash(_("Reservation is already cancelled."), "warning")
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    r.status = ReservationStatus.CANCELLED
    db.session.commit()
    flash(_("Reservation cancelled."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Confirm / decline (owner only) ───────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/<int:res_id>/confirm",
                        methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def confirm_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r  = _get_reservation_or_404(ac, res_id)

    if r.status != ReservationStatus.PENDING:
        flash(_("Only pending reservations can be confirmed."), "warning")
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    if _has_conflict(ac.id, r.start_dt, r.end_dt, exclude_id=r.id):
        flash(_("Cannot confirm: overlapping confirmed reservation exists."), "danger")
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    r.status = ReservationStatus.CONFIRMED
    db.session.commit()
    flash(_("Reservation confirmed."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/<int:res_id>/decline",
                        methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def decline_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r  = _get_reservation_or_404(ac, res_id)

    if r.status != ReservationStatus.PENDING:
        flash(_("Only pending reservations can be declined."), "warning")
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    r.status = ReservationStatus.CANCELLED
    db.session.commit()
    flash(_("Reservation declined."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Booking settings (owner only) ─────────────────────────────────────────────

@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/settings",
                        methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def booking_settings(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    settings = ac.booking_settings

    if request.method == "POST":
        return _save_booking_settings(ac, settings)

    return render_template("reservations/settings.html", aircraft=ac, settings=settings)


def _save_booking_settings(ac: Aircraft, settings: AircraftBookingSettings | None):
    def _float_or_none(key: str) -> float | None:
        val = request.form.get(key, "").strip()
        try:
            return float(val) if val else None
        except ValueError:
            return None

    min_h  = _float_or_none("min_booking_hours")
    max_h  = _float_or_none("max_booking_hours")
    rate   = _float_or_none("hourly_rate")

    errors = []
    if min_h is not None and min_h <= 0:
        errors.append(_("Minimum booking duration must be positive."))
    if max_h is not None and max_h <= 0:
        errors.append(_("Maximum booking duration must be positive."))
    if min_h is not None and max_h is not None and min_h > max_h:
        errors.append(_("Minimum duration cannot exceed maximum duration."))
    if rate is not None and rate < 0:
        errors.append(_("Hourly rate cannot be negative."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("reservations/settings.html", aircraft=ac, settings=settings)

    if settings is None:
        settings = AircraftBookingSettings(aircraft_id=ac.id)
        db.session.add(settings)

    settings.min_booking_hours = min_h
    settings.max_booking_hours = max_h
    settings.hourly_rate       = rate
    db.session.commit()
    flash(_("Booking settings saved."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Shared save logic ─────────────────────────────────────────────────────────

def _save_reservation(ac: Aircraft, r: Reservation | None,
                      settings: AircraftBookingSettings | None):
    start_raw = request.form.get("start_dt", "").strip()
    end_raw   = request.form.get("end_dt",   "").strip()
    notes     = request.form.get("notes",    "").strip() or None

    start_dt = _parse_datetime(start_raw)
    end_dt   = _parse_datetime(end_raw)

    errors = []
    if not start_dt:
        errors.append(_("Start date/time is required."))
    if not end_dt:
        errors.append(_("End date/time is required."))
    if start_dt and end_dt:
        if end_dt <= start_dt:
            errors.append(_("End must be after start."))
        else:
            duration = (end_dt - start_dt).total_seconds() / 3600
            if settings:
                if settings.min_booking_hours and duration < float(settings.min_booking_hours):
                    errors.append(_(
                        "Minimum booking duration is %(h)s h.",
                        h=settings.min_booking_hours,
                    ))
                if settings.max_booking_hours and duration > float(settings.max_booking_hours):
                    errors.append(_(
                        "Maximum booking duration is %(h)s h.",
                        h=settings.max_booking_hours,
                    ))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("reservations/form.html",
                               aircraft=ac, reservation=r,
                               settings=settings, prefill_start="")

    hourly_rate, estimated_cost = _compute_cost(
        (end_dt - start_dt).total_seconds() / 3600, settings
    )

    if r is None:
        r = Reservation(
            aircraft_id=ac.id,
            pilot_user_id=session["user_id"],
            status=ReservationStatus.PENDING,
        )
        db.session.add(r)

    r.start_dt       = start_dt
    r.end_dt         = end_dt
    r.notes          = notes
    r.hourly_rate    = hourly_rate
    r.estimated_cost = estimated_cost
    db.session.commit()

    flash(_("Reservation saved."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))
