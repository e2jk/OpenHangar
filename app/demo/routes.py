import logging
import os
from datetime import UTC, datetime, timedelta

from extensions import _rate_limiting_disabled  # pyright: ignore[reportMissingImports]
from extensions import limiter as _limiter
from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_babel import (
    get_locale as _get_locale,  # pyright: ignore[reportMissingImports]
)
from models import DemoSlot, Passport, PassportSeries, User, db
from passports.editions import current_edition, latest_edition
from services import auth_sessions

log = logging.getLogger(__name__)

demo_bp = Blueprint("demo", __name__)

_DEFAULT_BUSY_WINDOW = 30


def _busy_window_minutes() -> int:
    try:
        return int(
            os.environ.get("OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES", _DEFAULT_BUSY_WINDOW)
        )
    except ValueError:
        return _DEFAULT_BUSY_WINDOW


@demo_bp.route("/demo/enter", methods=["POST"])
@_limiter.limit("3 per minute", exempt_when=_rate_limiting_disabled)
def enter() -> ResponseReturnValue:
    return _enter_demo(request.form.get("role", "owner"))


@demo_bp.route("/landegut")
@_limiter.limit("3 per minute", exempt_when=_rate_limiting_disabled)
def landegut() -> ResponseReturnValue:
    purpose = request.headers.get("Sec-Purpose") or request.headers.get("Purpose", "")
    if (
        request.method == "HEAD"
        or purpose.lower().split(";", 1)[0].strip() == "prefetch"
    ):
        return "", 204, {"Cache-Control": "no-store"}
    response = make_response(_enter_demo("owner", landegut=True))
    response.headers["Cache-Control"] = "no-store"
    return response


def _enter_demo(role: str, *, landegut: bool = False) -> ResponseReturnValue:
    existing_slot_id = session.get("demo_slot_id")
    slot = db.session.get(DemoSlot, existing_slot_id) if existing_slot_id else None
    if slot is None:
        # Capture visitor locale before replacing the session.
        visitor_lang = str(_get_locale())
        cutoff = datetime.now(UTC) - timedelta(minutes=_busy_window_minutes())
        slot = (
            DemoSlot.query.filter(
                db.or_(
                    DemoSlot.last_activity_at.is_(None),
                    DemoSlot.last_activity_at < cutoff,
                )
            )
            .order_by(DemoSlot.last_activity_at.asc().nullsfirst())
            .with_for_update(skip_locked=True)
            .first()
        )
        if slot is None:
            if DemoSlot.query.first() is None:
                return redirect(url_for("index"))
            return render_template("demo_full.html"), 503

        # Save the reservation with any commit made while revoking an old login.
        slot.last_activity_at = datetime.now(UTC)
        auth_sessions.revoke_current()
        session.clear()
        session["demo_slot_id"] = slot.id
        session["language"] = visitor_lang

    auth_sessions.start(User.query.filter_by(id=_slot_user_id(slot, role)).one())
    session["demo_role"] = role
    session.permanent = True
    _touch_slot(slot)
    return redirect(_landegut_url(slot) if landegut else url_for("index"))


def _landegut_url(slot: DemoSlot) -> str:
    passports = (
        Passport.query.join(PassportSeries, Passport.series_id == PassportSeries.id)
        .filter(
            PassportSeries.title == "Lande-Gutscheinheft",
            PassportSeries.tenant_id == slot.tenant_id,
            PassportSeries.owner_user_id == slot.user_id,
            Passport.tenant_id == slot.tenant_id,
            Passport.owner_user_id == slot.user_id,
            Passport.archived_at.is_(None),
        )
        .all()
    )
    edition = current_edition(passports) or latest_edition(passports)
    return (
        url_for("passports.detail", passport_id=edition.id)
        if edition
        else url_for("passports.index")
    )


def _slot_user_id(slot: DemoSlot, role: str) -> int:
    """Return the correct user_id for the requested role in this slot."""
    if role in ("renter", "pilot") and slot.renter_user_id:
        return int(slot.renter_user_id)
    if role == "maintenance" and slot.maintenance_user_id:
        return int(slot.maintenance_user_id)
    if role == "viewer" and slot.viewer_user_id:
        return int(slot.viewer_user_id)
    if role == "sole_pilot" and slot.sole_pilot_user_id:
        return int(slot.sole_pilot_user_id)
    if role == "sole_operator" and slot.sole_operator_user_id:
        return int(slot.sole_operator_user_id)
    if role == "shared_ownership" and slot.shared_ownership_user_id:
        return int(slot.shared_ownership_user_id)
    return int(slot.user_id)


def _touch_slot(slot: DemoSlot) -> None:
    slot.last_activity_at = datetime.now(UTC)
    db.session.commit()


@demo_bp.route("/demo/next-wipe")
def next_wipe() -> ResponseReturnValue:
    """Return the scheduled next wipe time for the browser reload-detection logic."""
    from flask import jsonify

    return jsonify({"next_wipe": os.environ.get("OPENHANGAR_DEMO_NEXT_WIPE_UTC")}), 200


def demo_has_recent_activity(window_minutes: int = 20) -> bool:
    """Return True if any slot had activity within *window_minutes*."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    return int(DemoSlot.query.filter(DemoSlot.last_activity_at >= cutoff).count()) > 0
