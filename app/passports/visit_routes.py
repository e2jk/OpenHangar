"""Minimal mobile visit form: choose the airfield, the date, save.

A plain form with no map or JavaScript. It records the participant's own manual
visit through the same validation as the airfield page; editing or deleting a
visit stays on that airfield's page. Nothing about other instance users,
aircraft or other participants' evidence is shown.
"""

from datetime import date

from flask import abort, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import db
from utils import db_id, login_required

from passports import evidence, passports_bp
from passports.access import require_passport
from passports.eligibility import passport_progress
from passports.management import BuilderError


@passports_bp.route("/<int:passport_id>/record-visit", methods=["GET", "POST"])
@login_required
def record_visit(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    uid = session["user_id"]
    try:
        participant = evidence.writable_participant(passport, uid)
    except BuilderError as exc:
        # An explicit exit: the rest of the view always has a participant.
        return abort(exc.status, description=str(exc))
    targets = sorted(
        passport_progress(passport, uid)["rows"],
        key=lambda row: (str(row.get("name") or "").casefold(), row["target_id"]),
    )
    error = None
    status = 200
    if request.method == "POST":
        target_id = request.form.get("target", type=db_id)
        if target_id is None or target_id not in {row["target_id"] for row in targets}:
            error, status = _("Choose an airfield of this passport."), 422
        else:
            try:
                evidence.add_visit(passport, participant, target_id, request.form)
                db.session.commit()
                return redirect(
                    url_for(
                        "passports.target_detail",
                        passport_id=passport.id,
                        target_id=target_id,
                    )
                )
            except BuilderError as exc:
                db.session.rollback()
                error, status = str(exc), exc.status
    return render_template(
        "passports/record_visit.html",
        passport=passport,
        targets=targets,
        today=date.today().isoformat(),
        error=error,
    ), status
