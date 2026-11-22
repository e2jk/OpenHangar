"""Minimal mobile visit form: choose the airfield, the date, save.

A plain form with no map; it works without JavaScript. With it, a photo taken
at the airfield can suggest the airfield and the date (read, never stored:
photo_target below). It records the participant's own manual visit through
the same validation as the airfield page; editing or deleting a
visit stays on that airfield's page. Nothing about other instance users,
aircraft or other participants' evidence is shown.
"""

from datetime import date

from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
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


# A photo suggests the passport airfield nearest where it was taken, within
# this distance, unless a second one is about as close.
PHOTO_TARGET_KM = 3.0


@passports_bp.route("/<int:passport_id>/photo-target", methods=["POST"])
@login_required
def photo_target(passport_id: int) -> ResponseReturnValue:
    """Which of this passport's airfields a photo was taken at, and when —
    read in memory and discarded (services.photo_hints)."""
    from services.images import MAX_PHOTO_BYTES
    from services.photo_hints import AMBIGUOUS_RATIO, read_hints
    from utils import haversine_nm

    passport = require_passport(passport_id)
    uid = session["user_id"]
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        abort(400)
    hints = read_hints(
        upload.stream.read(MAX_PHOTO_BYTES + 1),
        upload.filename,
        request.form.get("offset", type=int),
    )
    target = None
    position = hints["position"]
    if position is not None:
        distances = sorted(
            (
                haversine_nm(
                    position["latitude"],
                    position["longitude"],
                    float(row["latitude"]),
                    float(row["longitude"]),
                )
                * 1.852,
                row["target_id"],
                row,
            )
            for row in passport_progress(passport, uid)["rows"]
            if row.get("latitude") is not None and row.get("longitude") is not None
        )
        if distances and distances[0][0] <= PHOTO_TARGET_KM:
            nearest_km, target_id, row = distances[0]
            if (
                len(distances) == 1
                or distances[1][0] >= max(nearest_km, 0.2) * AMBIGUOUS_RATIO
            ):
                target = {
                    "target_id": target_id,
                    "name": row["name"],
                    "distance_km": round(nearest_km, 1),
                }
    response = jsonify(target=target, date=hints["date"])
    response.headers["Cache-Control"] = "private, no-store"
    return response
