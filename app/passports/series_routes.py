"""Personal linked-edition navigation and explicit copy/review flows."""

import copy

from flask import abort, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from models import Passport, PassportParticipant, PassportSeries, db
from utils import current_membership, db_id, login_required

from passports import editions, passports_bp
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
    # Group history access will extend this through explicit series membership;
    # merely sharing a tenant or an edition does not expose personal collections.
    series = PassportSeries.query.filter_by(
        id=series_id, tenant_id=tenant_id, owner_user_id=user_id
    ).first_or_404()
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
