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
    send_file,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Passport,
    PassportArrivalExclusion,
    PassportDraft,
    PassportStampPhoto,
    PassportVisit,
    db,
)
from utils import (
    current_membership,
    db_id,
    list_index,
    login_required,
    require_pilot_access,
)

from passports import (
    builder,
    evidence,
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
from passports.eligibility import participant_for, passport_progress
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
    return render_template(
        "passports/index.html",
        passports=passports,
        summaries={
            passport.id: passport_progress(passport, session["user_id"])
            for passport in passports
        },
    )


@passports_bp.route("/<int:passport_id>/")
@login_required
def detail(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    progress = passport_progress(passport, session["user_id"])
    state = request.args.get("state", "all")
    rows = [
        row
        for row in progress["rows"]
        if state not in {"visited", "unvisited", "closed"}
        or (state == "visited" and row["visited"])
        or (state == "unvisited" and not row["visited"])
        or (state == "closed" and row.get("is_closed"))
    ]
    map_rows = [
        {
            key: float(row[key])
            if key in {"latitude", "longitude"} and row.get(key) is not None
            else row.get(key)
            for key in (
                "target_id",
                "name",
                "ident",
                "latitude",
                "longitude",
                "visited",
                "count",
                "is_closed",
            )
        }
        for row in rows
    ]
    can_manage = passport.owner_user_id == session.get("user_id")
    try:
        require_actor(passport.tenant_id, session["user_id"])
    except BuilderError:
        can_manage = False
    return render_template(
        "passports/detail.html",
        passport=passport,
        targets=rows,
        progress=progress,
        map_rows=map_rows,
        state=state,
        frequency=request.args.get("frequency") == "1",
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


@passports_bp.route(
    "/<int:passport_id>/airfields/<int:target_id>", methods=["GET", "POST"]
)
@login_required
def target_detail(passport_id: int, target_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    progress = passport_progress(passport, session["user_id"])
    target = next(
        (row for row in progress["rows"] if row["target_id"] == target_id), None
    )
    if target is None:
        abort(404)
    participant = participant_for(passport.id, session["user_id"])
    error = None
    status = 200
    if request.method == "POST":
        try:
            participant = evidence.writable_participant(passport, session["user_id"])
            action = request.form.get("action")
            if action == "visit":
                evidence.add_visit(passport, participant, target_id, request.form)
            elif action == "delete_visit":
                visit = PassportVisit.query.filter_by(
                    id=request.form.get("visit_id", type=db_id),
                    participant_id=participant.id,
                    target_id=target_id,
                ).first_or_404()
                db.session.delete(visit)
            elif action == "arrival":
                flight_id = request.form.get("flight_id", type=db_id)
                event = next(
                    (
                        event
                        for event in target["events"]
                        if event["kind"] == "arrival"
                        and event["flight_id"] == flight_id
                    ),
                    None,
                )
                if event is None:
                    abort(404)
                exclusion = PassportArrivalExclusion.query.filter_by(
                    participant_id=participant.id, flight_id=flight_id
                ).first()
                if request.form.get("exclude") == "yes":
                    if exclusion is None:
                        db.session.add(
                            PassportArrivalExclusion(
                                participant_id=participant.id, flight_id=flight_id
                            )
                        )
                elif exclusion is not None:
                    db.session.delete(exclusion)
            elif action == "photo":
                filename = evidence.save_photo(request.files.get("photo"))
                try:
                    evidence.replace_photo(passport, participant, target_id, filename)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    evidence.photo_path(filename).unlink(missing_ok=True)
                    raise
            elif action == "delete_photo":
                photo = PassportStampPhoto.query.filter_by(
                    participant_id=participant.id, target_id=target_id
                ).first_or_404()
                db.session.delete(photo)
            else:
                abort(400)
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
    photo = (
        PassportStampPhoto.query.filter_by(
            participant_id=participant.id, target_id=target_id
        ).first()
        if participant
        else None
    )
    can_record = False
    try:
        evidence.writable_participant(passport, session["user_id"])
        can_record = True
    except BuilderError:
        pass
    return _builder_response(
        render_template(
            "passports/target.html",
            passport=passport,
            target=target,
            supporting_flights=progress["supporting_flights"],
            photo=photo,
            can_record=can_record,
            error=error,
        ),
        status,
    )


@passports_bp.route("/<int:passport_id>/photos/<int:photo_id>")
@login_required
def stamp_photo(passport_id: int, photo_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    participant = participant_for(passport.id, session["user_id"])
    if participant is None:
        abort(404)
    photo = PassportStampPhoto.query.filter_by(
        id=photo_id, passport_id=passport.id, participant_id=participant.id
    ).first_or_404()
    response = send_file(
        evidence.photo_path(photo.filename), mimetype="image/jpeg", max_age=0
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@passports_bp.route("/<int:passport_id>/where-next")
@login_required
def where_next(passport_id: int) -> ResponseReturnValue:
    from models import PilotProfile
    from utils import accessible_aircraft

    from passports import discovery

    passport = require_passport(passport_id)
    uid = session["user_id"]
    aircraft = accessible_aircraft(passport.tenant_id).all()
    selected = None
    if request.args.get("aircraft"):
        selected = next(
            (
                ac
                for ac in aircraft
                if ac.id == request.args.get("aircraft", type=db_id)
            ),
            None,
        )
        if selected is None:
            abort(404)
    profile = PilotProfile.query.filter_by(user_id=uid).first()
    home = discovery.home_descriptor(
        selected, passport.tenant_id
    ) or discovery.home_descriptor(profile, passport.tenant_id)
    participant = participant_for(passport.id, uid)
    latest = discovery.latest_arrival(
        passport.tenant_id, uid, participant.id if participant else None
    )
    searches = discovery.origin_choices(
        passport.tenant_id, request.args.get("q", "")[:100]
    )
    origin_key = request.args.get("origin") or (home["key"] if home else "")
    if request.args.get("start") == "latest" and latest:
        origin_key = latest["key"]
    if request.args.get("start") == "home" and home:
        origin_key = home["key"]
    origin = None
    errors = []
    if origin_key:
        origin = builder.available_fields(passport.tenant_id).get(origin_key)
        if origin is None:
            errors.append(_("Choose a starting airfield from the search results."))
        elif origin.get("latitude") is None or origin.get("longitude") is None:
            errors.append(
                _("This starting airfield has no coordinates. Choose another.")
            )
    mode = request.args.get("mode", "distance")
    if mode not in {"distance", "time"}:
        mode = "distance"
    defaults = {
        "speed": str(selected.cruise_speed_knots)
        if selected and selected.cruise_speed_knots is not None
        else "",
        "limit": "100",
        "usable_fuel": "",
        "flow": str(selected.fuel_flow)
        if selected and selected.fuel_flow is not None
        else "",
        "reserve": "",
    }
    preference = discovery.reserve_preference(uid)
    if preference is not None:
        defaults["reserve"] = str(preference)
    values = {
        key: (request.args.get(key) or value)[:100] for key, value in defaults.items()
    }
    parsed = {}
    for key, label, maximum in [
        ("speed", _("cruise speed"), 9999.99),
        ("limit", _("distance/time limit"), 21600),
        ("usable_fuel", _("usable fuel"), 999999),
        ("flow", _("fuel flow"), 9999.99),
        ("reserve", _("reserve minutes"), 1440),
    ]:
        try:
            parsed[key] = discovery.positive(
                values[key], label, maximum, zero=key == "reserve"
            )
        except BuilderError as exc:
            parsed[key] = None
            errors.append(str(exc))
    limit = parsed["limit"] or 100
    speed = parsed["speed"]
    if mode == "time" and speed is None:
        errors.append(_("Enter a cruise speed for time mode, or use distance mode."))
    returning = request.args.get("trip") == "return"
    range_nm = discovery.theoretical_range(
        speed, parsed["usable_fuel"], parsed["flow"], parsed["reserve"], returning
    )
    range_enabled = request.args.get("range") == "1"
    if range_enabled and range_nm is None:
        errors.append(
            _(
                "Theoretical range is unknown. Enter usable fuel, fuel flow, speed and an explicit reserve leaving positive flight time. The distance/time filter still applies."
            )
        )
    progress = passport_progress(passport, uid)
    rows, missing = discovery.nearby(
        progress, origin, speed, limit, mode, range_nm if range_enabled else None
    )
    radius = (
        limit * speed / 60
        if mode == "time" and speed
        else limit
        if mode == "distance"
        else None
    )
    if radius is not None and range_enabled and range_nm is not None:
        radius = min(radius, range_nm)
    map_data = {
        "origin": {
            key: float(origin[key])
            if key in {"latitude", "longitude"} and origin.get(key) is not None
            else origin.get(key)
            for key in ("name", "latitude", "longitude")
        }
        if origin
        else None,
        "radius_nm": radius,
        "fields": [
            {
                key: float(row[key]) if key in {"latitude", "longitude"} else row[key]
                for key in ("name", "latitude", "longitude", "target_id", "distance_nm")
            }
            for row in rows
        ],
    }
    return render_template(
        "passports/where_next.html",
        passport=passport,
        aircraft=aircraft,
        selected=selected,
        home=home,
        latest=latest,
        searches=searches,
        origin_options=list(
            {
                field["key"]: field
                for field in [origin, home, latest, *searches]
                if field is not None
            }.values()
        ),
        origin=origin,
        origin_key=origin_key,
        rows=rows,
        missing=missing,
        values=values,
        mode=mode,
        range_enabled=range_enabled,
        returning=returning,
        range_nm=range_nm,
        map_data=map_data,
        errors=errors,
        preference=preference,
    )
