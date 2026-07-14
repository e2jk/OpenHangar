"""
Reservations blueprint — aircraft booking calendar, create/edit/cancel,
owner approval workflow, and per-aircraft booking settings.
"""

import calendar
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse

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
    Aircraft,
    AircraftBookingSettings,
    RateBasis,
    RateType,
    Reservation,
    ReservationStatus,
    Role,
    TenantUser,
    db,
)
from utils import login_required, require_role, user_can_access_aircraft  # pyright: ignore[reportMissingImports]

from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    DEFAULT_PERIOD_MONTHS,
    compute_cost_dashboard,
)

reservations_bp = Blueprint("reservations", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)
_BOOKING_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT)

# How far the manually-set booking rate may drift from the computed cost
# dashboard's wet rate before the owner is nudged to review it.
RATE_DIVERGENCE_WARN_PCT = 0.10


def _safe_next(next_url: str, fallback: str) -> str:
    """Return next_url only when it is a safe relative path, otherwise fallback."""
    next_url = next_url.replace("\\", "")
    parsed = urlparse(next_url)
    if (
        next_url
        and not parsed.scheme
        and not parsed.netloc
        and next_url.startswith("/")
    ):
        return next_url
    return fallback


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover
    return tu.tenant_id


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
        abort(404)
    return ac


def _get_reservation_or_404(ac: Aircraft, res_id: int) -> Reservation:
    r = db.session.get(Reservation, res_id)
    if not r or r.aircraft_id != ac.id:
        abort(404)
    return r


def _has_conflict(
    aircraft_id: int,
    start_dt: datetime,
    end_dt: datetime,
    exclude_id: int | None = None,
) -> bool:
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


def _computed_rate(ac: Aircraft) -> float | None:
    """The cost dashboard's wet rate for this aircraft, or None with no history yet."""
    return compute_cost_dashboard(ac, DEFAULT_PERIOD_MONTHS)["wet_per_hour"]


def _effective_rate(
    ac: Aircraft, settings: AircraftBookingSettings | None
) -> tuple[float | None, str | None]:
    """Return (rate, source): the manually-set rate wins when configured;
    otherwise fall back to the computed cost dashboard rate, if available."""
    if settings and settings.hourly_rate is not None:
        return float(settings.hourly_rate), "manual"
    computed = _computed_rate(ac)
    if computed is not None:
        return computed, "computed"
    return None, None


def _rate_terms_label(settings: AircraftBookingSettings | None) -> str:
    """e.g. 'Wet (Engine time)' — falls back to the column defaults when no
    settings row exists yet, matching AircraftBookingSettings' own defaults.

    Labels are literal _() calls, not a RateType.LABELS[...] dict lookup —
    pybabel's static extractor cannot see a translatable string reached
    through a dynamic key, so a dict-lookup version would silently never
    get translated in any locale.
    """
    rate_type = settings.rate_type if settings else RateType.WET
    rate_basis = settings.rate_basis if settings else RateBasis.ENGINE_TIME
    type_label = _("Wet") if rate_type == RateType.WET else _("Dry")
    basis_label = (
        _("Engine time") if rate_basis == RateBasis.ENGINE_TIME else _("Flight time")
    )
    return f"{type_label} ({basis_label})"


def _is_owner_role(user_id: int) -> bool:
    tu = TenantUser.query.filter_by(user_id=user_id).first()
    return tu is not None and tu.role in _OWNER_ROLES


def _rental_authorization_policy(tenant_id: int) -> str:
    from models import TenantProfile  # pyright: ignore[reportMissingImports]

    profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
    return profile.rental_authorization_policy if profile else "warn"


def _renter_authorization_ok(aircraft_id: int, pilot_user_id: int) -> bool:
    from models import RenterAuthorization  # pyright: ignore[reportMissingImports]

    return RenterAuthorization.valid_for(pilot_user_id, aircraft_id) is not None


def _rate_divergence_warning(
    ac: Aircraft, settings: AircraftBookingSettings | None
) -> str | None:
    """Warn when the manual rate has drifted from the computed cost-basis rate."""
    if not settings or settings.hourly_rate is None:
        return None
    computed = _computed_rate(ac)
    if computed is None or computed == 0:
        return None
    manual = float(settings.hourly_rate)
    pct_diff = abs(manual - computed) / computed
    if pct_diff <= RATE_DIVERGENCE_WARN_PCT:
        return None
    return str(
        _(
            "Your manual rate (%(manual)s EUR/h) differs from the computed "
            "cost-dashboard rate (%(computed)s EUR/h) by more than %(pct)s%% — "
            "consider reviewing it.",
            manual=f"{manual:.2f}",
            computed=f"{computed:.2f}",
            pct=int(RATE_DIVERGENCE_WARN_PCT * 100),
        )
    )


def _chargeable_days(start_dt: datetime, end_dt: datetime) -> int:
    """Number of distinct calendar dates touched by the half-open interval
    [start_dt, end_dt) (tenant-local = UTC dates; the app runs in UTC).

    A booking ending exactly at midnight does not touch that calendar date
    (the interval is half-open), so e.g. 23:00 day1 → 00:00 day2 touches
    only day1, while 23:00 day1 → 00:01 day2 touches both.
    """
    last_day = end_dt.date()
    if end_dt.time() == time(0, 0):
        last_day -= timedelta(days=1)
    return (last_day - start_dt.date()).days + 1


def _estimated_hours(
    start_dt: datetime, end_dt: datetime, settings: AircraftBookingSettings | None
) -> float:
    """Wall-clock duration, floored at chargeable_days × min_hours_per_day
    when that per-aircraft minimum is configured (standard multi-day rental
    practice: a booking spanning N calendar days bills at least N days'
    worth of minimum hours, even if the wall-clock duration is shorter)."""
    wall_clock_hours = (end_dt - start_dt).total_seconds() / 3600
    if settings and settings.min_hours_per_day:
        floor_hours = _chargeable_days(start_dt, end_dt) * float(
            settings.min_hours_per_day
        )
        return max(wall_clock_hours, floor_hours)
    return wall_clock_hours


def _compute_cost(
    start_dt: datetime,
    end_dt: datetime,
    settings: AircraftBookingSettings | None,
    ac: Aircraft,
) -> tuple[float | None, float | None]:
    """Return (hourly_rate, estimated_cost) or (None, None) if no rate available."""
    rate, _source = _effective_rate(ac, settings)
    if rate is None:
        return None, None
    hours = _estimated_hours(start_dt, end_dt, settings)
    return rate, round(rate * hours, 2)


def _build_calendar_grid(year: int, month: int):
    """Return a list of weeks; each week is a list of date objects (Mon–Sun).
    Days outside the month are included to complete the grid."""
    cal = calendar.Calendar(firstweekday=0)  # Monday first
    return cal.monthdatescalendar(year, month)


# ── Fleet reservations overview (admin/owner) ─────────────────────────────────


@reservations_bp.route("/reservations/fleet/")
@login_required
@require_role(*_OWNER_ROLES)
def fleet_reservations():
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover

    role = tu.role
    from utils import accessible_aircraft  # pyright: ignore[reportMissingImports]

    aircraft_qs = accessible_aircraft(tu.tenant_id)
    if role == Role.OWNER:
        # Owners only see planes they explicitly have access to
        from models import UserAircraftAccess, UserAllAircraftAccess  # pyright: ignore[reportMissingImports]

        all_access = UserAllAircraftAccess.query.filter_by(user_id=tu.user_id).first()
        if not all_access:
            owned_ids = [
                r.aircraft_id
                for r in UserAircraftAccess.query.filter_by(user_id=tu.user_id).all()
            ]
            aircraft_qs = aircraft_qs.filter(Aircraft.id.in_(owned_ids))

    aircraft_list = aircraft_qs.order_by(Aircraft.registration).all()
    aircraft_ids = [a.id for a in aircraft_list]

    now = datetime.now(timezone.utc)
    expired_cutoff = now - timedelta(days=60)

    reservations = (
        (
            Reservation.query.filter(
                Reservation.aircraft_id.in_(aircraft_ids),
                # Exclude expired-pending older than 60 days — they're just noise
                db.or_(
                    Reservation.status != ReservationStatus.PENDING,
                    Reservation.start_dt >= expired_cutoff,
                ),
            )
            .order_by(Reservation.start_dt)
            .all()
        )
        if aircraft_ids
        else []
    )

    # SQLite returns naive datetimes even for DateTime(timezone=True) columns;
    # PostgreSQL returns timezone-aware.  Normalize `now` to match so that
    # Python comparisons and Jinja2 template filters stay compatible with both.
    if reservations and reservations[0].start_dt.tzinfo is None:
        now = now.replace(tzinfo=None)

    # Detect overlapping confirmed reservations per aircraft
    overlapping_ids: set[int] = set()
    from itertools import combinations

    confirmed = [r for r in reservations if r.status == ReservationStatus.CONFIRMED]
    by_aircraft: dict[int, list] = {}
    for r in confirmed:
        by_aircraft.setdefault(r.aircraft_id, []).append(r)
    for group in by_aircraft.values():
        for r1, r2 in combinations(group, 2):
            if r1.start_dt < r2.end_dt and r1.end_dt > r2.start_dt:
                overlapping_ids.add(r1.id)
                overlapping_ids.add(r2.id)

    # Find past confirmed reservations with no flight logged on that aircraft/date
    from models import FlightEntry  # pyright: ignore[reportMissingImports]

    missing_flight_ids: set[int] = set()
    for r in reservations:
        if r.status == ReservationStatus.CONFIRMED and r.end_dt <= now:
            start_date = r.start_dt.date()
            end_date = r.end_dt.date()
            has_flight = (
                FlightEntry.query.filter(
                    FlightEntry.aircraft_id == r.aircraft_id,
                    FlightEntry.date >= start_date,
                    FlightEntry.date <= end_date,
                ).first()
                is not None
            )
            if not has_flight:
                missing_flight_ids.add(r.id)

    aircraft_map = {a.id: a for a in aircraft_list}

    return render_template(
        "reservations/fleet.html",
        reservations=reservations,
        aircraft_map=aircraft_map,
        overlapping_ids=overlapping_ids,
        missing_flight_ids=missing_flight_ids,
        now=now,
        ReservationStatus=ReservationStatus,
    )


# ── Calendar view ─────────────────────────────────────────────────────────────


@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/")
@login_required
def calendar_view(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    today = datetime.now(timezone.utc).date()

    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month

    # Clamp to valid range
    if month < 1:
        year -= 1
        month = 12
    if month > 12:
        year += 1
        month = 1

    # Month boundaries in UTC
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    month_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    reservations = (
        Reservation.query.filter(
            Reservation.aircraft_id == ac.id,
            Reservation.start_dt <= month_end,
            Reservation.end_dt >= month_start,
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
    prev_year = year - 1 if month == 1 else year
    next_month = month % 12 + 1
    next_year = year + 1 if month == 12 else year

    weeks = _build_calendar_grid(year, month)

    return render_template(
        "reservations/calendar.html",
        aircraft=ac,
        weeks=weeks,
        day_reservations=day_reservations,
        year=year,
        month=month,
        month_name=datetime(year, month, 1).strftime("%B %Y"),
        today=today,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        ReservationStatus=ReservationStatus,
    )


# ── Create reservation ────────────────────────────────────────────────────────


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/new", methods=["GET", "POST"]
)
@login_required
@require_role(*_BOOKING_ROLES)
def new_reservation(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    if ac.is_archived:
        abort(404)
    settings = ac.booking_settings
    if request.method == "POST":
        return _save_reservation(ac, None, settings)
    # Pre-fill start from query string (clicked day on calendar)
    prefill_start = request.args.get("date", "")
    effective_rate, rate_source = _effective_rate(ac, settings)
    uid = int(session["user_id"])
    renter_auth_blocked = (
        not _is_owner_role(uid)
        and _rental_authorization_policy(ac.tenant_id) == "block"
        and not _renter_authorization_ok(ac.id, uid)
    )
    return render_template(
        "reservations/form.html",
        aircraft=ac,
        reservation=None,
        settings=settings,
        prefill_start=prefill_start,
        effective_rate=effective_rate,
        rate_source=rate_source,
        rate_terms_label=_rate_terms_label(settings),
        renter_auth_blocked=renter_auth_blocked,
    )


# ── Edit reservation ──────────────────────────────────────────────────────────


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/edit",
    methods=["GET", "POST"],
)
@login_required
def edit_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)

    # Pilots may only edit their own pending reservations
    role = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    user_role = role.role if role else None
    is_owner_role = user_role in _OWNER_ROLES
    if not is_owner_role:
        if (
            r.pilot_user_id != session["user_id"]
            or r.status != ReservationStatus.PENDING
        ):
            abort(403)

    settings = ac.booking_settings
    if request.method == "POST":
        return _save_reservation(ac, r, settings)
    effective_rate, rate_source = _effective_rate(ac, settings)
    return render_template(
        "reservations/form.html",
        aircraft=ac,
        reservation=r,
        settings=settings,
        prefill_start="",
        effective_rate=effective_rate,
        rate_source=rate_source,
        rate_terms_label=_rate_terms_label(settings),
        renter_auth_blocked=False,
    )


# ── Cancel reservation ────────────────────────────────────────────────────────


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/cancel", methods=["POST"]
)
@login_required
def cancel_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)

    role = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    user_role = role.role if role else None
    is_owner_role = user_role in _OWNER_ROLES
    if not is_owner_role and r.pilot_user_id != session["user_id"]:
        abort(403)

    if r.status == ReservationStatus.CANCELLED:
        flash(_("Reservation is already cancelled."), "warning")
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    if r.dispatch is not None and r.dispatch.is_checked_out:
        flash(
            _("Cannot cancel: this reservation has already been checked out."),
            "danger",
        )
        return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))

    r.status = ReservationStatus.CANCELLED
    db.session.commit()
    if r.pilot_user_id:
        try:
            from models import NotificationType  # pyright: ignore[reportMissingImports]
            from services.notification_service import dispatch  # pyright: ignore[reportMissingImports]

            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            if tu:
                dispatch(
                    NotificationType.RESERVATION_CANCELLED,
                    tu.tenant_id,
                    {
                        "subject": f"Reservation cancelled — {ac.registration}",
                        "notification_title": f"Reservation cancelled: {ac.registration}",
                        "notification_message": f"Your reservation for {ac.registration} from {r.start_dt.strftime('%Y-%m-%d %H:%M')} to {r.end_dt.strftime('%Y-%m-%d %H:%M')} UTC has been cancelled.",
                        "details": [
                            ("Aircraft", ac.registration),
                            ("Start", r.start_dt.strftime("%Y-%m-%d %H:%M UTC")),
                            ("End", r.end_dt.strftime("%Y-%m-%d %H:%M UTC")),
                        ],
                    },
                    target_user_ids=[r.pilot_user_id],
                )
        except Exception:
            import logging as _log

            _log.getLogger(__name__).exception(
                "Failed to dispatch reservation cancelled notification"
            )
    flash(_("Reservation cancelled."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Confirm / decline (owner only) ───────────────────────────────────────────


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/confirm", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def confirm_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)
    _next = request.form.get("next", "")
    _fallback = url_for("reservations.calendar_view", aircraft_id=ac.id)
    _dest = _safe_next(_next, _fallback)

    if r.status != ReservationStatus.PENDING:
        flash(_("Only pending reservations can be confirmed."), "warning")
        return redirect(_dest)

    if _has_conflict(ac.id, r.start_dt, r.end_dt, exclude_id=r.id):
        flash(_("Cannot confirm: overlapping confirmed reservation exists."), "danger")
        return redirect(_dest)

    if (
        r.pilot_user_id
        and not _is_owner_role(r.pilot_user_id)
        and _rental_authorization_policy(ac.tenant_id) == "block"
        and not _renter_authorization_ok(ac.id, r.pilot_user_id)
    ):
        flash(
            _(
                "Cannot confirm: this renter does not have a valid rental "
                "authorization for this aircraft."
            ),
            "danger",
        )
        return redirect(_dest)

    r.status = ReservationStatus.CONFIRMED
    db.session.commit()
    if r.pilot_user_id:
        try:
            from models import NotificationType  # pyright: ignore[reportMissingImports]
            from services.notification_service import dispatch  # pyright: ignore[reportMissingImports]

            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            if tu:
                dispatch(
                    NotificationType.RESERVATION_CONFIRMED,
                    tu.tenant_id,
                    {
                        "subject": f"Reservation confirmed — {ac.registration}",
                        "notification_title": f"Reservation confirmed: {ac.registration}",
                        "notification_message": f"Your reservation for {ac.registration} from {r.start_dt.strftime('%Y-%m-%d %H:%M')} to {r.end_dt.strftime('%Y-%m-%d %H:%M')} UTC has been confirmed.",
                        "details": [
                            ("Aircraft", ac.registration),
                            ("Start", r.start_dt.strftime("%Y-%m-%d %H:%M UTC")),
                            ("End", r.end_dt.strftime("%Y-%m-%d %H:%M UTC")),
                        ],
                    },
                    target_user_ids=[r.pilot_user_id],
                )
        except Exception:
            import logging as _log

            _log.getLogger(__name__).exception(
                "Failed to dispatch reservation confirmed notification"
            )
    flash(_("Reservation confirmed."), "success")
    return redirect(_dest)


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/decline", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def decline_reservation(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)
    _next = request.form.get("next", "")
    _fallback = url_for("reservations.calendar_view", aircraft_id=ac.id)
    _dest = _safe_next(_next, _fallback)

    if r.status != ReservationStatus.PENDING:
        flash(_("Only pending reservations can be declined."), "warning")
        return redirect(_dest)

    r.status = ReservationStatus.CANCELLED
    db.session.commit()
    flash(_("Reservation declined."), "success")
    return redirect(_dest)


# ── Booking settings (owner only) ─────────────────────────────────────────────


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/settings", methods=["GET", "POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def booking_settings(aircraft_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    settings = ac.booking_settings

    if request.method == "POST":
        return _save_booking_settings(ac, settings)

    return render_template(
        "reservations/settings.html",
        aircraft=ac,
        settings=settings,
        computed_rate=_computed_rate(ac),
        rate_warning=_rate_divergence_warning(ac, settings),
    )


def _save_booking_settings(ac: Aircraft, settings: AircraftBookingSettings | None):
    def _float_or_none(key: str) -> float | None:
        val = request.form.get(key, "").strip()
        try:
            return float(val) if val else None
        except ValueError:
            return None

    min_h = _float_or_none("min_booking_hours")
    max_h = _float_or_none("max_booking_hours")
    rate = _float_or_none("hourly_rate")
    min_per_day = _float_or_none("min_hours_per_day")
    # A real <select>/<radio> form always submits one of the valid values;
    # a blank submission (e.g. an old cached form, or a direct API call that
    # omits the field) falls back to the model's own default rather than
    # being rejected — only a genuinely tampered, non-empty value is invalid.
    rate_basis = request.form.get("rate_basis", "").strip() or RateBasis.ENGINE_TIME
    rate_type = request.form.get("rate_type", "").strip() or RateType.WET

    errors = []
    if min_h is not None and min_h <= 0:
        errors.append(_("Minimum booking duration must be positive."))
    if max_h is not None and max_h <= 0:
        errors.append(_("Maximum booking duration must be positive."))
    if min_h is not None and max_h is not None and min_h > max_h:
        errors.append(_("Minimum duration cannot exceed maximum duration."))
    if rate is not None and rate < 0:
        errors.append(_("Hourly rate cannot be negative."))
    if min_per_day is not None and min_per_day <= 0:
        errors.append(_("Minimum billed hours per day must be positive."))
    if rate_basis not in RateBasis.ALL:
        errors.append(_("Invalid rate basis selected."))
    if rate_type not in RateType.ALL:
        errors.append(_("Invalid rate type selected."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "reservations/settings.html",
            aircraft=ac,
            settings=settings,
            computed_rate=_computed_rate(ac),
            rate_warning=_rate_divergence_warning(ac, settings),
        )

    if settings is None:
        settings = AircraftBookingSettings(aircraft_id=ac.id)
        db.session.add(settings)

    settings.min_booking_hours = min_h
    settings.max_booking_hours = max_h
    settings.hourly_rate = rate
    settings.min_hours_per_day = min_per_day
    settings.rate_basis = rate_basis
    settings.rate_type = rate_type
    db.session.commit()
    flash(_("Booking settings saved."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Shared save logic ─────────────────────────────────────────────────────────


def _save_reservation(
    ac: Aircraft, r: Reservation | None, settings: AircraftBookingSettings | None
):
    start_raw = request.form.get("start_dt", "").strip()
    end_raw = request.form.get("end_dt", "").strip()
    notes = request.form.get("notes", "").strip() or None

    start_dt = _parse_datetime(start_raw)
    end_dt = _parse_datetime(end_raw)

    # Renter authorization guard — only for a brand-new booking made by a
    # non-owner renter (an owner/admin booking on someone's behalf, or
    # editing an existing pending reservation, is out of scope here).
    uid = int(session["user_id"])
    renter_auth_warning: str | None = None
    if r is None and not _is_owner_role(uid):
        policy = _rental_authorization_policy(ac.tenant_id)
        if policy != "off" and not _renter_authorization_ok(ac.id, uid):
            if policy == "block":
                flash(
                    _(
                        "You do not have a valid rental authorization for this "
                        "aircraft. Contact the owner to be authorized before booking."
                    ),
                    "danger",
                )
                effective_rate, rate_source = _effective_rate(ac, settings)
                return render_template(
                    "reservations/form.html",
                    aircraft=ac,
                    reservation=None,
                    settings=settings,
                    prefill_start="",
                    effective_rate=effective_rate,
                    rate_source=rate_source,
                    rate_terms_label=_rate_terms_label(settings),
                    renter_auth_blocked=True,
                )
            renter_auth_warning = str(
                _(
                    "You do not have a valid rental authorization for this "
                    "aircraft yet — your booking request was submitted, but "
                    "check with the owner before flying."
                )
            )

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
                if settings.min_booking_hours and duration < float(
                    settings.min_booking_hours
                ):
                    errors.append(
                        _(
                            "Minimum booking duration is %(h)s h.",
                            h=settings.min_booking_hours,
                        )
                    )
                if settings.max_booking_hours and duration > float(
                    settings.max_booking_hours
                ):
                    errors.append(
                        _(
                            "Maximum booking duration is %(h)s h.",
                            h=settings.max_booking_hours,
                        )
                    )

    if errors:
        for msg in errors:
            flash(msg, "danger")
        effective_rate, rate_source = _effective_rate(ac, settings)
        return render_template(
            "reservations/form.html",
            aircraft=ac,
            reservation=r,
            settings=settings,
            prefill_start="",
            effective_rate=effective_rate,
            rate_source=rate_source,
            rate_terms_label=_rate_terms_label(settings),
            renter_auth_blocked=False,
        )

    hourly_rate, estimated_cost = _compute_cost(start_dt, end_dt, settings, ac)

    _is_new_reservation = r is None
    if r is None:
        r = Reservation(
            aircraft_id=ac.id,
            pilot_user_id=session["user_id"],
            status=ReservationStatus.PENDING,
        )
        db.session.add(r)

    r.start_dt = start_dt
    r.end_dt = end_dt
    r.notes = notes
    r.hourly_rate = hourly_rate
    r.estimated_cost = estimated_cost
    db.session.commit()

    if _is_new_reservation:
        try:
            from models import NotificationType  # pyright: ignore[reportMissingImports]
            from services.notification_service import dispatch  # pyright: ignore[reportMissingImports]

            tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
            if tu:
                details = [
                    ("Aircraft", ac.registration),
                    ("Start", r.start_dt.strftime("%Y-%m-%d %H:%M UTC")),
                    ("End", r.end_dt.strftime("%Y-%m-%d %H:%M UTC")),
                ]
                if renter_auth_warning:
                    details.append(("Note", "No valid rental authorization on file"))
                dispatch(
                    NotificationType.RESERVATION_REQUEST,
                    tu.tenant_id,
                    {
                        "subject": f"New booking request — {ac.registration}",
                        "notification_title": f"New booking request: {ac.registration}",
                        "notification_message": f"A new booking request was submitted for {ac.registration} from {r.start_dt.strftime('%Y-%m-%d %H:%M')} to {r.end_dt.strftime('%Y-%m-%d %H:%M')} UTC.",
                        "details": details,
                    },
                )
        except Exception:
            import logging as _log

            _log.getLogger(__name__).exception(
                "Failed to dispatch reservation request notification"
            )

    if renter_auth_warning:
        flash(renter_auth_warning, "warning")
    flash(_("Reservation saved."), "success")
    return redirect(url_for("reservations.calendar_view", aircraft_id=ac.id))


# ── Reservation detail + dispatch (Phase 37d) ─────────────────────────────────


def _can_dispatch(r: Reservation, user_id: int) -> bool:
    return _is_owner_role(user_id) or r.pilot_user_id == user_id


def _discrepancy_warning(ac: Aircraft, r: Reservation) -> str | None:
    """Compare the dispatch counter delta against the sum of linked
    flight-entry counter deltas; return a warning naming both figures when
    they differ, or None when they match (or there isn't enough data)."""
    d = r.dispatch
    if d is None or not d.is_checked_in:
        return None

    parts = []
    if d.out_flight_counter is not None and d.in_flight_counter is not None:
        dispatch_delta = float(d.in_flight_counter) - float(d.out_flight_counter)
        flights_sum = sum(
            float(fe.flight_time_counter_end) - float(fe.flight_time_counter_start)
            for fe in r.flights
            if fe.flight_time_counter_end is not None
            and fe.flight_time_counter_start is not None
        )
        if round(dispatch_delta, 1) != round(flights_sum, 1):
            parts.append(
                str(
                    _(
                        "flight time: dispatch shows %(d)s h, logged flights show %(f)s h",
                        d=f"{dispatch_delta:.1f}",
                        f=f"{flights_sum:.1f}",
                    )
                )
            )
    if d.out_engine_counter is not None and d.in_engine_counter is not None:
        dispatch_delta = float(d.in_engine_counter) - float(d.out_engine_counter)
        flights_sum = sum(
            float(fe.engine_time_counter_end) - float(fe.engine_time_counter_start)
            for fe in r.flights
            if fe.engine_time_counter_end is not None
            and fe.engine_time_counter_start is not None
        )
        if round(dispatch_delta, 1) != round(flights_sum, 1):
            parts.append(
                str(
                    _(
                        "engine time: dispatch shows %(d)s h, logged flights show %(f)s h",
                        d=f"{dispatch_delta:.1f}",
                        f=f"{flights_sum:.1f}",
                    )
                )
            )
    if not parts:
        return None
    return str(
        _(
            "Dispatch/logbook discrepancy — %(details)s.",
            details="; ".join(parts),
        )
    )


@reservations_bp.route("/aircraft/<int:aircraft_id>/reservations/<int:res_id>")
@login_required
def reservation_detail(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)
    uid = int(session["user_id"])
    is_owner = _is_owner_role(uid)
    if not is_owner and r.pilot_user_id != uid:
        abort(403)

    today_start = datetime.combine(
        r.start_dt.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc
    )
    can_checkout = (
        _can_dispatch(r, uid)
        and r.status == ReservationStatus.CONFIRMED
        and (r.dispatch is None or not r.dispatch.is_checked_out)
        and datetime.now(timezone.utc) >= today_start
    )
    can_checkin = (
        _can_dispatch(r, uid)
        and r.dispatch is not None
        and r.dispatch.is_checked_out
        and not r.dispatch.is_checked_in
    )

    return render_template(
        "reservations/detail.html",
        aircraft=ac,
        reservation=r,
        dispatch=r.dispatch,
        is_owner=is_owner,
        can_checkout=can_checkout,
        can_checkin=can_checkin,
        discrepancy_warning=_discrepancy_warning(ac, r),
    )


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/checkout",
    methods=["GET", "POST"],
)
@login_required
def checkout(aircraft_id: int, res_id: int):
    from models import DispatchRecord, Snag  # pyright: ignore[reportMissingImports]

    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)
    uid = int(session["user_id"])
    if not _can_dispatch(r, uid):
        abort(403)
    if r.status != ReservationStatus.CONFIRMED:
        flash(_("Only confirmed reservations can be checked out."), "danger")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )

    dispatch_record = r.dispatch
    if dispatch_record is not None and dispatch_record.is_checked_out:
        flash(_("This reservation has already been checked out."), "warning")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )

    open_snags = (
        Snag.query.filter_by(aircraft_id=ac.id).filter(Snag.resolved_at.is_(None)).all()
    )

    if request.method == "POST":
        walkaround_ok = bool(request.form.get("walkaround_ok"))
        snags_acknowledged = bool(request.form.get("snags_acknowledged"))
        grounded_override = bool(request.form.get("grounded_override"))
        engine_counter = request.form.get("out_engine_counter", "").strip() or None
        flight_counter = request.form.get("out_flight_counter", "").strip() or None
        fuel_state = request.form.get("out_fuel_state", "").strip() or None

        errors = []
        if not walkaround_ok:
            errors.append(_("Walk-around confirmation is required."))
        if not snags_acknowledged:
            errors.append(_("You must acknowledge the open snag list."))
        if ac.is_grounded and not (_is_owner_role(uid) and grounded_override):
            errors.append(
                _(
                    "This aircraft is grounded — dispatch is blocked. An owner "
                    "may override with an explicit confirmation."
                )
            )

        try:
            engine_val = float(engine_counter) if engine_counter else None
        except ValueError:
            engine_val = None
            errors.append(_("Invalid engine counter value."))
        try:
            flight_val = float(flight_counter) if flight_counter else None
        except ValueError:
            flight_val = None
            errors.append(_("Invalid flight counter value."))

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "reservations/checkout.html",
                aircraft=ac,
                reservation=r,
                open_snags=open_snags,
                counter_hint=_checkout_counter_hint(ac.id),
            )

        if dispatch_record is None:
            dispatch_record = DispatchRecord(reservation_id=r.id)
            db.session.add(dispatch_record)

        dispatch_record.out_at = datetime.now(timezone.utc)
        dispatch_record.out_by_id = uid
        dispatch_record.out_engine_counter = engine_val
        dispatch_record.out_flight_counter = flight_val
        dispatch_record.out_fuel_state = fuel_state
        dispatch_record.out_walkaround_ok = walkaround_ok
        dispatch_record.out_snags_acknowledged = snags_acknowledged
        dispatch_record.out_grounded_override = ac.is_grounded and grounded_override
        db.session.commit()
        flash(_("Checked out."), "success")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )

    return render_template(
        "reservations/checkout.html",
        aircraft=ac,
        reservation=r,
        open_snags=open_snags,
        counter_hint=_checkout_counter_hint(ac.id),
    )


def _checkout_counter_hint(aircraft_id: int) -> dict[str, float | None]:
    from flights.routes import _get_counter_hint  # pyright: ignore[reportMissingImports]

    return _get_counter_hint(aircraft_id)


@reservations_bp.route(
    "/aircraft/<int:aircraft_id>/reservations/<int:res_id>/checkin",
    methods=["GET", "POST"],
)
@login_required
def checkin(aircraft_id: int, res_id: int):
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_reservation_or_404(ac, res_id)
    uid = int(session["user_id"])
    if not _can_dispatch(r, uid):
        abort(403)

    dispatch_record = r.dispatch
    if dispatch_record is None or not dispatch_record.is_checked_out:
        flash(_("Check out before checking in."), "danger")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )
    if dispatch_record.is_checked_in:
        flash(_("This reservation has already been checked in."), "warning")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )

    if request.method == "POST":
        engine_counter = request.form.get("in_engine_counter", "").strip() or None
        flight_counter = request.form.get("in_flight_counter", "").strip() or None
        fuel_state = request.form.get("in_fuel_state", "").strip() or None
        notes = request.form.get("in_notes", "").strip() or None

        errors = []
        engine_val: float | None = None
        flight_val: float | None = None
        try:
            engine_val = float(engine_counter) if engine_counter else None
        except ValueError:
            errors.append(_("Invalid engine counter value."))
        try:
            flight_val = float(flight_counter) if flight_counter else None
        except ValueError:
            errors.append(_("Invalid flight counter value."))

        if (
            not errors
            and engine_val is not None
            and dispatch_record.out_engine_counter is not None
            and engine_val < float(dispatch_record.out_engine_counter)
        ):
            errors.append(
                _("Engine counter on return cannot be less than the check-out value.")
            )
        if (
            not errors
            and flight_val is not None
            and dispatch_record.out_flight_counter is not None
            and flight_val < float(dispatch_record.out_flight_counter)
        ):
            errors.append(
                _("Flight counter on return cannot be less than the check-out value.")
            )

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "reservations/checkin.html",
                aircraft=ac,
                reservation=r,
                dispatch=dispatch_record,
            )

        dispatch_record.in_at = datetime.now(timezone.utc)
        dispatch_record.in_by_id = uid
        dispatch_record.in_engine_counter = engine_val
        dispatch_record.in_flight_counter = flight_val
        dispatch_record.in_fuel_state = fuel_state
        dispatch_record.in_notes = notes
        db.session.commit()
        flash(_("Checked in."), "success")
        return redirect(
            url_for("reservations.reservation_detail", aircraft_id=ac.id, res_id=r.id)
        )

    return render_template(
        "reservations/checkin.html",
        aircraft=ac,
        reservation=r,
        dispatch=dispatch_record,
    )
