"""Private passport views and owner-only builder/lifecycle routes."""

import copy
import json

from flask import (
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    PassportDraft,
    db,
)
from utils import current_membership, list_index, login_required, require_pilot_access

from passports import (
    builder,
    lifecycle,  # Register transaction-safe file cleanup.
    passports_bp,
)
from passports.access import accessible_passports, require_passport
from passports.catalogue import AirfieldConflict, catalogue_choices
from passports.drafts import (
    MAX_PAYLOAD_BYTES,
    MAX_TARGETS,
    load_draft,
    new_draft,
    save_draft,
)
from passports.management import (
    METADATA_FIELDS,
    BuilderError,
    owned_passport,
    require_actor,
    set_archived,
)

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
    can_manage = passport.owner_user_id == session.get("user_id")
    try:
        require_actor(passport.tenant_id, session["user_id"])
    except BuilderError:
        can_manage = False
    return render_template(
        "passports/detail.html",
        passport=passport,
        targets=targets,
        can_manage=can_manage,
    )


def _actor() -> tuple[int, int]:
    membership = current_membership()
    if membership is None:
        abort(403)
    return membership.tenant_id, session["user_id"]


def _owner(passport_id: int, *, archived_ok: bool = False) -> Passport:
    tenant_id, user_id = _actor()
    try:
        return owned_passport(tenant_id, user_id, passport_id, archived_ok=archived_ok)
    except BuilderError as exc:
        abort(exc.status, description=str(exc))


def _render_builder(
    token: str,
    draft: PassportDraft,
    passport: Passport | None,
    error: str | None = None,
    status: int = 200,
) -> ResponseReturnValue:
    state = draft.payload
    fields = builder.selected_fields(draft.tenant_id, state, allow_missing=True)
    choices = catalogue_choices()
    type_labels = {
        "small_airport": _("Small airfields"),
        "medium_airport": _("Medium airports"),
        "large_airport": _("Large airports"),
        "heliport": _("Heliports"),
        "seaplane_base": _("Seaplane bases"),
        "balloonport": _("Balloon ports"),
        "closed": _("Closed airfields"),
    }

    if state["review"]:
        impact = builder.review(draft.tenant_id, draft.user_id, passport, draft, state)
        return _builder_response(
            render_template(
                "passports/review.html",
                token=token,
                draft=draft,
                passport=passport,
                state=state,
                impact=impact,
                error=error,
                map_fields=fields,
            ),
            status,
        )
    matches = builder.matching_fields(draft.tenant_id, state)
    return _builder_response(
        render_template(
            "passports/builder.html",
            token=token,
            draft=draft,
            passport=passport,
            state=state,
            fields=fields,
            matches=matches[: builder.VISIBLE_LIMIT],
            match_count=len(matches),
            choices=choices,
            type_labels=type_labels,
            error=error,
            map_fields=list(
                {
                    field["key"]: field
                    for field in matches[: builder.VISIBLE_LIMIT] + fields
                }.values()
            ),
        ),
        status,
    )


def _builder_response(html: str, status: int) -> ResponseReturnValue:
    response = make_response(html, status)
    response.headers["X-OH-Passport-Builder"] = "1"
    return response


def _builder_page(passport: Passport | None) -> ResponseReturnValue:
    tenant_id, user_id = _actor()
    try:
        require_actor(tenant_id, user_id, creating=passport is None)
    except BuilderError as exc:
        abort(exc.status, description=str(exc))
    if request.method == "GET":
        token, draft = new_draft(tenant_id, user_id, passport)
        db.session.commit()
        return _render_builder(token, draft, passport)
    token = request.form.get("draft", "")
    revision = request.form.get("revision", type=list_index)
    if revision is None:
        abort(400)
    try:
        draft = load_draft(
            token, tenant_id, user_id, passport.id if passport else None, revision
        )
    except BuilderError as exc:
        return _builder_response(
            render_template(
                "passports/draft_error.html", message=str(exc), restart_url=request.path
            ),
            exc.status,
        )
    state = copy.deepcopy(draft.payload)
    try:
        _state, saved = builder.advance(
            tenant_id, user_id, passport, draft, request.form, state=state
        )
        if saved is not None:
            passport_id = saved.id
            db.session.commit()
            flash(_("Passport saved."), "success")
            return redirect(url_for("passports.detail", passport_id=passport_id))
        db.session.commit()
        return _render_builder(token, draft, passport)
    except (BuilderError, AirfieldConflict) as exc:
        db.session.rollback()
        draft = load_draft(token, tenant_id, user_id, passport.id if passport else None)
        # Keep typed metadata and pasted codes, but never retain unvalidated
        # selections/actions or an oversized request payload after an error.
        if (
            len(state["selected"]) > MAX_TARGETS
            or len(json.dumps(state).encode()) > MAX_PAYLOAD_BYTES
        ):
            state = copy.deepcopy(draft.payload)
        for key in METADATA_FIELDS:
            if key in request.form:
                state["metadata"][key] = request.form[key][:10000]
        if "codes" in request.form:
            state["codes"] = request.form["codes"][:50000]
        state["review"] = None
        if len(json.dumps(state).encode()) > MAX_PAYLOAD_BYTES:
            state = copy.deepcopy(draft.payload)
            state["review"] = None
        save_draft(draft, state)
        db.session.commit()
        message = (
            str(exc)
            if isinstance(exc, BuilderError)
            else _(
                "An airfield code conflicts with an existing identity. Review or remove that selection."
            )
        )
        return _render_builder(
            token,
            draft,
            passport,
            message,
            exc.status if isinstance(exc, BuilderError) else 409,
        )


@passports_bp.route("/new", methods=["GET", "POST"])
@login_required
@require_pilot_access
def new_passport() -> ResponseReturnValue:
    return _builder_page(None)


@passports_bp.route("/<int:passport_id>/edit", methods=["GET", "POST"])
@login_required
def edit_passport(passport_id: int) -> ResponseReturnValue:
    return _builder_page(_owner(passport_id))


@passports_bp.route("/<int:passport_id>/archive", methods=["GET", "POST"])
@login_required
def archive_passport(passport_id: int) -> ResponseReturnValue:
    passport = _owner(passport_id, archived_ok=True)
    if request.method == "POST":
        if request.form.get("confirm") != "yes":
            abort(400)
        set_archived(passport, request.form.get("operation") != "reopen")
        db.session.commit()
        return redirect(url_for("passports.detail", passport_id=passport.id))
    return render_template("passports/confirm.html", passport=passport, deleting=False)


@passports_bp.route("/<int:passport_id>/delete", methods=["GET", "POST"])
@login_required
def delete_passport(passport_id: int) -> ResponseReturnValue:
    passport = _owner(passport_id, archived_ok=True)
    if request.method == "POST":
        if request.form.get("confirm") != "yes":
            abort(400)
        db.session.delete(passport)
        db.session.commit()
        flash(_("Passport deleted."), "success")
        return redirect(url_for("passports.index"))
    return render_template("passports/confirm.html", passport=passport, deleting=True)
