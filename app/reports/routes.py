import csv
import io
from datetime import date as _date
from typing import Any

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    Response,
    abort,
    render_template,
    request,
    session,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from models import Aircraft, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import (  # pyright: ignore[reportMissingImports]
    login_required,
    user_can_access_aircraft,
)

from reports.utilization import (  # pyright: ignore[reportMissingImports]
    DEFAULT_PERIOD_MONTHS,
    PERIOD_OPTIONS,
    compute_utilization_report,
    resolve_period,
)

reports_bp = Blueprint("reports", __name__)


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


def _resolve_report_range(
    today: _date,
) -> tuple[_date | None, _date, int | None, str, str]:
    """Read `?from=&to=` (an arbitrary policy year) or `?period=` (a rolling
    preset, default 12 months) from the query string. Returns
    (period_start, period_end, period_months, from_raw, to_raw) —
    period_months is None when a custom range was used, so the template
    knows which selector mode was active."""
    from_raw = request.args.get("from", "").strip()
    to_raw = request.args.get("to", "").strip()
    if from_raw and to_raw:
        try:
            custom_start = _date.fromisoformat(from_raw)
            custom_end = _date.fromisoformat(to_raw)
        except ValueError:
            pass
        else:
            if custom_start <= custom_end:
                return custom_start, custom_end, None, from_raw, to_raw

    try:
        period_months = int(request.args.get("period", DEFAULT_PERIOD_MONTHS))
    except ValueError:
        period_months = DEFAULT_PERIOD_MONTHS
    period_start, period_end = resolve_period(period_months, today)
    return period_start, period_end, period_months, from_raw, to_raw


@reports_bp.route("/aircraft/<aircraft_ref:aircraft_id>/reports/utilization")
@login_required
def utilization_report(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    today = _date.today()
    period_start, period_end, period_months, from_raw, to_raw = _resolve_report_range(
        today
    )
    report = compute_utilization_report(ac.id, period_start, period_end)

    return render_template(
        "reports/utilization.html",
        aircraft=ac,
        report=report,
        period_months=period_months,
        period_options=PERIOD_OPTIONS,
        from_date=from_raw,
        to_date=to_raw,
        today=today.isoformat(),
    )


@reports_bp.route("/aircraft/<aircraft_ref:aircraft_id>/reports/utilization.csv")
@login_required
def utilization_report_csv(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    today = _date.today()
    period_start, period_end, _period_months, _from_raw, _to_raw = (
        _resolve_report_range(today)
    )
    report = compute_utilization_report(ac.id, period_start, period_end)

    buf = io.StringIO()
    writer = csv.writer(buf)
    period_start_str = period_start.isoformat() if period_start else _("all time")
    period_label = f"{period_start_str} {_('to')} {period_end.isoformat()}"
    writer.writerow([_("Aircraft"), ac.registration])
    writer.writerow([_("Period"), period_label])
    writer.writerow([_("Export date"), today.isoformat()])
    writer.writerow([])

    current = report["current"]
    previous = report["previous"]
    header = [_("Metric"), _("This period")]
    if previous is not None:
        header.append(_("Previous period"))
    writer.writerow(header)

    def _fuel_str(fuel: dict[str, float]) -> str:
        if not fuel:
            return "0"
        return ", ".join(f"{qty:.1f} {unit}" for unit, qty in sorted(fuel.items()))

    def _stat_row(stats: dict[str, Any], key: str) -> str:
        value = stats[key]
        if key == "fuel_added":
            return _fuel_str(value)
        if key in ("flight_count", "landings"):
            return str(value)
        return f"{value:.2f}" if key == "oil_added_l" else f"{value:.1f}"

    metrics = [
        (_("Flight hours"), "flight_hours"),
        (_("Engine hours"), "engine_hours"),
        (_("Number of flights"), "flight_count"),
        (_("Landings"), "landings"),
        (_("Fuel added"), "fuel_added"),
        (_("Oil added (L)"), "oil_added_l"),
    ]
    for label, key in metrics:
        row = [label, _stat_row(current, key)]
        if previous is not None:
            row.append(_stat_row(previous, key))
        writer.writerow(row)

    filename = f"{ac.registration}_utilization_{period_end.isoformat()}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
