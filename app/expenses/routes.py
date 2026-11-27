from collections import defaultdict
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
    Expense,
    ExpenseType,
    Flight,
    Role,
    db,
)  # pyright: ignore[reportMissingImports]
from pwa import pending  # pyright: ignore[reportMissingImports]
from utils import (  # pyright: ignore[reportMissingImports]
    accessible_aircraft,
    current_membership,
    db_id,
    login_required,
    months_back,
    require_role,
    user_can_access_aircraft,
)
from werkzeug.datastructures import FileStorage

from expenses import fee_checks
from expenses.cost_dashboard import (  # pyright: ignore[reportMissingImports]
    DEFAULT_PERIOD_MONTHS,
    PERIOD_OPTIONS,
    compute_cost_dashboard,
)
from expenses.form_parsing import (
    parse_expense_fields,  # pyright: ignore[reportMissingImports]
)

expenses_bp = Blueprint("expenses", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

_CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
_UNITS = ["L", "gal"]
_DEFAULT_PERIOD = 12  # months


def _tenant_id() -> int:
    tu = current_membership()
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


def _expense_prefill(aircraft: Aircraft, fee_kind: str) -> dict[str, Any] | None:
    """Prefill from a flight fee, a flight refuel, or a standalone refuel."""
    flight_entry_id = request.args.get("flight_entry_id", type=db_id)
    date_raw = request.args.get("date", "").strip()

    flight_id: int | None = None
    prefill_date: str | None = None

    if flight_entry_id is not None:
        flight = db.session.get(Flight, flight_entry_id)
        if flight is not None and flight.aircraft_id == aircraft.id:
            flight_id = flight.id
            prefill_date = flight.date.isoformat()
    elif date_raw:
        try:
            _date.fromisoformat(date_raw)
        except ValueError:
            pass
        else:
            prefill_date = date_raw

    if prefill_date is None:
        return None

    unit = request.args.get("unit", "").strip()
    return {
        "flight_entry_id": flight_id,
        "expense_type": fee_kind
        if flight_id and fee_kind in fee_checks.KINDS
        else None,
        "date": prefill_date,
        "quantity": request.args.get("quantity", "").strip() or None,
        "unit": unit if unit in _UNITS else "L",
    }


def _compute_stats(
    expenses: list[Any], aircraft_id: int, period_months: int
) -> tuple[float, float | None, str]:
    """Return (total_cost, cost_per_hour, period_label) for the filtered expense list."""
    total_cost = sum(float(e.amount) for e in expenses)

    if period_months > 0:
        cutoff = _date.today() - timedelta(days=period_months * 30)
        flights = Flight.query.filter(
            Flight.aircraft_id == aircraft_id,
            Flight.date >= cutoff,
        ).all()
        period_label = f"last {period_months} months"
    else:
        flights = Flight.query.filter_by(aircraft_id=aircraft_id).all()
        period_label = "all time"

    total_hours = sum(
        float(f.flight_time)
        if f.flight_time is not None
        else float(f.flight_time_counter_end) - float(f.flight_time_counter_start)
        for f in flights
        if f.flight_time is not None
        or (
            f.flight_time_counter_end is not None
            and f.flight_time_counter_start is not None
        )
    )
    cost_per_hour = round(total_cost / total_hours, 2) if total_hours > 0 else None
    return total_cost, cost_per_hour, period_label


@expenses_bp.route("/expenses/fee-checks", methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def fee_review() -> ResponseReturnValue:
    aircraft_id = request.args.get("aircraft", type=db_id)
    aircraft = accessible_aircraft(_tenant_id(), include_archived=True).all()
    aircraft_by_id = {item.id: item for item in aircraft}
    if aircraft_id is not None and aircraft_id not in aircraft_by_id:
        return abort(404)
    today = _date.today()
    start = request.args.get(
        "start", today - fee_checks.HISTORY, type=_date.fromisoformat
    )
    end = request.args.get("end", today, type=_date.fromisoformat)
    airfield = request.args.get("airfield", "").strip().upper()
    kind = request.args.get("kind", "")
    aircraft_ids = [aircraft_id] if aircraft_id is not None else list(aircraft_by_id)
    if request.method == "POST":
        flight_id = request.form.get("flight", type=db_id)
        flight = db.session.get(Flight, flight_id) if flight_id is not None else None
        action = request.form.get("action")
        fee_kind = request.form.get("kind")
        if not flight or flight.aircraft_id not in aircraft_ids:
            return abort(404)
        if fee_kind not in fee_checks.KINDS or action not in (
            "no_fee",
            "defer",
            "link",
        ):
            return abort(400)
        finding = next(
            (
                item
                for item in fee_checks.missing_fees(
                    [flight.aircraft_id], flight.date, flight.date, today
                )
                if item.flight.id == flight.id and item.kind == fee_kind
            ),
            None,
        )
        if finding and action == "link":
            expense_id = request.form.get("expense", type=db_id)
            expense = (
                db.session.get(Expense, expense_id) if expense_id is not None else None
            )
            if (
                not expense
                or expense.aircraft_id != flight.aircraft_id
                or fee_kind not in fee_checks.fee_kinds(expense)
                or abs(expense.date - flight.date) > fee_checks.INVOICE_WINDOW
            ):
                return abort(400)
            if not fee_checks.link_expense(expense, flight):
                return abort(409)
        elif finding:
            key = fee_checks.feedback_key(finding.kind, finding.airfield, action)
            fee_checks.record_feedback(flight.id, key, session.get("user_id"))
        db.session.commit()
        return redirect(
            url_for(
                "expenses.fee_review",
                aircraft=aircraft_id,
                start=start,
                end=end,
                airfield=airfield,
                kind=kind,
            )
        )

    findings = [
        item
        for item in fee_checks.missing_fees(aircraft_ids, start, end, today)
        if (not airfield or airfield in item.airfield)
        and (not kind or kind == item.kind)
    ]
    candidates: dict[tuple[int, str], list[Expense]] = defaultdict(list)
    if findings:
        for expense in fee_checks.linkable_expenses(
            aircraft_ids,
            max(start, _date.min + fee_checks.INVOICE_WINDOW)
            - fee_checks.INVOICE_WINDOW,
            min(end, _date.max - fee_checks.INVOICE_WINDOW) + fee_checks.INVOICE_WINDOW,
        ):
            for fee_kind in fee_checks.fee_kinds(expense):
                candidates[(expense.aircraft_id, fee_kind)].append(expense)
    return render_template(
        "expenses/fee_review.html",
        aircraft=aircraft,
        selected_aircraft=aircraft_id,
        aircraft_by_id=aircraft_by_id,
        findings=findings,
        expense_choices={
            (item.flight.id, item.kind): [
                expense
                for expense in candidates[(item.flight.aircraft_id, item.kind)]
                if abs(expense.date - item.flight.date) <= fee_checks.INVOICE_WINDOW
            ]
            for item in findings
        },
        start=start,
        end=end,
        airfield=airfield,
        kind=kind,
        fee_labels={key: ExpenseType.LABELS[key] for key in fee_checks.KINDS},
    )


# ── Expense list ──────────────────────────────────────────────────────────────


@expenses_bp.route("/aircraft/<aircraft_ref:aircraft_id>/expenses")
@login_required
@require_role(*_OWNER_ROLES)
def list_expenses(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    type_filter = request.args.get("type", "")
    period_months = request.args.get("period", _DEFAULT_PERIOD, type=months_back)

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


@expenses_bp.route("/aircraft/<aircraft_ref:aircraft_id>/costs")
@login_required
@require_role(*_OWNER_ROLES)
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


@expenses_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/expenses/add", methods=["GET", "POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def add_expense(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    if request.method == "POST":
        err = _validate_and_save(ac, expense=None)
        if err is None:
            flash(_("Expense recorded."), "success")
            # Opened from the fuel log's "Add cost" (the form posts back to
            # its own URL, so the query string is still there): go back to it.
            if request.args.get("return_to") == "fuel":
                return redirect(url_for("refuels.list_refuels", aircraft_id=ac.id))
            return redirect(url_for("expenses.list_expenses", aircraft_id=ac.id))
        flash(err, "danger")

    fee_kind = request.args.get("fee_kind", "")
    prefill = (
        _expense_prefill(ac, fee_kind)
        if request.method == "GET" or fee_kind in fee_checks.KINDS
        else None
    )
    return render_template(
        "expenses/expense_form.html",
        aircraft=ac,
        expense=None,
        expense_types=ExpenseType.LABELS,
        currencies=_CURRENCIES,
        units=_UNITS,
        today=_date.today().isoformat(),
        prefill=prefill,
        shared_file=pending.current("expense", ac.id),
    )


# ── Edit expense ──────────────────────────────────────────────────────────────


@expenses_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/expenses/<int:expense_id>/edit",
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
        prefill=None,
        shared_file=None,
    )


# ── Delete expense ────────────────────────────────────────────────────────────


@expenses_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/expenses/<int:expense_id>/delete",
    methods=["POST"],
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
    """Validate POST data, persist, return error string or None on success.

    The receipt is the uploaded file, else a file shared to this expense
    form from another app (pwa.pending), used through the same checks.
    """
    receipt_file = request.files.get("receipt")
    if receipt_file is not None and not receipt_file.filename:
        receipt_file = None
    shared = (
        pending.submitted("expense", aircraft.id)
        if receipt_file is None and expense is None
        else None
    )
    if shared is None:
        return _save_expense(aircraft, expense, receipt_file)
    with shared.upload() as shared_receipt:
        error = _save_expense(aircraft, expense, shared_receipt)
    if error is None:
        pending.discard()
    return error


def _save_expense(
    aircraft: Aircraft, expense: Expense | None, receipt_file: FileStorage | None
) -> str | None:
    """Validate the form fields, persist, return error string or None.

    Field validation (excluding the receipt file) is delegated to
    parse_expense_fields(); note this checks the receipt file extension
    *after* all other fields now rather than interleaved between coverage
    and recurrence — a harmless reordering of which single error message
    wins when multiple fields are simultaneously invalid, not a behaviour
    change to what's ultimately accepted or persisted.
    """
    values, error = parse_expense_fields(request.form)
    if error:
        return error

    if receipt_file is not None:
        import os as _os

        from documents.routes import (
            _ALLOWED_EXTS,  # pyright: ignore[reportMissingImports]
        )

        ext = _os.path.splitext(receipt_file.filename or "")[1].lower()
        if ext not in _ALLOWED_EXTS:
            return str(_("This file type is not allowed for receipts."))

    date_val = values["date"]
    description = values["description"]
    quantity = values["quantity"]
    recurrence = values["recurrence"]

    if expense is None:
        expense = Expense(aircraft_id=aircraft.id, created_by_id=session.get("user_id"))
        flight_entry_id = request.form.get("flight_entry_id", type=db_id)
        if flight_entry_id is not None:
            flight = db.session.get(Flight, flight_entry_id)
            if flight is not None and flight.aircraft_id == aircraft.id:
                expense.flight_entry_id = flight.id
        db.session.add(expense)

    expense.date = date_val
    expense.expense_type = values["expense_type"]
    expense.expense_category = values["expense_category"]
    expense.description = description
    expense.amount = values["amount"]
    expense.currency = values["currency"]
    expense.paid_amount = values["paid_amount"]
    expense.paid_currency = values["paid_currency"]
    expense.quantity = quantity
    expense.unit = values["unit"] if quantity else None
    expense.coverage_start = values["coverage_start"]
    expense.coverage_end = values["coverage_end"]
    expense.recurrence = recurrence
    expense.recurrence_end = values["recurrence_end"]
    if recurrence is None:
        expense.recurrence_last_date = None

    if receipt_file is not None:
        from documents.routes import (  # pyright: ignore[reportMissingImports]
            _delete_file,
            _save_upload_canonical,
        )
        from models import (  # pyright: ignore[reportMissingImports]
            DocCategory,
            Document,
            Tenant,
        )
        from werkzeug.utils import (
            secure_filename,  # pyright: ignore[reportMissingImports]
        )

        db.session.flush()
        title = description or _("Expense receipt %(date)s", date=date_val.isoformat())
        tenant = db.session.get(Tenant, aircraft.tenant_id)
        if tenant is None:  # pragma: no cover — the aircraft FK guarantees it exists
            abort(404)
        relpath, mime, size = _save_upload_canonical(
            receipt_file, tenant, aircraft, DocCategory.INVOICE, title
        )
        # A new upload replaces any previous receipt on this expense.
        for old in Document.query.filter_by(expense_id=expense.id).all():
            _delete_file(old.filename)
            db.session.delete(old)
        db.session.add(
            Document(
                aircraft_id=aircraft.id,
                expense_id=expense.id,
                filename=relpath,
                original_filename=secure_filename(receipt_file.filename or "receipt"),
                mime_type=mime,
                size_bytes=size,
                title=title,
                category=DocCategory.INVOICE,
            )
        )

    db.session.commit()
    return None
