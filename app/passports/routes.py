"""Read-only foundation; creation and editing are delivered in 41b."""

from flask import render_template
from flask.typing import ResponseReturnValue
from models import PassportAirfield, PassportAirfieldIdentity, db
from utils import login_required

from passports import (
    lifecycle,  # Register transaction-safe file cleanup.
    passports_bp,
)
from passports.access import accessible_passports, require_passport

__all__ = ["lifecycle", "passports_bp"]


@passports_bp.route("/")
@login_required
def index() -> ResponseReturnValue:
    passports = accessible_passports().order_by("title", "id").all()
    return render_template("passports/index.html", passports=passports)


@passports_bp.route("/<int:passport_id>/")
@login_required
def detail(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    targets = (
        db.session.query(PassportAirfieldIdentity)
        .join(
            PassportAirfield,
            PassportAirfield.airfield_id == PassportAirfieldIdentity.id,
        )
        .filter(PassportAirfield.passport_id == passport.id)
        .order_by(PassportAirfield.display_order, PassportAirfield.id)
        .all()
    )
    return render_template("passports/detail.html", passport=passport, targets=targets)
