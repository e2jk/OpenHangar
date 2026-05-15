"""
Share blueprint — public read-only aircraft status pages via token.
"""

import io
import secrets

from flask import (
    Blueprint,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)  # pyright: ignore[reportMissingImports]

from models import (
    Aircraft,
    Document,
    ExpenseType,
    FlightEntry,
    MaintenanceTrigger,
    Role,
    ShareToken,
    TenantUser,
    db,
)  # pyright: ignore[reportMissingImports]
from utils import login_required, require_role  # pyright: ignore[reportMissingImports]

share_bp = Blueprint("share", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

_TOKEN_LENGTH = 8


def _generate_token() -> str:
    """Return a unique 8-character URL-safe token."""
    while True:
        candidate = secrets.token_urlsafe(6)[:_TOKEN_LENGTH]
        if not ShareToken.query.filter_by(token=candidate).first():
            return candidate


def _get_aircraft_or_403(aircraft_id: int) -> Aircraft:
    """Fetch an aircraft belonging to the logged-in user's tenant, or 403."""
    from utils import login_required  # noqa: F401 — guard already applied by decorator

    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover
    ac = db.session.get(Aircraft, aircraft_id)
    if not ac or ac.tenant_id != tu.tenant_id:
        abort(404)
    return ac


# ── Token management (owner-facing) ──────────────────────────────────────────


@share_bp.route("/aircraft/<int:aircraft_id>/share/create", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def create_token(aircraft_id):
    ac = _get_aircraft_or_403(aircraft_id)
    access_level = request.form.get("access_level", "summary")
    if access_level not in ("summary", "full"):
        access_level = "summary"
    token = _generate_token()
    db.session.add(
        ShareToken(aircraft_id=ac.id, token=token, access_level=access_level)
    )
    db.session.commit()
    return redirect(url_for("aircraft.detail", aircraft_id=aircraft_id))


@share_bp.route(
    "/aircraft/<int:aircraft_id>/share/<int:token_id>/revoke", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def revoke_token(aircraft_id, token_id):
    ac = _get_aircraft_or_403(aircraft_id)
    from datetime import datetime, timezone

    st = db.session.get(ShareToken, token_id)
    if not st or st.aircraft_id != ac.id:
        abort(404)
    st.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for("aircraft.detail", aircraft_id=aircraft_id))


@share_bp.route("/aircraft/<int:aircraft_id>/share/<int:token_id>/qr")
@login_required
@require_role(*_OWNER_ROLES)
def token_qr(aircraft_id, token_id):
    ac = _get_aircraft_or_403(aircraft_id)
    st = db.session.get(ShareToken, token_id)
    if not st or st.aircraft_id != ac.id or not st.is_active:
        abort(404)

    import qrcode  # pyright: ignore[reportMissingImports]

    share_url = request.host_url.rstrip("/") + url_for(
        "share.public_view", token=st.token
    )
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4
    )
    qr.add_data(share_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Content-Disposition"] = f'attachment; filename="share_{st.token}.png"'
    return resp


# ── Public view ───────────────────────────────────────────────────────────────


@share_bp.route("/share/<token>")
def public_view(token):
    st = ShareToken.query.filter_by(token=token).first()
    if not st or not st.is_active:
        abort(404)

    ac = st.aircraft
    hobbs = ac.total_engine_hours
    flight_hours = ac.total_flight_hours
    triggers = MaintenanceTrigger.query.filter_by(aircraft_id=ac.id).all()
    maintenance_summary = [(t, t.status(hobbs)) for t in triggers]

    overdue = [(t, s) for t, s in maintenance_summary if s == "overdue"]
    due_soon = [(t, s) for t, s in maintenance_summary if s == "due_soon"]

    recent_flights = None
    recent_documents = None
    if st.access_level == "full":
        recent_flights = (
            FlightEntry.query.filter_by(aircraft_id=ac.id)
            .order_by(FlightEntry.date.desc(), FlightEntry.id.desc())
            .limit(5)
            .all()
        )
        recent_documents = (
            Document.query.filter_by(aircraft_id=ac.id, is_sensitive=False)
            .order_by(Document.uploaded_at.desc())
            .limit(10)
            .all()
        )

    resp = make_response(
        render_template(
            "share/public.html",
            aircraft=ac,
            token=st,
            hobbs=hobbs,
            flight_hours=flight_hours,
            maintenance_summary=maintenance_summary,
            overdue=overdue,
            due_soon=due_soon,
            recent_flights=recent_flights,
            recent_documents=recent_documents,
            expense_type_labels=ExpenseType.LABELS,
        )
    )
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
