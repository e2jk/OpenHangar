"""Private passport views and owner-only builder/lifecycle routes."""

import copy
import json
from datetime import date
from typing import Any
from urllib.parse import urlencode

from extensions import _rate_limiting_disabled, limiter
from flask import (
    Response,
    abort,
    after_this_request,
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
    PassportSeries,
    PassportStampPhoto,
    PassportVisit,
    db,
)
from utils import (
    current_membership,
    db_id,
    flag_arg,
    list_index,
    login_required,
)

from passports import (
    builder,
    comments,
    discussion_routes,  # Register private group airfield discussions.
    editions,
    evidence,
    exports,  # Register the passport contributor to the common account export.
    groups,
    invitation_routes,  # Register edition admission views.
    lifecycle,  # Register transaction-safe file cleanup.
    logbook_routes,  # Register personal pilot-logbook activation.
    passports_bp,
    photo_sharing,
    printing,
    public_routes,  # Register standalone public passport representations.
    series_routes,  # Register linked-edition views.
    visit_routes,  # Register the minimal mobile visit form.
    voucher_routes,  # Register private voucher accounting.
    waypoints,
)
from passports.access import (
    accessible_group_series,
    accessible_passports,
    require_passport,
)
from passports.catalogue import AirfieldConflict, catalogue_choices
from passports.drafts import (
    MAX_PAYLOAD_BYTES,
    MAX_TARGETS,
    load_draft,
    new_draft,
    save_draft,
)
from passports.eligibility import participant_for, passport_progress, progress_many
from passports.imports import PUBLISHER_LIST_URL, source_values
from passports.management import (
    METADATA_FIELDS,
    BuilderError,
    owned_passport,
    require_actor,
    set_archived,
)

__all__ = [
    "discussion_routes",
    "exports",
    "invitation_routes",
    "lifecycle",
    "logbook_routes",
    "passports_bp",
    "public_routes",
    "series_routes",
    "visit_routes",
    "voucher_routes",
]


def _progress_map_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
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
                "state",
                "colour",
                "editions",
                "friends",
            )
        }
        for row in rows
    ]


@passports_bp.route("/")
@login_required
def index() -> ResponseReturnValue:
    passports = accessible_passports().order_by("title", "id").all()
    try:
        require_actor(*_actor(), creating=True)
        can_create = True
    except BuilderError:
        can_create = False
    user_id = session["user_id"]
    summaries = progress_many(passports, user_id)
    by_series: dict[int, list[Passport]] = {}
    cards: list[tuple[Passport, list[Passport]]] = []
    for passport in passports:
        if passport.series_id is None:
            cards.append((passport, []))
        else:
            by_series.setdefault(passport.series_id, []).append(passport)
    for series_passports in by_series.values():
        featured = editions.current_edition(
            series_passports
        ) or editions.latest_edition(series_passports)
        assert featured is not None
        history = [
            p for p in reversed(editions.ordered(series_passports)) if p != featured
        ]
        cards.append((featured, history))
    group_series = accessible_group_series().order_by("title", "id").all()
    series_titles = {series.id: series.title for series in group_series}
    series_titles.update(
        (series.id, series.title)
        for series in PassportSeries.query.filter(
            PassportSeries.id.in_(by_series),
            PassportSeries.owner_user_id == user_id,
        )
    )
    cards.sort(
        key=lambda card: (
            series_titles.get(card[0].series_id, card[0].title),
            card[0].id,
        )
    )
    return render_template(
        "passports/index.html",
        can_create=can_create,
        can_export=exports.account_has_data(*_actor(), passports),
        cards=cards,
        group_series=group_series,
        series_titles=series_titles,
        summaries=summaries,
        card_maps={p.id: _progress_map_rows(summaries[p.id]["rows"]) for p, _ in cards},
    )


@passports_bp.route("/export")
@login_required
@limiter.limit("2 per minute", exempt_when=_rate_limiting_disabled)
def export_account() -> ResponseReturnValue:
    from passports.exports import account_archive

    tenant_id, user_id = _actor()
    output = account_archive(tenant_id, user_id, accessible_passports().all())
    try:
        response = send_file(
            output,
            mimetype="application/zip",
            as_attachment=True,
            download_name="openhangar-passports.zip",
            max_age=0,
        )
    except Exception:
        output.close()
        raise
    response.call_on_close(output.close)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@passports_bp.route("/<int:passport_id>/")
@login_required
def detail(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    shared = _shared_view(passport)
    progress = (
        shared["progress"][passport.id]
        if shared
        else passport_progress(passport, session["user_id"])
    )
    shared_rows = {row["id"]: row for row in shared["rows"]} if shared else {}
    display_rows = [
        {**row, **shared_rows.get(row["id"], {})} for row in progress["rows"]
    ]
    stats = shared["stats"] if shared else None
    state = request.args.get("state", "all")
    # The participant filter narrows which airfields show; styling and "your"
    # progress still describe the viewer, whoever is selected.
    participant = request.args.get("participant", type=list_index) if stats else None
    rows = [
        row
        for row in display_rows
        if _state_matches(row, state, stats)
        and (
            participant is None
            or (stats is not None and participant in stats["by_target"][row["id"]])
        )
    ]
    download, waypoint_context = _waypoint_view(
        passport.id,
        rows,
        ("state", "participant", "frequency", "count"),
        endpoint="passports.detail",
    )
    if download is not None:
        return download
    map_rows = _progress_map_rows(rows)
    can_manage = passport.owner_user_id == session.get("user_id")
    try:
        require_actor(passport.tenant_id, session["user_id"])
    except BuilderError:
        can_manage = False
    try:
        evidence.writable_participant(passport, session["user_id"])
        can_record_visit = True
    except BuilderError:
        can_record_visit = False
    return render_template(
        "passports/detail.html",
        can_record_visit=can_record_visit,
        **waypoint_context,
        passport=passport,
        targets=rows,
        progress=progress,
        map_rows=map_rows,
        state=state,
        frequency=flag_arg("frequency"),
        can_manage=can_manage,
        group_view=shared is not None,
        count_scopes=shared["count_scopes"] if shared else [],
        count_scope=shared["count_scope"] if shared else "you",
        count_scope_name=shared["count_scope_name"] if shared else "",
        group_stats=stats,
        participant=participant,
        today=date.today(),
    )


@passports_bp.route("/<int:passport_id>/print")
@login_required
def print_passport(passport_id: int) -> ResponseReturnValue:
    @after_this_request
    def private(response: Response) -> Response:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    passport = require_passport(passport_id)
    try:
        view = printing.personal(
            passport, session["user_id"], request.args.getlist("photo")
        )
    except BuilderError as exc:
        return abort(exc.status, description=str(exc))
    return render_template("passports/print.html", view=view)


def _waypoint_view(
    passport_id: int,
    rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    endpoint: str,
) -> tuple[Response | None, dict[str, Any]]:
    selection = waypoints.select(rows)
    fmt = request.args.get("download")
    if fmt is not None:
        if fmt not in waypoints.FORMATS:
            abort(400)
        response = Response(
            waypoints.document(selection, fmt), mimetype=waypoints.FORMATS[fmt]
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="passport-{passport_id}-unvisited.{fmt}"'
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response, {}
    # Never pass arbitrary query keys to url_for: its reserved options can
    # change the URL's host or scheme. These are the view's own input names.
    query = {key: request.args[key][:100] for key in keys if key in request.args}
    links = (
        {
            fmt: url_for(endpoint, passport_id=passport_id)
            + "?"
            + urlencode({**query, "download": fmt})
            for fmt in waypoints.FORMATS
        }
        if selection.points
        else {}
    )
    return None, {"waypoint_links": links, "omitted_waypoints": selection.omitted}


def _state_matches(
    row: dict[str, Any], state: str, stats: dict[str, Any] | None
) -> bool:
    if state == "visited":
        return bool(row["visited"])
    if state == "unvisited":
        return not row["visited"]
    if state == "closed":
        return bool(row.get("is_closed"))
    if stats is not None and state in {"anyone", "everyone", "nobody"}:
        visitors = stats["by_target"][row["id"]]
        if state == "anyone":
            return bool(visitors)
        if state == "nobody":
            return not visitors
        everyone = {p["key"] for p in stats["participants"]}
        return bool(everyone) and visitors >= everyone
    return True


def _shared_view(passport: Passport) -> dict[str, Any] | None:
    if not passport.is_group:
        return None
    series = db.session.get(PassportSeries, passport.series_id)
    if series is None:
        return abort(404)
    try:
        return groups.combined(
            series,
            [passport],
            session["user_id"],
            current=passport,
            scope=groups.parse_count_scope(request.args.get("count")),
        )
    except BuilderError as exc:
        return abort(exc.status)


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
        return abort(exc.status, description=str(exc))


def _render_builder(
    token: str,
    draft: PassportDraft,
    passport: Passport | None,
    error: str | None = None,
    status: int = 200,
) -> ResponseReturnValue:
    state = draft.payload
    fields = builder.selected_fields(
        draft.tenant_id, state, allow_missing=True, user_id=draft.user_id
    )
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

    if state.get("import_preview"):
        preview = state["import_preview"]
        matched_fields = builder.selected_fields(
            draft.tenant_id,
            {**state, "selected": preview["matched"]},
            allow_missing=True,
            user_id=draft.user_id,
        )
        return _builder_response(
            render_template(
                "passports/import_review.html",
                token=token,
                draft=draft,
                passport=passport,
                state=state,
                preview=preview,
                source_retrieved_at=source_values({"source": preview["source"]})[
                    "source_retrieved_at"
                ],
                fields=matched_fields,
                map_fields=matched_fields,
                map_selected=preview["matched"],
                error=error,
            ),
            status,
        )
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
    matches = builder.matching_fields(draft.tenant_id, state, user_id=draft.user_id)
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
            publisher_list_url=PUBLISHER_LIST_URL,
            source_retrieved_at=source_values(state)["source_retrieved_at"],
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
        return abort(exc.status, description=str(exc))
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
        try:
            draft = load_draft(
                token, tenant_id, user_id, passport.id if passport else None, revision
            )
        except BuilderError as recovery_error:
            return _builder_response(
                render_template(
                    "passports/draft_error.html",
                    message=str(recovery_error),
                    restart_url=request.path,
                ),
                recovery_error.status,
            )
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


def _import_limit_exempt() -> bool:
    return _rate_limiting_disabled() or request.form.get("action") != "preview_import"


@passports_bp.route("/new", methods=["GET", "POST"])
@login_required
@limiter.shared_limit(
    "5 per minute",
    scope="passport-source-import",
    methods=["POST"],
    exempt_when=_import_limit_exempt,
)
def new_passport() -> ResponseReturnValue:
    return _builder_page(None)


@passports_bp.route("/<int:passport_id>/edit", methods=["GET", "POST"])
@login_required
@limiter.shared_limit(
    "5 per minute",
    scope="passport-source-import",
    methods=["POST"],
    exempt_when=_import_limit_exempt,
)
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


def _share_photo(passport: Passport, target_id: int) -> None:
    """Share an own stamp photo with the group, or withdraw it at any time."""
    uid = session["user_id"]
    share = request.form.get("share") == "yes"
    # Withdrawing stays possible on an archived edition; sharing does not.
    participant = (
        evidence.writable_participant(passport, uid)
        if share
        else participant_for(passport.id, uid)
    )
    if participant is None or not passport.is_group:
        abort(404)
    photo = PassportStampPhoto.query.filter_by(
        participant_id=participant.id, target_id=target_id
    ).first_or_404()
    if share and not participant.share_visits:
        raise BuilderError(
            _("Share your visits with this group before sharing photos."), 409
        )
    photo_sharing.set_shared(photo, share)
    db.session.commit()


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
            if request.form.get("action") == "share_photo":
                _share_photo(passport, target_id)
                return redirect(
                    url_for(
                        "passports.target_detail",
                        passport_id=passport.id,
                        target_id=target_id,
                    )
                )
            participant = evidence.writable_participant(passport, session["user_id"])
            action = request.form.get("action")
            if action == "visit":
                evidence.add_visit(passport, participant, target_id, request.form)
            elif action == "edit_visit":
                visit = PassportVisit.query.filter_by(
                    id=request.form.get("visit_id", type=db_id),
                    passport_id=passport.id,
                    participant_id=participant.id,
                    target_id=target_id,
                    evidence_kind="manual",
                ).first_or_404()
                for name, value in evidence.visit_values(
                    passport, participant, request.form
                ).items():
                    setattr(visit, name, value)
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
        can_record = False
    discussion_comments = []
    if passport.is_group:
        try:
            scope = comments.context(
                passport.tenant_id, session["user_id"], passport.series_id, target["id"]
            )
            discussion_comments = comments.feed(scope).limit(3).all()
        except BuilderError as exc:
            return abort(exc.status, description=str(exc))
    from places import airfields, passport_links

    uid = session["user_id"]
    place_radius, place_radius_error = passport_links.search_radius(
        request.args.get("radius", "15")
    )
    linked_places = airfields.linked_airfields(
        passport.tenant_id, uid, airfield_id=target["id"]
    )
    nearby_places = passport_links.nearby_places(
        passport.tenant_id, uid, {target["id"]: target}, place_radius
    )
    return _builder_response(
        render_template(
            "passports/target.html",
            linked_places=linked_places,
            nearby_places=nearby_places,
            place_radius=place_radius,
            place_radius_error=place_radius_error,
            discussion_comments=discussion_comments,
            passport=passport,
            target=target,
            friends=next(
                (
                    row["friends"]
                    for row in (_shared_view(passport) or {}).get("rows", [])
                    if row["id"] == target["id"]
                ),
                [],
            ),
            supporting_flights=progress["supporting_flights"],
            photo=photo,
            photo_shared=(
                photo is not None
                and participant is not None
                and passport.is_group
                and photo_sharing.is_shared(photo, participant)
            ),
            can_share_photo=(
                can_record
                and passport.is_group
                and participant is not None
                and participant.share_visits
            ),
            friends_photos=photo_sharing.friends_photos(
                passport, target_id, session["user_id"]
            ),
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
    photo = (
        PassportStampPhoto.query.filter_by(
            id=photo_id, passport_id=passport.id, participant_id=participant.id
        ).first()
        if participant is not None
        else None
    )
    if photo is None:
        # A friend's photo only while it is shared with this edition's group.
        photo = photo_sharing.visible_photo(passport, photo_id, session["user_id"])
    if photo is None:
        abort(404)
    try:
        response = send_file(
            evidence.photo_path(photo.filename), mimetype="image/jpeg", max_age=0
        )
    except FileNotFoundError:
        # A stale reference or concurrent replacement must not become a 500.
        return abort(404)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@passports_bp.route("/<int:passport_id>/where-next")
@login_required
def where_next(passport_id: int) -> ResponseReturnValue:
    """Moved to Explore (Phase 42f); old links and bookmarks keep working."""
    from explore.routes import redirect_from_passport

    return redirect_from_passport(passport_id)
