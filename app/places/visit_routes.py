"""Record, correct and remove one's own visits from a place's page (42e)."""

from __future__ import annotations

from flask import abort, flash, redirect, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import db
from utils import current_membership, login_required

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


def _back() -> ResponseReturnValue:
    """Back to the page that showed the suggestion (a same-site path only)."""
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        target = url_for("pilots.logbook")
    return redirect(target)


@places_bp.route("/<int:place_id>/visits/from-flight/<int:flight_id>", methods=["POST"])
@login_required
def visit_from_flight(place_id: int, flight_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        visits.record_from_flight(tid, uid, place_id, flight_id)
    except PlaceError as exc:
        db.session.rollback()
        if isinstance(exc, visits.NotFound):
            return abort(404)
        flash(str(exc), "danger")
        return _back()
    db.session.commit()
    flash(_("Visit recorded."), "success")
    return _back()


@places_bp.route(
    "/<int:place_id>/visits/from-flight/<int:flight_id>/dismiss", methods=["POST"]
)
@login_required
def dismiss_visit_suggestion(place_id: int, flight_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        visits.dismiss(tid, uid, place_id, flight_id)
    except PlaceError as exc:
        db.session.rollback()
        if isinstance(exc, visits.NotFound):
            return abort(404)
        flash(str(exc), "danger")
        return _back()
    db.session.commit()
    return _back()


@places_bp.app_context_processor
def _suggestion_helper() -> dict[str, object]:
    """`place_visit_suggestions()` for the pages a flight is saved to; it only
    queries when a template calls it."""

    def place_visit_suggestions() -> list[dict[str, object]]:
        membership = current_membership()
        if membership is None or not session.get("user_id"):
            return []
        return visits.flight_suggestions(membership.tenant_id, session["user_id"])

    return {"place_visit_suggestions": place_visit_suggestions}
