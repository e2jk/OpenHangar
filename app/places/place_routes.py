"""Private destination forms, photos and collection-governed actions."""

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import get_locale
from flask_babel import gettext as _
from models import Place, PlaceCollection, PlaceCollectionMember, PlaceVisit, User, db
from services import geocoding
from utils import db_id, login_required

from places import management
from places.access import (
    PlaceError,
    actor,
    collection_access,
    collections_for_user,
    place_access,
)
from places.collections import default_collection
from places.coordinates import parse_coordinates
from places.routes import context, places_bp
from places.tags import STARTER_TAGS


def editing_collections(tid: int, uid: int) -> list[PlaceCollection]:
    result: list[PlaceCollection] = (
        collections_for_user(tid, uid)
        .filter(PlaceCollectionMember.can_edit.is_(True))
        .order_by(PlaceCollection.name, PlaceCollection.id)
        .all()
    )
    return result


def show_form(
    tid: int, uid: int, place: Place | None, selected: int | None, *, status: int = 200
) -> ResponseReturnValue:
    values = (
        dict(request.form)
        if request.method == "POST"
        else {
            key: str(getattr(place, key) or "") if place is not None else ""
            for key in (
                "title",
                "description",
                "address",
                "external_url",
                "latitude",
                "longitude",
            )
        }
    )
    # Zero is a valid latitude or longitude and must survive the edit form.
    if place is not None and request.method != "POST":
        values.update(latitude=str(place.latitude), longitude=str(place.longitude))
    tags = (
        request.form.get("tags", "")
        if request.method == "POST"
        else "\n".join(management.tag_names(place.id))
        if place is not None
        else ""
    )
    return render_template(
        "places/place_form.html",
        place=place,
        collections=editing_collections(tid, uid),
        selected=selected,
        values=values,
        tags=tags,
        starter_tags=[_(name) for name in STARTER_TAGS],
        geocoder_enabled=geocoding.enabled(),
        geocoder_attribution=geocoding.attribution(),
        geocoder_disabled=geocoding.GeocoderError("disabled").message,
    ), status


def save_form(
    tid: int, uid: int, place: Place | None, collection_id: int
) -> ResponseReturnValue:
    old_name = place.photo_filename if place is not None else None
    new_name = None
    try:
        saved = management.save_place(
            tid,
            uid,
            collection_id,
            request.form,
            place_id=place.id if place is not None else None,
            tags=request.form.get("tags", "").splitlines()
            + request.form.getlist("starter_tags"),
            photo=request.files.get("photo"),
            remove_photo=request.form.get("remove_photo") == "yes",
        )
        if saved.photo_filename != old_name:
            new_name = saved.photo_filename
        db.session.commit()
        return redirect(url_for("places.place_detail", place_id=saved.id))
    except Exception as exc:
        db.session.rollback()
        if new_name is not None:
            management.photo_path(new_name).unlink(missing_ok=True)
        if not isinstance(exc, PlaceError):
            raise
        flash(str(exc), "danger")
        return show_form(tid, uid, place, collection_id, status=400)


@places_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_place() -> ResponseReturnValue:
    tid, uid = context()
    try:
        actor(tid, uid, writing=True)
    except PlaceError:
        abort(403)
    if request.method == "POST":
        collection_id = request.form.get("collection", type=db_id)
        if collection_id is None:
            flash(_("Choose a collection you can edit."), "danger")
            return show_form(tid, uid, None, None, status=400)
        return save_form(tid, uid, None, collection_id)
    selected = request.args.get("collection", type=db_id)
    if selected is None:
        selected = default_collection(tid, uid).id
        db.session.commit()
    try:
        collection_access(tid, uid, selected, editing=True)
    except PlaceError:
        abort(404)
    return show_form(tid, uid, None, selected)


@places_bp.route("/<int:place_id>/edit", methods=["GET", "POST"])
@login_required
def edit_place(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        place = place_access(tid, uid, place_id, editing=True)
    except PlaceError:
        abort(404)
    if request.method == "POST":
        return save_form(tid, uid, place, place.collection_id)
    return show_form(tid, uid, place, place.collection_id)


@places_bp.route("/<int:place_id>")
@login_required
def place_detail(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        place = place_access(tid, uid, place_id)
    except PlaceError:
        abort(404)
    member = db.session.get(PlaceCollectionMember, (place.collection_id, uid))
    user = actor(tid, uid)
    names = {
        person.id: person.name or _("Unnamed member")
        for person in User.query.join(
            PlaceCollectionMember, PlaceCollectionMember.user_id == User.id
        ).filter(
            PlaceCollectionMember.collection_id == place.collection_id,
            User.is_active.is_(True),
        )
    }
    collection = db.session.get(PlaceCollection, place.collection_id)
    assert collection is not None
    return render_template(
        "places/place_detail.html",
        visited=PlaceVisit.query.filter_by(place_id=place.id, user_id=uid).first()
        is not None,
        place=place,
        collection=collection,
        tags=management.tag_names(place.id),
        can_edit=member is not None and member.can_edit and not user.view_only,
        can_move=place.owner_user_id == uid,
        can_purge=uid in {place.owner_user_id, collection.created_by_user_id},
        last_changed=names.get(place.updated_by_user_id, _("Former member")),
        owner_name=names.get(place.owner_user_id, _("Former member")),
        destinations=editing_collections(tid, uid),
    )


@places_bp.route("/<int:place_id>/photo")
@login_required
def place_photo(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    try:
        place = place_access(tid, uid, place_id)
    except PlaceError:
        abort(404)
    if not place.photo_filename:
        abort(404)
    path = management.photo_path(place.photo_filename)
    if not path.is_file():
        abort(404)
    return send_file(
        path, mimetype="image/jpeg", conditional=False, etag=False, max_age=0
    )


@places_bp.route("/coordinates", methods=["POST"])
@login_required
def coordinates() -> ResponseReturnValue:
    tid, uid = context()
    actor(tid, uid)
    try:
        lat, lon = parse_coordinates(request.form.get("coordinates", ""))
    except PlaceError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(latitude=lat, longitude=lon)


@places_bp.route("/<int:place_id>/action", methods=["POST"])
@login_required
def place_action(place_id: int) -> ResponseReturnValue:
    tid, uid = context()
    action = request.form.get("action")
    try:
        place = place_access(
            tid,
            uid,
            place_id,
            editing=True,
            include_deleted=action in {"restore", "purge"},
        )
    except PlaceError:
        abort(404)
    cid = place.collection_id
    new_name = None
    redirect_id = place.id
    try:
        if action in {"move", "copy"}:
            target = request.form.get("target", type=db_id)
            if target is None:
                raise PlaceError(_("Choose a collection you can edit."))
            if action == "copy":
                result = management.copy_place(tid, uid, place_id, target)
                redirect_id = result.id
                new_name = result.photo_filename
            else:
                management.move_place(tid, uid, place_id, target)
        elif action in {"archive", "unarchive"}:
            management.archive_place(tid, uid, place_id, action == "archive")
        elif action == "trash":
            management.trash_place(tid, uid, place_id)
        elif action == "restore":
            management.restore_place(tid, uid, place_id)
        elif action == "purge":
            if request.form.get("confirm") != "yes":
                raise PlaceError(_("Confirm the permanent deletion of this place."))
            management.purge_place(tid, uid, place_id)
        else:
            abort(400)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if new_name is not None:
            management.photo_path(new_name).unlink(missing_ok=True)
        if not isinstance(exc, PlaceError):
            raise
        flash(str(exc), "danger")
        return redirect(
            url_for("places.index", collection=cid, state="bin")
            if place.deleted_at is not None
            else url_for("places.place_detail", place_id=place_id)
        )
    if action in {"trash", "purge"}:
        return redirect(url_for("places.index", collection=cid, state="bin"))
    return redirect(url_for("places.place_detail", place_id=redirect_id))


@places_bp.route("/geocode", methods=["POST"])
@login_required
def geocode() -> ResponseReturnValue:
    tid, uid = context()
    actor(tid, uid)
    try:
        results = geocoding.search(
            request.form.get("query", ""),
            language=str(get_locale() or "en"),
            limit=5,
        )
    except geocoding.GeocoderError as exc:
        status = (
            400 if exc.reason == "invalid" else 429 if exc.reason == "busy" else 503
        )
        return jsonify(error=exc.message, reason=exc.reason), status
    return jsonify(
        results=[
            {
                "name": item.name,
                "address": item.address,
                "latitude": item.latitude,
                "longitude": item.longitude,
            }
            for item in results
        ]
    )
