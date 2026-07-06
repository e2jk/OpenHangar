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
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from typing import Any

from models import (
    Aircraft,
    Expense,
    ExpenseCategory,
    ExpenseType,
    FlightEntry,
    Role,
    TenantUser,
    db,
)  # pyright: ignore[reportMissingImports]
from utils import login_required, require_role, user_can_access_aircraft  # pyright: ignore[reportMissingImports]

from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    DEFAULT_PERIOD_MONTHS,
    PERIOD_OPTIONS,
    compute_cost_dashboard,
)

expenses_bp = Blueprint("expenses", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

_CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
_UNITS = ["L", "gal"]
_DEFAULT_PERIOD = 12  # months


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


def _get_expense_or_404(aircraft: Aircraft, expense_id: int) -> Expense:
    exp = db.session.get(Expense, expense_id)
    if not exp or exp.aircraft_id != aircraft.id:
        abort(404)
    return exp


def _compute_stats(
    expenses: list[Any], aircraft_id: int, period_months: int
) -> tuple[float, float | None, str]:
    """Return (total_cost, cost_per_hour, period_label) for the filtered expense list."""
    total_cost = sum(float(e.amount) for e in expenses)

    if period_months > 0:
        cutoff = _date.today() - timedelta(days=period_months * 30)
        flights = FlightEntry.query.filter(
            FlightEntry.aircraft_id == aircraft_id,
            FlightEntry.date >= cutoff,
        ).all()
        period_label = f"last {period_months} months"
    else:
        flights = FlightEntry.query.filter_by(aircraft_id=aircraft_id).all()
        period_label = "all time"

    total_hours = sum(
        float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
        for f in flights
        if f.flight_time_counter_end is not None
        and f.flight_time_counter_start is not None
    )
    cost_per_hour = round(total_cost / total_hours, 2) if total_hours > 0 else None
    return total_cost, cost_per_hour, period_label


# ── Expense list ──────────────────────────────────────────────────────────────


@expenses_bp.route("/aircraft/<int:aircraft_id>/expenses")
@login_required
def list_expenses(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    type_filter = request.args.get("type", "")
    try:
        period_months = int(request.args.get("period", _DEFAULT_PERIOD))
    except ValueError:
        period_months = _DEFAULT_PERIOD

    query = Expense.query.filter_by(aircraft_id=ac.id)

    if type_filter and type_filter in ExpenseType.ALL:
        query = query.filter_by(expense_type=type_filter)

    if period_months > 0:
        cutoff = _date.today() - timedelta(days=period_months * 30)
        query = query.filter(Expense.date >= cutoff)

    expenses = query.order_by(Expense.date.desc(), Expense.id.desc()).all()
    total_cost, cost_per_hour, period_label = _compute_stats(
        expenses, ac.id, period_months
    )

    return render_template(
        "expenses/list.html",
        aircraft=ac,
        expenses=expenses,
        type_filter=type_filter,
        period_months=period_months,
        total_cost=total_cost,
        cost_per_hour=cost_per_hour,
        period_label=period_label,
        expense_type_labels=ExpenseType.LABELS,
        currencies=_CURRENCIES,
    )


# ── Cost dashboard (Phase 36) ──────────────────────────────────────────────────


@expenses_bp.route("/aircraft/<int:aircraft_id>/costs")
@login_required
def cost_dashboard(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    try:
        period_months = int(request.args.get("period", DEFAULT_PERIOD_MONTHS))
    except ValueError:
        period_months = DEFAULT_PERIOD_MONTHS
    if period_months not in PERIOD_OPTIONS:
        period_months = DEFAULT_PERIOD_MONTHS

    dashboard = compute_cost_dashboard(ac, period_months)

    return render_template(
        "expenses/cost_dashboard.html",
        aircraft=ac,
        period_months=period_months,
        period_options=PERIOD_OPTIONS,
        **dashboard,
    )


# ── Add expense ───────────────────────────────────────────────────────────────


@expenses_bp.route("/aircraft/<int:aircraft_id>/expenses/add", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def add_expense(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    if request.method == "POST":
        err = _validate_and_save(ac, expense=None)
        if err is None:
            flash(_("Expense recorded."), "success")
            return redirect(url_for("expenses.list_expenses", aircraft_id=ac.id))
        flash(err, "danger")

    return render_template(
        "expenses/expense_form.html",
        aircraft=ac,
        expense=None,
        expense_types=ExpenseType.LABELS,
        currencies=_CURRENCIES,
        units=_UNITS,
        today=_date.today().isoformat(),
    )


# ── Edit expense ──────────────────────────────────────────────────────────────


@expenses_bp.route(
    "/aircraft/<int:aircraft_id>/expenses/<int:expense_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def edit_expense(aircraft_id: int, expense_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    exp = _get_expense_or_404(ac, expense_id)

    if request.method == "POST":
        err = _validate_and_save(ac, expense=exp)
        if err is None:
            flash(_("Expense updated."), "success")
            return redirect(url_for("expenses.list_expenses", aircraft_id=ac.id))
        flash(err, "danger")

    return render_template(
        "expenses/expense_form.html",
        aircraft=ac,
        expense=exp,
        expense_types=ExpenseType.LABELS,
        currencies=_CURRENCIES,
        units=_UNITS,
        today=_date.today().isoformat(),
    )


# ── Delete expense ────────────────────────────────────────────────────────────


@expenses_bp.route(
    "/aircraft/<int:aircraft_id>/expenses/<int:expense_id>/delete", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_expense(aircraft_id: int, expense_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    exp = _get_expense_or_404(ac, expense_id)
    db.session.delete(exp)
    db.session.commit()
    flash(_("Expense deleted."), "success")
    return redirect(url_for("expenses.list_expenses", aircraft_id=ac.id))


# ── Shared save helper ────────────────────────────────────────────────────────


def _validate_and_save(aircraft: Aircraft, expense: Expense | None) -> str | None:
    """Validate POST data, persist, return error string or None on success."""
    from datetime import date as _date_cls

    date_str = request.form.get("date", "").strip()
    expense_type = request.form.get("expense_type", "").strip()
    expense_category = request.form.get("expense_category", "").strip()
    description = request.form.get("description", "").strip() or None
    amount_str = request.form.get("amount", "").strip()
    currency = request.form.get("currency", "EUR").strip()
    quantity_str = request.form.get("quantity", "").strip()
    unit = request.form.get("unit", "").strip() or None
    coverage_start_str = request.form.get("coverage_start", "").strip()
    coverage_end_str = request.form.get("coverage_end", "").strip()

    if not date_str:
        return str(_("Date is required."))
    try:
        date_val = _date_cls.fromisoformat(date_str)
    except ValueError:
        return str(_("Invalid date format."))

    if expense_type not in ExpenseType.ALL:
        return str(_("Invalid expense type."))

    if not expense_category:
        expense_category = ExpenseCategory.DEFAULTS.get(
            expense_type, ExpenseCategory.OPERATING
        )
    if expense_category not in ExpenseCategory.ALL:
        return str(_("Invalid expense category."))

    if not amount_str:
        return str(_("Amount is required."))
    try:
        amount = float(amount_str)
        if amount < 0:
            raise ValueError
    except ValueError:
        return str(_("Amount must be a non-negative number."))

    quantity = None
    if quantity_str:
        try:
            quantity = float(quantity_str)
            if quantity < 0:
                raise ValueError
        except ValueError:
            return str(_("Quantity must be a non-negative number."))

    coverage_start = None
    coverage_end = None
    if coverage_start_str or coverage_end_str:
        if not (coverage_start_str and coverage_end_str):
            return str(
                _("Coverage start and end dates must both be set, or both left blank.")
            )
        try:
            coverage_start = _date_cls.fromisoformat(coverage_start_str)
            coverage_end = _date_cls.fromisoformat(coverage_end_str)
        except ValueError:
            return str(_("Invalid coverage date format."))
        if coverage_end < coverage_start:
            return str(_("Coverage end date must not be before the start date."))

    if expense is None:
        expense = Expense(aircraft_id=aircraft.id)
        db.session.add(expense)

    expense.date = date_val
    expense.expense_type = expense_type
    expense.expense_category = expense_category
    expense.description = description
    expense.amount = amount
    expense.currency = currency
    expense.quantity = quantity
    expense.unit = unit if quantity else None
    expense.coverage_start = coverage_start
    expense.coverage_end = coverage_end
    db.session.commit()
    return None
