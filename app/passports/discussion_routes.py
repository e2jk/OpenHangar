"""Group-visible airfield comments; no private flight evidence is projected."""

from datetime import UTC, datetime

from extensions import _rate_limiting_disabled, limiter
from flask import (
    Response,
    abort,
    after_this_request,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import db
from utils import current_membership, db_id, login_required, page_number

from passports import comments, passports_bp
from passports.management import BuilderError, require_actor


def _comment_actor() -> str:
    return "passport-comments:" + str(session.get("user_id", "anonymous"))


@passports_bp.route(
    "/series/<int:series_id>/airfields/<int:airfield_id>/discussion",
    methods=["GET", "POST"],
)
@login_required
@limiter.shared_limit(
    "30 per hour",
    scope="passport-comments",
    methods=["POST"],
    key_func=_comment_actor,
    exempt_when=_rate_limiting_disabled,
)
def discussion(series_id: int, airfield_id: int) -> ResponseReturnValue:
    @after_this_request
    def private(response: Response) -> Response:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    membership = current_membership()
    if membership is None:
        abort(403)
    tenant_id, user_id = membership.tenant_id, session["user_id"]
    error, status = None, 200
    try:
        scope = comments.context(tenant_id, user_id, series_id, airfield_id)
    except BuilderError as exc:
        return abort(exc.status, description=str(exc))
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "add":
                comments.add(tenant_id, user_id, series_id, airfield_id, request.form)
            elif action in {"edit", "delete"}:
                comment_id = request.form.get("comment_id", type=db_id)
                if comment_id is None:
                    abort(404)
                comments.change(
                    tenant_id,
                    user_id,
                    series_id,
                    airfield_id,
                    comment_id,
                    request.form,
                    delete=action == "delete",
                )
            else:
                abort(400)
            db.session.commit()
            return redirect(
                url_for(
                    "passports.discussion", series_id=series_id, airfield_id=airfield_id
                )
            )
        except BuilderError as exc:
            db.session.rollback()
            if exc.status not in {400, 409}:
                return abort(exc.status, description=str(exc))
            error, status = str(exc), exc.status
        # A failed writer must not render a group that has revoked its access.
        try:
            scope = comments.context(tenant_id, user_id, series_id, airfield_id)
        except BuilderError as exc:
            return abort(exc.status, description=str(exc))
    filters = {
        name: request.args.get(name, "") for name in ("category", "after", "before")
    }
    try:
        query = comments.feed(scope, **filters)
    except BuilderError as exc:
        error, status = str(exc), exc.status
        filters = {"category": "", "after": "", "before": ""}
        query = comments.feed(scope)
    pagination = query.paginate(
        page=request.args.get("page", 1, type=page_number),
        per_page=20,
        max_per_page=20,
        error_out=False,
    )
    try:
        require_actor(tenant_id, user_id)
        can_post = True
    except BuilderError:
        can_post = False
    return make_response(
        render_template(
            "passports/discussion.html",
            scope=scope,
            pagination=pagination,
            category_labels={
                "airfield": _("Flying and airfield tips"),
                "nearby": _("Nearby recommendations"),
                "question": _("Question"),
            },
            filters=filters,
            error=error,
            can_post=can_post,
            user_id=user_id,
            today=datetime.now(UTC).date(),
            editing_id=request.form.get("comment_id", type=db_id)
            if error and request.form.get("action") == "edit"
            else None,
        ),
        status,
    )
