from datetime import UTC, date, datetime

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
    Refuel,
    Role,
    TenantUser,
    db,
)
from utils import (  # pyright: ignore[reportMissingImports]
    activity,
    login_required,
    require_role,
    user_can_access_aircraft,
)

refuels_bp = Blueprint("refuels", __name__)

_CREW_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT, Role.MAINTENANCE)
_UNITS = ["L", "gal"]


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


def _get_refuel_or_404(aircraft: Aircraft, refuel_id: int) -> Refuel:
    r = db.session.get(Refuel, refuel_id)
    if not r or r.aircraft_id != aircraft.id:
        abort(404)
    return r


def _parse_required_date(raw: str, label: str) -> tuple[date | None, str | None]:
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


# ── Refuel list ───────────────────────────────────────────────────────────────


@refuels_bp.route("/aircraft/<aircraft_ref:aircraft_id>/refuels")
@login_required
def list_refuels(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    refuels = (
        Refuel.query.filter_by(aircraft_id=ac.id)
        .order_by(Refuel.date.desc(), Refuel.id.desc())
        .all()
    )
    return render_template("refuels/list.html", aircraft=ac, refuels=refuels)


# ── Add refuel ────────────────────────────────────────────────────────────────


@refuels_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/refuels/new", methods=["GET", "POST"]
)
@login_required
@require_role(*_CREW_ROLES)
def new_refuel(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_refuel(ac, None)
    return render_template(
        "refuels/refuel_form.html",
        aircraft=ac,
        refuel=None,
        units=_UNITS,
        today=date.today().isoformat(),
    )


# ── Edit refuel ───────────────────────────────────────────────────────────────


@refuels_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/refuels/<int:refuel_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def edit_refuel(aircraft_id: int, refuel_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_refuel_or_404(ac, refuel_id)
    if request.method == "POST":
        return _save_refuel(ac, r)
    return render_template(
        "refuels/refuel_form.html",
        aircraft=ac,
        refuel=r,
        units=_UNITS,
        today=date.today().isoformat(),
    )


def _save_refuel(ac: Aircraft, r: Refuel | None) -> ResponseReturnValue:
    date_raw = request.form.get("date", "").strip()
    quantity_raw = request.form.get("quantity", "").strip()
    unit = request.form.get("unit", "").strip()
    note = request.form.get("note", "").strip() or None

    errors = []
    refuel_date, date_error = _parse_required_date(date_raw, str(_("Date")))
    if date_error:
        errors.append(date_error)

    quantity: float | None = None
    if not quantity_raw:
        errors.append(_("Quantity is required."))
    else:
        try:
            quantity = float(quantity_raw)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            errors.append(_("Quantity must be a positive number."))

    if unit not in _UNITS:
        unit = "L"

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "refuels/refuel_form.html",
            aircraft=ac,
            refuel=r,
            units=_UNITS,
            today=date.today().isoformat(),
        )

    _refuel_is_new = r is None
    if r is None:
        r = Refuel(aircraft_id=ac.id, created_by_id=session.get("user_id"))
        db.session.add(r)

    assert refuel_date is not None
    r.date = refuel_date
    r.quantity = quantity
    r.unit = unit
    r.note = note
    db.session.commit()

    activity(
        "refuel.logged" if _refuel_is_new else "refuel.edited",
        refuel_id=r.id,
        aircraft_id=ac.id,
        quantity=str(r.quantity),
        unit=r.unit,
    )
    flash(_("Refuel recorded."), "success")
    return redirect(url_for("refuels.list_refuels", aircraft_id=ac.id))


# ── Delete refuel ─────────────────────────────────────────────────────────────


@refuels_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/refuels/<int:refuel_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def delete_refuel(aircraft_id: int, refuel_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    r = _get_refuel_or_404(ac, refuel_id)
    db.session.delete(r)
    db.session.commit()
    flash(_("Refuel deleted."), "success")
    return redirect(url_for("refuels.list_refuels", aircraft_id=ac.id))
