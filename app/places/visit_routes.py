"""Record, correct and remove one's own visits from a place's page (42e)."""

from __future__ import annotations

from flask import abort, flash, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import db
from utils import login_required

from places import visits
from places.access import PlaceError
from places.routes import context, places_bp


def _done(place_id: int, message: str) -> ResponseReturnValue:
    db.session.commit()
    flash(message, "success")
    return redirect(url_for("places.place_detail", place_id=place_id))


def _refused(place_id: int, exc: PlaceError) -> ResponseReturnValue:
    db.session.rollback()
    if isinstance(exc, visits.NotFound):
        return abort(404)
    flash(str(exc), "danger")
    return redirect(url_for("places.place_detail", place_id=place_id))


@places_bp.route("/<int:place_id>/visits", methods=["POST"])
@login_required
def add_visit(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        visits.record(tid, uid, place_id, request.form)
    except PlaceError as exc:
        return _refused(place_id, exc)
    return _done(place_id, _("Visit recorded."))


@places_bp.route("/<int:place_id>/visits/<int:visit_id>", methods=["POST"])
@login_required
def edit_visit(place_id: int, visit_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        visits.update(tid, uid, place_id, visit_id, request.form)
    except PlaceError as exc:
        return _refused(place_id, exc)
    return _done(place_id, _("Visit updated."))


@places_bp.route("/<int:place_id>/visits/<int:visit_id>/delete", methods=["POST"])
@login_required
def delete_visit(place_id: int, visit_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        visits.delete(tid, uid, place_id, visit_id)
    except PlaceError as exc:
        return _refused(place_id, exc)
    return _done(place_id, _("Visit removed."))
