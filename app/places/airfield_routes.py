"""Explicit collection-editor controls for reaching a place from an airfield."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import db
from services.destination_search import DestinationError, positive
from utils import db_id, login_required

from places import airfields, candidates
from places.access import PlaceError, place_access
from places.routes import context, places_bp


@places_bp.route("/<int:place_id>/airfields", methods=["GET", "POST"])
@login_required
def airfield_links(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        place = place_access(tid, uid, place_id, editing=True)
    except PlaceError:
        return abort(404)
    status = 200
    if request.method == "POST":
        try:
            action = request.form.get("action", "")
            if action == "add":
                candidates.select_airfield(
                    tid, uid, place.id, request.form.get("key", "")
                )
            elif action == "custom":
                candidates.create_manual_airfield(
                    tid,
                    uid,
                    place.id,
                    name=request.form.get("name", ""),
                    code=request.form.get("code", ""),
                    latitude=request.form.get("latitude", ""),
                    longitude=request.form.get("longitude", ""),
                )
            elif action in {"update", "remove"}:
                fid = request.form.get("airfield", type=db_id)
                if fid is None:
                    raise PlaceError(_("Airfield not found."))
                if action == "remove":
                    airfields.remove_link(tid, uid, place.id, fid)
                else:
                    airfields.update_link(
                        tid,
                        uid,
                        place.id,
                        fid,
                        note=request.form.get("note", ""),
                        preferred=request.form.get("preferred") == "yes",
                    )
            else:
                raise PlaceError(_("Choose an airfield action."))
            db.session.commit()
            return redirect(url_for("places.airfield_links", place_id=place.id))
        except PlaceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            status = 400
    query = request.args.get("q", "")
    try:
        radius = (
            positive(request.args.get("radius", "15"), _("search radius"), maximum=200)
            or 15
        )
        results = candidates.suggestions(
            tid, uid, place.id, query=query, radius_km=radius
        )
    except (PlaceError, DestinationError) as exc:
        flash(str(exc), "danger")
        radius, results, status = 15, [], 400
    linked = airfields.linked_airfields(
        tid, uid, place_ids=[place.id], include_archived=True
    )
    linked_ids = {row["airfield_id"] for row in linked}
    return render_template(
        "places/airfield_links.html",
        place=place,
        linked=linked,
        results=results,
        linked_ids=linked_ids,
        airfield_types={
            "small_airport": _("Small airfield"),
            "medium_airport": _("Medium airport"),
            "large_airport": _("Large airport"),
            "seaplane_base": _("Seaplane base"),
            "heliport": _("Heliport"),
            "closed": _("Closed airfield"),
        },
        query=query,
        radius=radius,
    ), status
