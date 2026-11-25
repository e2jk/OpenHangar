"""Private collection pages: every request checks the current membership."""

from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Place,
    PlaceCollection,
    PlaceCollectionInvitation,
    PlaceCollectionMember,
    PlaceTag,
    PlaceVisit,
    TenantUser,
    User,
    db,
)
from utils import (
    current_membership,
    db_id,
    is_view_only,
    list_index,
    login_required,
)

from places import airfields, collections, sharing, tags
from places.access import PlaceError, actor, collection_access, collections_for_user
from places.maps import airfield_map_data
from places.presentation import place_label

places_bp = Blueprint("places", __name__, url_prefix="/places")


@places_bp.context_processor
def place_template_helpers() -> dict[str, Any]:
    return {"place_label": place_label}


@places_bp.after_request
def private_response(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    if response.status_code == 400:
        response.headers["X-OH-Places-Form-Error"] = "1"
    return response


def context() -> tuple[int, int]:
    membership = current_membership()
    if membership is None:
        abort(403)
    return membership.tenant_id, session["user_id"]


@places_bp.route("/")
@login_required
def index() -> ResponseReturnValue:
    tid, uid = context()
    actor(tid, uid)
    # "My places" is created for someone who has no collection yet — not
    # again after its owner deleted it while keeping (shared) others.
    if not is_view_only(uid, tid) and collections_for_user(tid, uid).first() is None:
        collections.personal_collection(tid, uid)
        db.session.commit()
    collection_id = request.args.get("collection", type=db_id)
    if collection_id is not None:
        try:
            collection_access(tid, uid, collection_id)
        except PlaceError:
            return abort(404)
    choices = (
        collections_for_user(tid, uid)
        .order_by(PlaceCollection.name, PlaceCollection.id)
        .all()
    )
    invitations = (
        sharing.pending_for_user(tid, uid)
        .join(
            PlaceCollection,
            PlaceCollection.id == PlaceCollectionInvitation.collection_id,
        )
        .with_entities(PlaceCollectionInvitation, PlaceCollection.name)
        .all()
    )
    from places.exports import waypoint_selection
    from places.queries import list_places

    listing = list_places(tid, uid, request.args, collection_id)
    editable = (
        {
            m.collection_id
            for m in PlaceCollectionMember.query.filter_by(
                tenant_id=tid, user_id=uid, can_edit=True
            )
        }
        if not is_view_only(uid, tid)
        else set()
    )
    return render_template(
        "places/index.html",
        **listing,
        **airfield_map_data(
            airfields.linked_airfields(
                tid,
                uid,
                place_ids=[row[0].id for row in listing["rows"]],
                include_archived=True,
            )
        ),
        can_download=bool(waypoint_selection(listing["rows"]).points),
        export_args={
            "collection": collection_id,
            "tag": listing["tag_id"],
            **{
                key: listing[key]
                for key in ("visited", "state", "location", "linked", "sort")
            },
        },
        editable_ids=editable,
        collection_owners={c.id: c.created_by_user_id for c in choices},
        collection_names={c.id: c.name for c in choices},
        collections=choices,
        selected=collection_id,
        invitations=invitations,
        can_create=not is_view_only(uid, tid),
    )


@places_bp.route("/collections/new", methods=["GET", "POST"])
@login_required
def new_collection() -> ResponseReturnValue:
    tid, uid = context()
    try:
        actor(tid, uid, writing=True)
    except PlaceError:
        return abort(403)
    if request.method == "POST":
        try:
            collection = collections.create_collection(
                tid, uid, request.form.get("name", "")
            )
            db.session.commit()
            return redirect(
                url_for("places.collection_settings", collection_id=collection.id)
            )
        except PlaceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("places/new_collection.html"), 400
    return render_template("places/new_collection.html")


def collection_counts(collection_id: int) -> tuple[int, int]:
    return (
        Place.query.filter_by(collection_id=collection_id).count(),
        PlaceVisit.query.join(Place, Place.id == PlaceVisit.place_id)
        .filter(Place.collection_id == collection_id)
        .count(),
    )


def settings_context(tid: int, uid: int, cid: int) -> dict[str, Any]:
    try:
        # Members only (the owner is one): nobody else learns it exists.
        collection = collection_access(tid, uid, cid)
    except PlaceError:
        return abort(404)
    members = (
        db.session.query(PlaceCollectionMember, User)
        .join(User, User.id == PlaceCollectionMember.user_id)
        .filter(PlaceCollectionMember.collection_id == cid)
        .order_by(PlaceCollectionMember.joined_at, User.id)
        .all()
    )
    membership = current_membership()
    can_manage = not is_view_only(uid, tid) and collection.created_by_user_id == uid
    organisation_access = membership is not None and membership.access_scope == "full"
    available = []
    if can_manage and organisation_access:
        available = (
            User.query.join(TenantUser)
            .filter(
                TenantUser.tenant_id == tid,
                User.is_active.is_(True),
                User.id.notin_([m.user_id for m, _u in members]),
            )
            .order_by(User.name, User.id)
            .all()
        )
    pending = (
        PlaceCollectionInvitation.query.filter_by(collection_id=cid)
        .order_by(PlaceCollectionInvitation.created_at)
        .all()
        if can_manage
        else []
    )
    own_member = db.session.get(PlaceCollectionMember, (cid, uid))
    return {
        "collection_tags": PlaceTag.query.filter_by(collection_id=cid)
        .order_by(PlaceTag.name)
        .all(),
        "can_edit": own_member is not None
        and own_member.can_edit
        and not is_view_only(uid, tid),
        "can_leave": own_member is not None and collection.created_by_user_id != uid,
        "collection": collection,
        "members": members,
        "available": available,
        "pending": pending,
        "can_manage": can_manage,
        "organisation_access": organisation_access,
        "counts": collection_counts(cid),
    }


def settings_action(tid: int, uid: int, cid: int) -> bool:
    """Return True when leaving/deleting requires returning to the collection list."""
    action = request.form.get("action")
    target = request.form.get("member", type=db_id)
    if action == "rename":
        collections.rename(tid, uid, cid, request.form.get("name", ""))
    elif action == "default":
        collections.set_default(tid, uid, cid)
    elif action == "member":
        if target is None:
            raise PlaceError(_("Choose a member of this collection."))
        collections.set_member(
            tid, uid, cid, target, can_edit=request.form.get("rights") != "read"
        )
    elif action == "remove":
        if target is None:
            raise PlaceError(_("Choose a member of this collection."))
        collections.remove_member(tid, uid, cid, target)
    elif action == "leave":
        collections.remove_member(tid, uid, cid, uid)
        return True
    elif action == "transfer":
        if target is None:
            raise PlaceError(_("Choose a member of this collection."))
        collections.transfer_collection(tid, uid, cid, target)
    elif action == "invite":
        sharing.invite_email(
            tid,
            uid,
            cid,
            request.form.get("email", ""),
            can_edit=request.form.get("rights") != "read",
        )
        flash(
            _("Invitation saved. Ask your friend to sign in and open Places."),
            "success",
        )
    elif action == "revoke":
        invitation_id = request.form.get("invitation", type=db_id)
        if invitation_id is None:
            raise PlaceError(_("Invitation not found or expired."))
        sharing.revoke(tid, uid, cid, invitation_id)
    elif action in {"tag-rename", "tag-delete"}:
        tag_id = request.form.get("tag", type=db_id)
        if tag_id is None:
            raise PlaceError(_("Tag not found."))
        if action == "tag-rename":
            tags.rename_tag(tid, uid, cid, tag_id, request.form.get("name", ""))
        else:
            tags.delete_tag(tid, uid, cid, tag_id)
    elif action == "delete":
        collection_access(tid, uid, cid, managing=True)
        # Lock the parent before counting: a PostgreSQL insert referencing it
        # waits until this transaction commits rather than missing confirmation.
        PlaceCollection.query.filter_by(id=cid).with_for_update().one()
        expected = (
            request.form.get("places", type=list_index),
            request.form.get("visits", type=list_index),
        )
        if request.form.get("confirm") != "yes" or expected != collection_counts(cid):
            raise PlaceError(
                _("Review the collection totals and confirm its deletion.")
            )
        collections.delete_collection(tid, uid, cid)
        return True
    else:
        abort(400)
    return False


@places_bp.route("/collections/<int:collection_id>/settings", methods=["GET", "POST"])
@login_required
def collection_settings(collection_id: int) -> ResponseReturnValue:
    tid, uid = context()
    data = settings_context(tid, uid, collection_id)
    if request.method == "POST":
        try:
            gone = settings_action(tid, uid, collection_id)
            db.session.commit()
            return redirect(
                url_for("places.index")
                if gone
                else url_for("places.collection_settings", collection_id=collection_id)
            )
        except PlaceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            data = settings_context(tid, uid, collection_id)
            return render_template("places/collection_settings.html", **data), 400
    return render_template("places/collection_settings.html", **data)


@places_bp.route("/collections/<int:collection_id>/join", methods=["GET", "POST"])
@login_required
def join_collection(collection_id: int) -> ResponseReturnValue:
    tid, uid = context()
    if (
        collections_for_user(tid, uid)
        .filter(PlaceCollection.id == collection_id)
        .first()
        is not None
    ):
        return redirect(url_for("places.index", collection=collection_id))
    invitation = (
        sharing.pending_for_user(tid, uid)
        .filter_by(collection_id=collection_id)
        .first()
    )
    if invitation is None:
        abort(404)
    collection = db.session.get(PlaceCollection, collection_id)
    if request.method == "POST":
        try:
            sharing.accept(tid, uid, invitation.id)
            db.session.commit()
        except PlaceError:
            db.session.rollback()
            return abort(404)
        return redirect(url_for("places.index", collection=collection_id))
    return render_template(
        "places/join_collection.html", collection=collection, invitation=invitation
    )


from places import place_routes  # Register destination views after the blueprint.

__all__ = ["place_routes", "places_bp"]
