"""Personal linked-edition navigation and explicit copy/review flows."""

import copy

from flask import abort, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Passport,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    db,
)
from utils import current_membership, db_id, login_required

from passports import editions, groups, passports_bp
from passports.drafts import new_draft, save_draft
from passports.management import (
    METADATA_FIELDS,
    BuilderError,
    fingerprint,
    metadata_values,
)


def _scope() -> tuple[int, int]:
    membership = current_membership()
    if membership is None:
        abort(403)
    return membership.tenant_id, session["user_id"]


@passports_bp.route("/<int:passport_id>/series", methods=["GET", "POST"])
@login_required
def link_series(passport_id: int) -> ResponseReturnValue:
    from passports.routes import _owner

    passport = _owner(passport_id, archived_ok=True)
    error = None
    status = 200
    if request.method == "POST":
        try:
            series_id = request.form.get("series_id", type=db_id)
            if request.form.get("series_id") and series_id is None:
                abort(400)
            series = editions.link(
                passport,
                session["user_id"],
                series_id=series_id,
                title=request.form.get("title", ""),
            )
            db.session.commit()
            return redirect(url_for("passports.series_detail", series_id=series.id))
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    return render_template(
        "passports/link_series.html",
        passport=passport,
        series_list=PassportSeries.query.filter_by(
            tenant_id=passport.tenant_id, owner_user_id=session["user_id"]
        )
        .order_by(PassportSeries.title, PassportSeries.id)
        .all(),
        error=error,
    ), status


@passports_bp.route("/<int:passport_id>/next-edition", methods=["GET", "POST"])
@login_required
def next_edition(passport_id: int) -> ResponseReturnValue:
    from passports.routes import _owner, _render_builder

    source = _owner(passport_id, archived_ok=True)
    error = None
    status = 200
    if request.method == "POST":
        try:
            metadata = metadata_values(dict(request.form))
            token, draft = new_draft(source.tenant_id, session["user_id"])
            # This is a new selection draft, never an edition with copied evidence.
            from passports.eligibility import target_fields

            state = copy.deepcopy(draft.payload)
            state["metadata"] = {
                key: str(metadata[key] or "") for key in METADATA_FIELDS
            }
            state["selected"] = [
                "field:" + str(field["id"]) for field in target_fields(source)
            ]
            if source.source_url and source.source_retrieved_at:
                state["source"] = {
                    "url": source.source_url,
                    "retrieved_at": source.source_retrieved_at.isoformat(),
                }
            state["edition_copy"] = {
                "source_id": source.id,
                "fingerprint": fingerprint(source),
                "series_title": source.title,
                "copy_allocations": request.form.get("copy_allocations") == "yes",
            }
            save_draft(draft, state)
            db.session.commit()
            return _render_builder(token, draft, None)
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    return render_template(
        "passports/next_edition.html", passport=source, error=error
    ), status


@passports_bp.route("/series/<int:series_id>/")
@login_required
def series_detail(series_id: int) -> ResponseReturnValue:
    tenant_id, user_id = _scope()
    series = PassportSeries.query.filter_by(
        id=series_id, tenant_id=tenant_id
    ).first_or_404()
    if series.is_group:
        return _group_series(series, user_id)
    if series.owner_user_id != user_id:
        abort(404)
    passports = editions.ordered(
        Passport.query.filter_by(
            series_id=series.id, tenant_id=tenant_id, owner_user_id=user_id
        ).all()
    )
    accepted = {
        row.passport_id
        for row in PassportParticipant.query.filter(
            PassportParticipant.passport_id.in_([p.id for p in passports]),
            PassportParticipant.user_id == user_id,
            PassportParticipant.is_active.is_(True),
            PassportParticipant.accepted_at.is_not(None),
        ).all()
    }
    participating = [p for p in passports if p.id in accepted]
    current = editions.current_edition(participating)
    selected = current or editions.latest_edition(participating)
    requested = request.args.get("edition", "")
    if requested and requested != "all":
        try:
            edition_id = db_id(requested)
        except ValueError:
            abort(404)
        selected = next((p for p in passports if p.id == edition_id), None)
        if selected is None:
            abort(404)
    visible = passports if requested == "all" else ([selected] if selected else [])
    colours = {
        p.id: editions.COLOURS[i % len(editions.COLOURS)]
        for i, p in enumerate(passports)
    }
    comparison = editions.combined(passports, user_id, current=current, colours=colours)
    view = (
        comparison
        if requested == "all"
        else editions.combined(
            visible,
            user_id,
            current=current,
            oldest=request.args.get("priority") == "oldest",
            colours=colours,
            progress=comparison["progress"],
        )
    )
    if requested == "all" and request.args.get("priority") == "oldest":
        view = editions.combined(
            passports,
            user_id,
            current=current,
            oldest=True,
            colours=colours,
            progress=comparison["progress"],
        )
    # Only map-safe metadata leaves the server: no flight IDs, aircraft or notes.
    map_rows = [
        {
            key: float(value)
            if key in {"latitude", "longitude"} and value is not None
            else value
            for key, value in row.items()
        }
        for row in view["rows"]
    ]
    return render_template(
        "passports/series.html",
        series=series,
        group_view=False,
        passports=passports,
        current=current,
        selected=selected,
        all_editions=requested == "all",
        view=view,
        comparisons=comparison["comparisons"],
        map_rows=map_rows,
        oldest=request.args.get("priority") == "oldest",
        frequency=request.args.get("frequency") == "1",
    )


def _group_series(series: PassportSeries, user_id: int) -> ResponseReturnValue:
    try:
        include_history = request.args.get("history") == "1"
        passports, accepted = groups.visible_editions(
            series, user_id, include_history=include_history
        )
        participating = [p for p in passports if p.id in accepted]
        current = editions.current_edition(participating)
        selected = current or editions.latest_edition(participating)
        requested = request.args.get("edition", "")
        if requested and requested != "all":
            try:
                edition_id = db_id(requested)
            except ValueError:
                abort(404)
            selected = next((p for p in passports if p.id == edition_id), None)
            if selected is None:
                abort(404)
        visible = passports if requested == "all" else ([selected] if selected else [])
        all_group_editions = editions.ordered(
            Passport.query.filter_by(
                series_id=series.id, tenant_id=series.tenant_id, is_group=True
            ).all()
        )
        colours = {
            p.id: editions.COLOURS[i % len(editions.COLOURS)]
            for i, p in enumerate(all_group_editions)
        }
        view = groups.combined(
            series,
            visible,
            user_id,
            oldest=request.args.get("priority") == "oldest",
            current=current,
            colours=colours,
        )
        # Target comparisons apply to the selected authorised scope, not private linked passports.
        comparison = (
            view
            if visible == passports
            else groups.combined(
                series, passports, user_id, current=current, colours=colours
            )
        )
        map_rows = [
            {
                key: float(value)
                if key in {"latitude", "longitude"} and value is not None
                else value
                for key, value in row.items()
            }
            for row in view["rows"]
        ]
        return render_template(
            "passports/series.html",
            series=series,
            group_view=True,
            include_history=include_history,
            participating=accepted,
            passports=passports,
            current=current,
            selected=selected,
            all_editions=requested == "all",
            view=view,
            comparisons=comparison["comparisons"],
            map_rows=map_rows,
            oldest=request.args.get("priority") == "oldest",
            frequency=request.args.get("frequency") == "1",
        )
    except BuilderError as exc:
        return abort(exc.status)


@passports_bp.route("/<int:passport_id>/group", methods=["GET", "POST"])
@login_required
def group_settings(passport_id: int) -> ResponseReturnValue:
    tenant_id, user_id = _scope()
    # This consent page is also available to existing, accepted participants before
    # they acquire persistent series membership. It never enrols a nonparticipant.
    passport = Passport.query.filter_by(
        id=passport_id, tenant_id=tenant_id
    ).first_or_404()
    participant = (
        PassportParticipant.query.filter_by(
            passport_id=passport.id, user_id=user_id, is_active=True
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .first()
    )
    if participant is None:
        abort(404)
    member = (
        db.session.get(PassportSeriesMember, (passport.series_id, user_id))
        if passport.series_id
        else None
    )
    if member is not None and member.blocked:
        abort(404)
    owner = passport.owner_user_id == user_id
    if not owner and not passport.is_group:
        abort(404)
    error, status = None, 200
    if request.method == "POST":
        try:
            values = dict(request.form)
            series = (
                groups.enable(passport, user_id, values)
                if owner and not passport.is_group
                else (
                    db.session.get(PassportSeries, passport.series_id)
                    if passport.series_id is not None
                    else None
                )
            )
            if series is None:
                raise BuilderError(_("Group not found."), 404)
            if passport.is_group:
                groups.consent(passport, user_id, values)
            db.session.commit()
            return redirect(url_for("passports.series_detail", series_id=series.id))
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    return render_template(
        "passports/group_settings.html",
        passport=passport,
        participant=participant,
        error=error,
    ), status
