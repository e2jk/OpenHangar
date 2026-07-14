import contextlib
import json
import logging
import os
import uuid
from typing import Any
from datetime import (
    date as _date,
    datetime as _datetime,
    timedelta as _td,
    timezone as _tz,
)

from sqlalchemy import func  # pyright: ignore[reportMissingImports]
from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]

from flask_babel import gettext as _, ngettext  # pyright: ignore[reportMissingImports]
from werkzeug.utils import secure_filename  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Document,
    FlightCrew,
    FlightEntry,
    FstdType,
    GpsTrack,
    LogbookEntryType,
    LogbookImportBatch,
    LogbookImportMapping,
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsStatus,
    PersonalMinimumsTag,
    PilotLogbookEntry,
    PilotProfile,
    Reservation,
    ReservationStatus,
    TenantUser,
    db,
)
from utils import (  # pyright: ignore[reportMissingImports]
    login_required,
    require_pilot_access,
    user_can_access_aircraft,
)
from pilots.personal_minimums import (  # pyright: ignore[reportMissingImports]
    STARTERS,
    get_active_revision,
    recency_breaches,
)
from pilots.logbook_import import (  # pyright: ignore[reportMissingImports]
    TARGET_FIELDS,
    _norm,
    execute_import,
    link_entries_to_aircraft,
    parse_duration_value,
    parse_file,
    preview_rows,
    propose_mapping,
    type_hints,
)
from pilots.form_parsing import (  # pyright: ignore[reportMissingImports]
    _parse_date,
    apply_pilot_fields,
    parse_pilot_fields,
)

log = logging.getLogger(__name__)

pilots_bp = Blueprint("pilots", __name__)


def _current_user_id() -> int:
    return int(session["user_id"])


def _openaip_key() -> str | None:
    from models import AppSetting  # pyright: ignore[reportMissingImports]

    s = db.session.get(AppSetting, "openaip_api_key")
    return s.value if s and s.value else None


def _get_or_create_profile(user_id: int) -> PilotProfile:
    profile: PilotProfile | None = PilotProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = PilotProfile(user_id=user_id)
        db.session.add(profile)
        db.session.flush()
    return profile


def _check_logbook_milestone(entry: PilotLogbookEntry, uid: int) -> None:
    """Set one-shot session flags when a logbook milestone is crossed."""
    total = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).count()
    if total == 100:
        session["logbook_milestone"] = "100flights"
        flash(_("🎉 100th logbook entry — congratulations!"), "success")
        return

    night = float(entry.night_time or 0)
    if night > 0:
        prev_night = (
            db.session.query(func.count(PilotLogbookEntry.id))
            .filter(
                PilotLogbookEntry.pilot_user_id == uid,
                PilotLogbookEntry.id != entry.id,
                PilotLogbookEntry.night_time > 0,
            )
            .scalar()
            or 0
        )
        if prev_night == 0:
            session["logbook_milestone"] = "first_night"
            flash(_("🌙 First night flight logged — well done!"), "success")
            return

    dep = (entry.departure_place or "").strip().upper()
    arr = (entry.arrival_place or "").strip().upper()
    if dep and arr and dep != arr:
        prev_xc = (
            db.session.query(func.count(PilotLogbookEntry.id))
            .filter(
                PilotLogbookEntry.pilot_user_id == uid,
                PilotLogbookEntry.id != entry.id,
                PilotLogbookEntry.departure_place.isnot(None),
                PilotLogbookEntry.arrival_place.isnot(None),
                PilotLogbookEntry.departure_place != PilotLogbookEntry.arrival_place,
            )
            .scalar()
            or 0
        )
        if prev_xc == 0:
            session["logbook_milestone"] = "first_xc"
            flash(
                _("✈️ First cross-country flight logged — congratulations!"), "success"
            )


# ── Profile ───────────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/profile", methods=["GET", "POST"])
@login_required
@require_pilot_access
def profile() -> ResponseReturnValue:
    uid = _current_user_id()
    p = _get_or_create_profile(uid)

    if request.method == "POST":
        errors = []

        p.license_number = request.form.get("license_number", "").strip() or None

        medical_str = request.form.get("medical_expiry", "")
        medical, err = _parse_date(medical_str, "Medical expiry")
        if err:
            errors.append(err)
        else:
            p.medical_expiry = medical

        sep_str = request.form.get("sep_expiry", "")
        sep, err = _parse_date(sep_str, "SEP expiry")
        if err:
            errors.append(err)
        else:
            p.sep_expiry = sep

        solo_str = request.form.get("first_solo_date", "")
        solo, err = _parse_date(solo_str, _("First solo date"))
        if err:
            errors.append(err)
        else:
            p.first_solo_date = solo

        ppl_str = request.form.get("ppl_issue_date", "")
        ppl, err = _parse_date(ppl_str, _("PPL issue date"))
        if err:
            errors.append(err)
        else:
            p.ppl_issue_date = ppl

        if errors:
            for e in errors:
                flash(e, "danger")
            return (
                render_template(
                    "pilots/profile.html", profile=p, pilot_docs=[], currency=None
                ),
                422,
            )

        db.session.commit()
        flash(_("Profile saved."), "success")
        return redirect(url_for("pilots.profile"))

    from pilots.currency import currency_summary as _currency_summary  # pyright: ignore[reportMissingImports]

    pilot_entries = PilotLogbookEntry.query.filter_by(pilot_user_id=uid).all()
    currency = _currency_summary(p, pilot_entries)

    pilot_docs = (
        Document.query.filter_by(pilot_user_id=uid)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return render_template(
        "pilots/profile.html", profile=p, pilot_docs=pilot_docs, currency=currency
    )


_VALID_PER_PAGE = (10, 20, 50, 100)
_DEFAULT_PER_PAGE = 20


# ── Personal minimums ─────────────────────────────────────────────────────────


def _active_minimums_revision(uid: int) -> PersonalMinimumsRevision | None:
    return get_active_revision(uid)


def _draft_minimums_revision(uid: int) -> PersonalMinimumsRevision | None:
    revision: PersonalMinimumsRevision | None = (
        PersonalMinimumsRevision.query.filter_by(
            user_id=uid, status=PersonalMinimumsStatus.DRAFT
        ).first()
    )
    return revision


def _get_own_revision_or_404(revision_id: int, uid: int) -> PersonalMinimumsRevision:
    revision = db.session.get(PersonalMinimumsRevision, revision_id)
    if revision is None or revision.user_id != uid:
        abort(404)
    return revision


def _get_own_draft_section_or_404(section_id: int, uid: int) -> PersonalMinimumsSection:
    section = db.session.get(PersonalMinimumsSection, section_id)
    if (
        section is None
        or section.revision.user_id != uid
        or section.revision.status != PersonalMinimumsStatus.DRAFT
    ):
        abort(404)
    return section


def _get_own_draft_item_or_404(item_id: int, uid: int) -> PersonalMinimumsItem:
    item = db.session.get(PersonalMinimumsItem, item_id)
    if (
        item is None
        or item.section.revision.user_id != uid
        or item.section.revision.status != PersonalMinimumsStatus.DRAFT
    ):
        abort(404)
    return item


def _flew_or_reserved_today(uid: int) -> bool:
    today = _date.today()
    if PilotLogbookEntry.query.filter_by(pilot_user_id=uid, date=today).first():
        return True
    reservation = Reservation.query.filter(
        Reservation.pilot_user_id == uid,
        Reservation.status.in_(
            [ReservationStatus.PENDING, ReservationStatus.CONFIRMED]
        ),
        db.func.date(Reservation.start_dt) <= today,
        db.func.date(Reservation.end_dt) >= today,
    ).first()
    return reservation is not None


@pilots_bp.route("/pilot/minimums")
@login_required
@require_pilot_access
def minimums_view() -> ResponseReturnValue:
    uid = _current_user_id()
    active = _active_minimums_revision(uid)
    if active is not None:
        return render_template(
            "pilots/minimums_view.html", revision=active, is_own_draft=False
        )
    if _draft_minimums_revision(uid) is not None:
        return redirect(url_for("pilots.minimums_edit"))
    return render_template("pilots/minimums_start.html")


@pilots_bp.route("/pilot/minimums/create", methods=["POST"])
@login_required
@require_pilot_access
def minimums_create() -> ResponseReturnValue:
    uid = _current_user_id()
    if _draft_minimums_revision(uid) is not None:
        flash(_("You already have a draft in progress."), "warning")
        return redirect(url_for("pilots.minimums_edit"))
    starter = request.form.get("starter", "blank")
    if starter not in ("blank", "light", "full"):
        starter = "blank"
    next_number = (
        db.session.query(db.func.max(PersonalMinimumsRevision.revision_number))
        .filter_by(user_id=uid)
        .scalar()
        or 0
    ) + 1
    revision = PersonalMinimumsRevision(
        user_id=uid,
        revision_number=next_number,
        status=PersonalMinimumsStatus.DRAFT,
    )
    db.session.add(revision)
    db.session.flush()
    if starter in STARTERS:
        for s_order, (title, items) in enumerate(STARTERS[starter]):
            section = PersonalMinimumsSection(
                revision_id=revision.id, title=str(title), sort_order=s_order
            )
            db.session.add(section)
            db.session.flush()
            for i_order, (label, tag) in enumerate(items):
                db.session.add(
                    PersonalMinimumsItem(
                        section_id=section.id,
                        label=str(label),
                        value="",
                        semantic_tag=tag,
                        sort_order=i_order,
                    )
                )
    db.session.commit()
    flash(_("Draft created."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/history")
@login_required
@require_pilot_access
def minimums_history() -> ResponseReturnValue:
    uid = _current_user_id()
    revisions = (
        PersonalMinimumsRevision.query.filter_by(user_id=uid)
        .order_by(PersonalMinimumsRevision.revision_number.desc())
        .all()
    )
    return render_template("pilots/minimums_history.html", revisions=revisions)


@pilots_bp.route("/pilot/minimums/revision/<int:revision_id>")
@login_required
@require_pilot_access
def minimums_revision_detail(revision_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    revision = _get_own_revision_or_404(revision_id, uid)
    return render_template(
        "pilots/minimums_view.html",
        revision=revision,
        is_own_draft=(revision.status == PersonalMinimumsStatus.DRAFT),
    )


@pilots_bp.route("/pilot/minimums/revise", methods=["POST"])
@login_required
@require_pilot_access
def minimums_revise() -> ResponseReturnValue:
    uid = _current_user_id()
    active = _active_minimums_revision(uid)
    if active is None:
        flash(_("No active revision to revise yet."), "danger")
        return redirect(url_for("pilots.minimums_view"))
    if _draft_minimums_revision(uid) is not None:
        flash(_("You already have a draft in progress."), "warning")
        return redirect(url_for("pilots.minimums_edit"))
    next_number = (
        db.session.query(db.func.max(PersonalMinimumsRevision.revision_number))
        .filter_by(user_id=uid)
        .scalar()
        or 0
    ) + 1
    draft = PersonalMinimumsRevision(
        user_id=uid,
        revision_number=next_number,
        status=PersonalMinimumsStatus.DRAFT,
    )
    db.session.add(draft)
    db.session.flush()
    for section in active.sections:  # type: ignore[attr-defined]
        new_section = PersonalMinimumsSection(
            revision_id=draft.id, title=section.title, sort_order=section.sort_order
        )
        db.session.add(new_section)
        db.session.flush()
        for item in section.items:
            db.session.add(
                PersonalMinimumsItem(
                    section_id=new_section.id,
                    label=item.label,
                    value=item.value,
                    semantic_tag=item.semantic_tag,
                    numeric_value=item.numeric_value,
                    sort_order=item.sort_order,
                )
            )
    db.session.commit()
    flash(_("Draft created from your active revision."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/edit")
@login_required
@require_pilot_access
def minimums_edit() -> ResponseReturnValue:
    uid = _current_user_id()
    draft = _draft_minimums_revision(uid)
    if draft is None:
        return redirect(url_for("pilots.minimums_view"))
    return render_template(
        "pilots/minimums_edit.html", revision=draft, tags=PersonalMinimumsTag
    )


def _validate_tag_and_numeric(
    tag_raw: str, numeric_raw: str
) -> tuple[str | None, float | None, str | None]:
    """Return (tag_or_None, numeric_value_or_None, error_or_None)."""
    tag = tag_raw or None
    if tag and tag not in PersonalMinimumsTag.ALL:
        return None, None, str(_("Unrecognized tag."))
    if tag:
        try:
            return tag, float(numeric_raw), None
        except ValueError:
            return None, None, str(_("This tag requires a numeric value."))
    return None, None, None


@pilots_bp.route("/pilot/minimums/section/add", methods=["POST"])
@login_required
@require_pilot_access
def minimums_section_add() -> ResponseReturnValue:
    uid = _current_user_id()
    draft = _draft_minimums_revision(uid)
    if draft is None:
        abort(404)
    title = request.form.get("title", "").strip()
    if not title:
        flash(_("Section title is required."), "danger")
        return redirect(url_for("pilots.minimums_edit"))
    max_order = (
        db.session.query(db.func.max(PersonalMinimumsSection.sort_order))
        .filter_by(revision_id=draft.id)
        .scalar()
    )
    next_order = 0 if max_order is None else max_order + 1
    db.session.add(
        PersonalMinimumsSection(
            revision_id=draft.id, title=title, sort_order=next_order
        )
    )
    db.session.commit()
    flash(_("Section added."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/section/<int:section_id>/edit", methods=["POST"])
@login_required
@require_pilot_access
def minimums_section_edit(section_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    section = _get_own_draft_section_or_404(section_id, uid)
    title = request.form.get("title", "").strip()
    if not title:
        flash(_("Section title is required."), "danger")
        return redirect(url_for("pilots.minimums_edit"))
    section.title = title
    db.session.commit()
    flash(_("Section updated."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/section/<int:section_id>/delete", methods=["POST"])
@login_required
@require_pilot_access
def minimums_section_delete(section_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    section = _get_own_draft_section_or_404(section_id, uid)
    db.session.delete(section)
    db.session.commit()
    flash(_("Section removed."), "success")
    return redirect(url_for("pilots.minimums_edit"))


def _move_sort_order(
    model: Any, obj: Any, scope_field: str, scope_value: int, direction: str
) -> None:
    """Swap obj.sort_order with its neighbour within the given scope."""
    siblings = (
        model.query.filter_by(**{scope_field: scope_value})
        .order_by(model.sort_order)
        .all()
    )
    idx = next(i for i, s in enumerate(siblings) if s.id == obj.id)
    neighbour_idx = idx - 1 if direction == "up" else idx + 1
    if neighbour_idx < 0 or neighbour_idx >= len(siblings):
        return
    neighbour = siblings[neighbour_idx]
    obj.sort_order, neighbour.sort_order = neighbour.sort_order, obj.sort_order


@pilots_bp.route("/pilot/minimums/section/<int:section_id>/move-up", methods=["POST"])
@login_required
@require_pilot_access
def minimums_section_move_up(section_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    section = _get_own_draft_section_or_404(section_id, uid)
    _move_sort_order(
        PersonalMinimumsSection, section, "revision_id", section.revision_id, "up"
    )
    db.session.commit()
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/section/<int:section_id>/move-down", methods=["POST"])
@login_required
@require_pilot_access
def minimums_section_move_down(section_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    section = _get_own_draft_section_or_404(section_id, uid)
    _move_sort_order(
        PersonalMinimumsSection, section, "revision_id", section.revision_id, "down"
    )
    db.session.commit()
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/item/add", methods=["POST"])
@login_required
@require_pilot_access
def minimums_item_add() -> ResponseReturnValue:
    uid = _current_user_id()
    section_id = request.form.get("section_id", type=int)
    section = _get_own_draft_section_or_404(section_id, uid) if section_id else None
    if section is None:
        abort(404)
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip()
    tag_raw = request.form.get("tag", "").strip()
    numeric_raw = request.form.get("numeric_value", "").strip()
    if not label:
        flash(_("Item label is required."), "danger")
        return redirect(url_for("pilots.minimums_edit"))
    tag, numeric_value, error = _validate_tag_and_numeric(tag_raw, numeric_raw)
    if error:
        flash(error, "danger")
        return redirect(url_for("pilots.minimums_edit"))
    max_order = (
        db.session.query(db.func.max(PersonalMinimumsItem.sort_order))
        .filter_by(section_id=section.id)
        .scalar()
    )
    next_order = 0 if max_order is None else max_order + 1
    db.session.add(
        PersonalMinimumsItem(
            section_id=section.id,
            label=label,
            value=value or None,
            semantic_tag=tag,
            numeric_value=numeric_value,
            sort_order=next_order,
        )
    )
    db.session.commit()
    flash(_("Item added."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/item/<int:item_id>/edit", methods=["POST"])
@login_required
@require_pilot_access
def minimums_item_edit(item_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    item = _get_own_draft_item_or_404(item_id, uid)
    label = request.form.get("label", "").strip()
    value = request.form.get("value", "").strip()
    tag_raw = request.form.get("tag", "").strip()
    numeric_raw = request.form.get("numeric_value", "").strip()
    if not label:
        flash(_("Item label is required."), "danger")
        return redirect(url_for("pilots.minimums_edit"))
    tag, numeric_value, error = _validate_tag_and_numeric(tag_raw, numeric_raw)
    if error:
        flash(error, "danger")
        return redirect(url_for("pilots.minimums_edit"))
    item.label = label
    item.value = value or None
    item.semantic_tag = tag
    item.numeric_value = numeric_value
    db.session.commit()
    flash(_("Item updated."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/item/<int:item_id>/delete", methods=["POST"])
@login_required
@require_pilot_access
def minimums_item_delete(item_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    item = _get_own_draft_item_or_404(item_id, uid)
    db.session.delete(item)
    db.session.commit()
    flash(_("Item removed."), "success")
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/item/<int:item_id>/move-up", methods=["POST"])
@login_required
@require_pilot_access
def minimums_item_move_up(item_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    item = _get_own_draft_item_or_404(item_id, uid)
    _move_sort_order(PersonalMinimumsItem, item, "section_id", item.section_id, "up")
    db.session.commit()
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/item/<int:item_id>/move-down", methods=["POST"])
@login_required
@require_pilot_access
def minimums_item_move_down(item_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    item = _get_own_draft_item_or_404(item_id, uid)
    _move_sort_order(PersonalMinimumsItem, item, "section_id", item.section_id, "down")
    db.session.commit()
    return redirect(url_for("pilots.minimums_edit"))


@pilots_bp.route("/pilot/minimums/publish", methods=["GET", "POST"])
@login_required
@require_pilot_access
def minimums_publish() -> ResponseReturnValue:
    uid = _current_user_id()
    draft = _draft_minimums_revision(uid)
    if draft is None:
        flash(_("No draft to publish."), "danger")
        return redirect(url_for("pilots.minimums_view"))
    if not draft.sections:
        flash(_("Add at least one section before publishing."), "danger")
        return redirect(url_for("pilots.minimums_edit"))

    if request.method == "POST":
        active = _active_minimums_revision(uid)
        if active is not None:
            active.status = PersonalMinimumsStatus.SUPERSEDED
        totals = _compute_totals_sql(uid)
        draft.status = PersonalMinimumsStatus.ACTIVE
        draft.published_on = _date.today()
        draft.experience_hours = totals["total_flight_time"]
        db.session.commit()
        flash(_("Personal minimums published."), "success")
        return redirect(url_for("pilots.minimums_view"))

    return render_template(
        "pilots/minimums_publish_confirm.html",
        revision=draft,
        flew_or_reserved_today=_flew_or_reserved_today(uid),
    )


@pilots_bp.route("/pilot/minimums/delete-draft", methods=["POST"])
@login_required
@require_pilot_access
def minimums_delete_draft() -> ResponseReturnValue:
    uid = _current_user_id()
    draft = _draft_minimums_revision(uid)
    if draft is None:
        abort(404)
    db.session.delete(draft)
    db.session.commit()
    flash(_("Draft discarded."), "success")
    return redirect(url_for("pilots.minimums_view"))


@pilots_bp.route("/pilot/minimums/print")
@login_required
@require_pilot_access
def minimums_print() -> ResponseReturnValue:
    uid = _current_user_id()
    active = _active_minimums_revision(uid)
    if active is None:
        flash(_("No active personal minimums to print yet."), "warning")
        return redirect(url_for("pilots.minimums_view"))
    return render_template("pilots/minimums_print.html", revision=active)


# ── GPS tracks map ────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/tracks")
@login_required
@require_pilot_access
def pilot_tracks() -> ResponseReturnValue:
    from flask import url_for as _url_for

    uid = _current_user_id()
    entries = (
        PilotLogbookEntry.query.filter_by(pilot_user_id=uid)
        .filter(PilotLogbookEntry.gps_track_id.isnot(None))
        .order_by(PilotLogbookEntry.date.asc())
        .all()
    )
    track_rows = [
        {
            "date": str(e.date),
            "dep": e.departure_place or "",
            "arr": e.arrival_place or "",
            "time_str": f"{e.total_flight_time} h"
            if e.total_flight_time is not None
            else "",
            "view_url": _url_for(
                "aircraft.flight_detail",
                aircraft_id=e.flight.aircraft_id,
                flight_id=e.flight_id,
            )
            if e.flight_id and e.flight
            else _url_for("pilots.view_entry", entry_id=e.id),
            "geojson": e.gps_track.geojson if e.gps_track else None,
        }
        for e in entries
    ]

    return render_template(
        "pilots/flight_tracks.html",
        track_rows=track_rows,
        openaip_key=_openaip_key(),
    )


@pilots_bp.route("/pilot/tracks/animation.gif")
@login_required
@require_pilot_access
def pilot_tracks_gif() -> ResponseReturnValue:
    from utils import generate_tracks_gif, sort_tracks_oldest_first  # pyright: ignore[reportMissingImports]
    from flask import Response  # pyright: ignore[reportMissingImports]

    uid = _current_user_id()
    entries = (
        PilotLogbookEntry.query.filter_by(pilot_user_id=uid)
        .filter(PilotLogbookEntry.gps_track_id.isnot(None))
        .all()
    )
    track_rows = sort_tracks_oldest_first(
        [
            {
                "date": str(e.date),
                "dep": e.departure_place or "",
                "arr": e.arrival_place or "",
                "geojson": e.gps_track.geojson if e.gps_track else None,
            }
            for e in entries
        ]
    )
    portrait = request.args.get("orientation") == "portrait"
    hires = request.args.get("quality") == "hires"
    base_w, base_h = (480, 800) if portrait else (800, 480)
    mul = 2 if hires else 1
    canvas_w, canvas_h = base_w * mul, base_h * mul
    gif_bytes = generate_tracks_gif(
        track_rows,
        _openaip_key=_openaip_key(),
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        high_res=hires,
    )
    orient_sfx = "-portrait" if portrait else ""
    qual_sfx = "-hires" if hires else ""
    suffix = orient_sfx + qual_sfx
    return Response(
        gif_bytes,
        mimetype="image/gif",
        headers={
            "Content-Disposition": f'attachment; filename="my_tracks{suffix}.gif"'
        },
    )


# ── Logbook entry detail (read-only) ─────────────────────────────────────────


@pilots_bp.route("/pilot/logbook/<int:entry_id>/view")
@login_required
@require_pilot_access
def view_entry(entry_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)

    return render_template(
        "pilots/entry_detail.html",
        entry=entry,
        openaip_key=_openaip_key(),
        LogbookEntryType=LogbookEntryType,
    )


# ── Logbook list ──────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/logbook")
@login_required
@require_pilot_access
def logbook() -> ResponseReturnValue:
    uid = _current_user_id()
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    pp_raw = request.args.get("per_page", str(_DEFAULT_PER_PAGE))
    show_all = pp_raw == "all"
    per_page = (
        None
        if show_all
        else (
            int(pp_raw)
            if pp_raw.isdigit() and int(pp_raw) in _VALID_PER_PAGE
            else _DEFAULT_PER_PAGE
        )
    )

    q = PilotLogbookEntry.query.filter_by(pilot_user_id=uid)
    if order == "asc":
        q = q.order_by(
            PilotLogbookEntry.date.asc(),
            PilotLogbookEntry.departure_time.asc().nullslast(),
            PilotLogbookEntry.id.asc(),
        )
    else:
        q = q.order_by(
            PilotLogbookEntry.date.desc(),
            PilotLogbookEntry.departure_time.desc().nullslast(),
            PilotLogbookEntry.id.desc(),
        )

    if show_all:
        entries = q.all()
        pagination = None
    else:
        pagination = q.paginate(page=page, per_page=per_page, error_out=False)
        entries = pagination.items

    totals = _compute_totals_sql(uid)
    logbook_milestone = session.pop("logbook_milestone", None)

    active_minimums = _active_minimums_revision(uid)
    minimums_breaches = (
        recency_breaches(active_minimums, uid) if active_minimums else []
    )

    return render_template(
        "pilots/logbook.html",
        entries=entries,
        pagination=pagination,
        totals=totals,
        order=order,
        per_page=pp_raw,
        valid_per_page=_VALID_PER_PAGE,
        logbook_milestone=logbook_milestone,
        LogbookEntryType=LogbookEntryType,
        minimums_breaches=minimums_breaches,
    )


def _compute_totals_sql(pilot_user_id: int) -> dict[str, object]:
    """Aggregate totals over ALL entries for the pilot via a single SQL query."""
    row = (
        db.session.query(
            func.sum(PilotLogbookEntry.night_time),
            func.sum(PilotLogbookEntry.instrument_time),
            func.sum(PilotLogbookEntry.landings_day),
            func.sum(PilotLogbookEntry.landings_night),
            func.sum(PilotLogbookEntry.single_pilot_se),
            func.sum(PilotLogbookEntry.single_pilot_me),
            func.sum(PilotLogbookEntry.multi_pilot),
            func.sum(PilotLogbookEntry.function_pic),
            func.sum(PilotLogbookEntry.function_copilot),
            func.sum(PilotLogbookEntry.function_dual),
            func.sum(PilotLogbookEntry.function_instructor),
            func.sum(PilotLogbookEntry.fstd_duration),
        )
        .filter(PilotLogbookEntry.pilot_user_id == pilot_user_id)
        .one()
    )

    sp_se = round(float(row[4] or 0), 1)
    sp_me = round(float(row[5] or 0), 1)
    multi = round(float(row[6] or 0), 1)

    return {
        "night_time": round(float(row[0] or 0), 1),
        "instrument_time": round(float(row[1] or 0), 1),
        "landings_day": int(row[2] or 0),
        "landings_night": int(row[3] or 0),
        "single_pilot_se": sp_se,
        "single_pilot_me": sp_me,
        "multi_pilot": multi,
        # FSTD/simulator sessions are excluded from flight-time totals — they
        # are not flight hours, only single_pilot_se/me and multi_pilot count.
        "total_flight_time": round(sp_se + sp_me + multi, 1),
        "function_pic": round(float(row[7] or 0), 1),
        "function_copilot": round(float(row[8] or 0), 1),
        "function_dual": round(float(row[9] or 0), 1),
        "function_instructor": round(float(row[10] or 0), 1),
        "fstd_duration": round(float(row[11] or 0), 1),
    }


# ── New entry ────────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/logbook/new", methods=["GET", "POST"])
@login_required
@require_pilot_access
def new_entry() -> ResponseReturnValue:
    uid = _current_user_id()

    if request.method == "POST":
        values, errors = parse_pilot_fields(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pilots/entry_form.html",
                entry=None,
                form=request.form,
                action="new",
                openaip_key=_openaip_key(),
                LogbookEntryType=LogbookEntryType,
                FstdType=FstdType,
            ), 422
        entry = PilotLogbookEntry(pilot_user_id=uid)
        apply_pilot_fields(entry, values)
        db.session.add(entry)
        db.session.flush()
        _apply_gps_to_pilot_entry(entry)
        db.session.commit()
        _check_logbook_milestone(entry, uid)
        flash(_("Logbook entry saved."), "success")
        return redirect(url_for("pilots.logbook"))

    return render_template(
        "pilots/entry_form.html",
        entry=None,
        form={},
        action="new",
        openaip_key=_openaip_key(),
        LogbookEntryType=LogbookEntryType,
        FstdType=FstdType,
    )


# ── Edit entry ────────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/logbook/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
@require_pilot_access
def edit_entry(entry_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)

    if entry.flight_id:
        return redirect(url_for("flights.edit_flight", flight_id=entry.flight_id))

    if request.method == "POST":
        values, errors = parse_pilot_fields(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pilots/entry_form.html",
                entry=entry,
                form=request.form,
                action="edit",
                openaip_key=_openaip_key(),
                LogbookEntryType=LogbookEntryType,
                FstdType=FstdType,
            ), 422
        apply_pilot_fields(entry, values)
        _apply_gps_to_pilot_entry(entry)
        db.session.commit()
        flash(_("Logbook entry updated."), "success")
        return redirect(url_for("pilots.logbook"))

    return render_template(
        "pilots/entry_form.html",
        entry=entry,
        form={},
        action="edit",
        openaip_key=_openaip_key(),
        LogbookEntryType=LogbookEntryType,
        FstdType=FstdType,
    )


# ── Delete entry ──────────────────────────────────────────────────────────────


def _delete_upload(filename: str | None) -> None:
    if not filename:
        return
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    try:
        os.remove(os.path.join(folder, filename))
    except OSError:
        current_app.logger.debug(
            "Could not delete upload %s (already absent?)", filename
        )


@pilots_bp.route("/pilot/logbook/<int:entry_id>/delete", methods=["POST"])
@login_required
@require_pilot_access
def delete_entry(entry_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)

    flight_also_deleted = False
    if request.form.get("delete_flight_entry") == "1" and entry.flight_id:
        fe = db.session.get(FlightEntry, entry.flight_id)
        if fe and user_can_access_aircraft(fe.aircraft_id):
            _delete_upload(fe.flight_counter_photo)
            _delete_upload(fe.engine_counter_photo)
            db.session.delete(fe)
            flight_also_deleted = True

    db.session.delete(entry)
    db.session.commit()
    if flight_also_deleted:
        flash(_("Logbook entry and linked aircraft log entry deleted."), "success")
    else:
        flash(_("Logbook entry deleted."), "success")
    return redirect(url_for("pilots.logbook"))


# ── GPS track helper ──────────────────────────────────────────────────────────


def _apply_gps_to_pilot_entry(entry: PilotLogbookEntry) -> None:
    """Create or update the GpsTrack linked to a pilot logbook entry from form data."""
    f = request.form
    gps_geojson_raw = f.get("gps_geojson", "").strip()
    gps_filename = f.get("gps_filename", "").strip() or None
    if not gps_geojson_raw and not gps_filename:
        return

    geojson = None
    if gps_geojson_raw:
        with contextlib.suppress(
            ValueError
        ):  # malformed hidden field — GPS track simply not applied
            geojson = json.loads(gps_geojson_raw)

    def _parse_dt(raw: str) -> "_datetime | None":
        try:
            return _datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    block_off = _parse_dt(f.get("gps_block_off_utc", "").strip())
    block_on = _parse_dt(f.get("gps_block_on_utc", "").strip())
    dep = f.get("departure_place", "").strip().upper()[:4] or None
    arr = f.get("arrival_place", "").strip().upper()[:4] or None

    if entry.gps_track_id:
        gt = db.session.get(GpsTrack, entry.gps_track_id)
        if gt:
            if geojson is not None:
                gt.geojson = geojson
            if gps_filename:
                gt.source_filename = gps_filename
            if block_off:
                gt.block_off_utc = block_off
            if block_on:
                gt.block_on_utc = block_on
            return

    gt = GpsTrack(
        source_filename=gps_filename,
        block_off_utc=block_off,
        block_on_utc=block_on,
        departure_icao=dep,
        arrival_icao=arr,
        geojson=geojson,
    )
    db.session.add(gt)
    db.session.flush()
    entry.gps_track_id = gt.id


# ── Logbook Import ────────────────────────────────────────────────────────────

_IMPORT_SESSION_KEY = "logbook_import"
_ALLOWED_IMPORT_EXTS = {".csv", ".xlsx", ".xls"}
_MAX_IMPORT_BYTES = 10 * 1024 * 1024  # 10 MB


def _import_tmp_dir() -> str:
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    d = os.path.join(folder, "import_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _cleanup_previous_tmp(uid: int) -> None:
    """Delete any leftover temp import file for this user."""
    meta = session.get(_IMPORT_SESSION_KEY)
    if meta and meta.get("uid") == uid:
        tmp = meta.get("tmp_path")
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                current_app.logger.debug("cleanup tmp import file: %s", exc)
    session.pop(_IMPORT_SESSION_KEY, None)


@pilots_bp.route("/pilot/logbook/import", methods=["GET", "POST"])
@login_required
@require_pilot_access
def import_upload() -> ResponseReturnValue:
    uid = _current_user_id()

    if request.method == "GET":
        return render_template("pilots/import_upload.html")

    # ── POST: receive file, parse, present mapping page ───────────────────────
    uploaded = request.files.get("logbook_file")
    if not uploaded or not uploaded.filename:
        flash(_("Please select a file to upload."), "danger")
        return render_template("pilots/import_upload.html"), 422

    ext = os.path.splitext(uploaded.filename)[1].lower()
    if ext not in _ALLOWED_IMPORT_EXTS:
        flash(
            _("Unsupported format. Please upload a .csv or .xlsx file."),
            "danger",
        )
        return render_template("pilots/import_upload.html"), 422

    data = uploaded.read()
    if len(data) > _MAX_IMPORT_BYTES:
        flash(_("File too large (maximum 10 MB)."), "danger")
        return render_template("pilots/import_upload.html"), 422

    try:
        parsed = parse_file(data, uploaded.filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_template("pilots/import_upload.html"), 422

    # Save to a temp file so execute step can re-parse without re-upload
    _cleanup_previous_tmp(uid)
    safe_base = secure_filename(uploaded.filename) or "upload"
    tmp_name = f"import_{uid}_{uuid.uuid4().hex}_{safe_base}"
    tmp_path = os.path.join(_import_tmp_dir(), tmp_name)
    with open(tmp_path, "wb") as fh:
        fh.write(data)

    session[_IMPORT_SESSION_KEY] = {
        "uid": uid,
        "tmp_path": tmp_path,
        "original_filename": uploaded.filename,
        "norm_cols": parsed.norm_cols,
        "raw_cols": parsed.raw_cols,
        "fingerprint": parsed.fingerprint,
    }

    # Look up saved mappings for this pilot
    saved = LogbookImportMapping.query.filter_by(pilot_user_id=uid).all()
    proposal = propose_mapping(parsed, saved)

    preview = preview_rows(parsed, proposal.mapping, n=5)

    return render_template(
        "pilots/import_map.html",
        norm_cols=parsed.norm_cols,
        raw_cols=parsed.raw_cols,
        base_norm_cols=[_norm(r) for r in parsed.raw_cols],
        mapping=proposal.mapping,
        match_type=proposal.match_type,
        fuzzy_score=proposal.fuzzy_score,
        target_fields=TARGET_FIELDS,
        preview=preview,
        filename=uploaded.filename,
        type_hints=type_hints(parsed, proposal.mapping),
    )


@pilots_bp.route("/pilot/logbook/import/execute", methods=["POST"])
@login_required
@require_pilot_access
def import_execute() -> ResponseReturnValue:
    uid = _current_user_id()
    meta = session.get(_IMPORT_SESSION_KEY)

    if not meta or meta.get("uid") != uid:
        flash(_("Import session expired. Please upload the file again."), "warning")
        return redirect(url_for("pilots.import_upload"))

    tmp_path: str = meta["tmp_path"]
    original_filename: str = meta["original_filename"]
    norm_cols: list[str] = meta["norm_cols"]
    fingerprint: str = meta["fingerprint"]

    if not os.path.isfile(tmp_path):
        flash(_("Temporary file not found. Please upload the file again."), "warning")
        session.pop(_IMPORT_SESSION_KEY, None)
        return redirect(url_for("pilots.import_upload"))

    # Reconstruct mapping from form
    mapping: dict[str, str] = {}
    for col in norm_cols:
        val = request.form.get(f"mapping_{col}", "ignore").strip()
        mapping[col] = val if val in TARGET_FIELDS else "ignore"

    # Validate: at least 'date' must be mapped
    if "date" not in mapping.values():
        flash(_("You must map at least one column to 'Date'."), "danger")
        # Re-render the mapping page
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        try:
            parsed = parse_file(data, original_filename)
        except ValueError:
            session.pop(_IMPORT_SESSION_KEY, None)
            return redirect(url_for("pilots.import_upload"))
        preview = preview_rows(parsed, mapping, n=5)
        return render_template(
            "pilots/import_map.html",
            norm_cols=parsed.norm_cols,
            raw_cols=parsed.raw_cols,
            base_norm_cols=[_norm(r) for r in parsed.raw_cols],
            mapping=mapping,
            match_type="alias",
            fuzzy_score=0.0,
            target_fields=TARGET_FIELDS,
            preview=preview,
            filename=original_filename,
            type_hints=type_hints(parsed, mapping),
        ), 422

    # Parse opening balance
    opening_balance: dict[str, float | None] = {}
    ob_fields = [
        "single_pilot_se",
        "single_pilot_me",
        "multi_pilot",
        "night_time",
        "instrument_time",
        "function_pic",
        "function_copilot",
        "function_dual",
        "function_instructor",
    ]
    for f_name in ob_fields:
        raw = request.form.get(f"ob_{f_name}", "").strip()
        opening_balance[f_name] = parse_duration_value(raw) if raw else None

    # Re-parse the temp file
    with open(tmp_path, "rb") as fh:
        data = fh.read()
    try:
        parsed = parse_file(data, original_filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        session.pop(_IMPORT_SESSION_KEY, None)
        return redirect(url_for("pilots.import_upload"))

    # Create or reuse the mapping record
    saved_mappings = LogbookImportMapping.query.filter_by(pilot_user_id=uid).all()
    mapping_record: LogbookImportMapping | None = None
    for m in saved_mappings:
        if m.source_fingerprint == fingerprint:
            # Update the saved mapping with the user's potentially-refined choices
            m.column_mapping = json.dumps(mapping)
            mapping_record = m
            break
    if mapping_record is None:
        mapping_record = LogbookImportMapping(
            pilot_user_id=uid,
            source_fingerprint=fingerprint,
            column_mapping=json.dumps(mapping),
            source_columns=json.dumps(norm_cols),
            created_at=_datetime.now(_tz.utc),
        )
        db.session.add(mapping_record)
    db.session.flush()  # get mapping_record.id

    # Create the batch record (row counts filled in after execute)
    batch = LogbookImportBatch(
        pilot_user_id=uid,
        mapping_id=mapping_record.id,
        source_filename=original_filename,
        imported_at=_datetime.now(_tz.utc),
    )
    db.session.add(batch)
    db.session.flush()  # get batch.id

    result = execute_import(
        parsed=parsed,
        mapping=mapping,
        pilot_user_id=uid,
        batch_id=batch.id,
        opening_balance=opening_balance if any(opening_balance.values()) else None,
    )

    batch.row_count = result.imported
    batch.subtotal_count = result.subtotals
    batch.skipped_count = len(result.skipped)
    batch.has_opening_balance = result.has_opening_balance

    db.session.commit()

    # Link newly imported entries to aircraft log entries for managed aircraft
    new_entries = (
        PilotLogbookEntry.query.filter(
            PilotLogbookEntry.import_batch_id == batch.id,
            PilotLogbookEntry.aircraft_registration.isnot(None),
            PilotLogbookEntry.flight_id.is_(None),
        )
        .order_by(PilotLogbookEntry.date.asc(), PilotLogbookEntry.id.asc())
        .all()
    )
    ac_created = link_entries_to_aircraft(new_entries)
    if ac_created > 0:
        db.session.commit()

    # Clean up temp file and session
    try:
        os.remove(tmp_path)
    except OSError as exc:
        current_app.logger.debug("cleanup tmp import file: %s", exc)
    session.pop(_IMPORT_SESSION_KEY, None)

    flash(
        _(
            "Import complete: %(imported)d entries imported, %(subtotals)d subtotal rows "
            "skipped, %(skipped)d rows could not be parsed.",
            imported=result.imported,
            subtotals=result.subtotals,
            skipped=len(result.skipped),
        ),
        "success",
    )
    if ac_created > 0:
        flash(
            ngettext(
                "%(n)d aircraft log entry was also created for your managed aircraft.",
                "%(n)d aircraft log entries were also created for your managed aircraft.",
                ac_created,
                n=ac_created,
            ),
            "info",
        )
    if result.skipped:
        detail = "; ".join(f"row {r}: {reason}" for r, reason in result.skipped[:5])
        if len(result.skipped) > 5:
            detail += f" … and {len(result.skipped) - 5} more"
        flash(_("Skipped rows: %(detail)s", detail=detail), "warning")

    if result.parse_warnings:
        n = len(result.parse_warnings)
        examples = "; ".join(
            f"row {r}, {target}: {raw}"
            for r, _col, target, raw in result.parse_warnings[:3]
        )
        if n > 3:
            examples += f" … +{n - 3}"
        flash(
            ngettext(
                "One cell value could not be parsed and was imported as blank: %(examples)s",
                "%(n)d cell values could not be parsed and were imported as blank: %(examples)s",
                n,
                n=n,
                examples=examples,
            ),
            "warning",
        )

    if result.total_mismatch_warnings:
        n = len(result.total_mismatch_warnings)
        examples = "; ".join(
            _(
                "row %(row)d (source %(src).1f h, computed %(comp).1f h)",
                row=r,
                src=src,
                comp=comp,
            )
            for r, src, comp in result.total_mismatch_warnings[:3]
        )
        if n > 3:
            examples += f" … +{n - 3}"
        flash(
            ngettext(
                "One row has a total flight time that doesn't match the sum of its components — please review: %(examples)s",
                "%(n)d rows have a total flight time that doesn't match the sum of their components — please review: %(examples)s",
                n,
                n=n,
                examples=examples,
            ),
            "warning",
        )

    return redirect(url_for("pilots.import_history"))


@pilots_bp.route("/pilot/logbook/import/history")
@login_required
@require_pilot_access
def import_history() -> ResponseReturnValue:
    uid = _current_user_id()
    batches = (
        LogbookImportBatch.query.filter_by(pilot_user_id=uid)
        .order_by(LogbookImportBatch.imported_at.desc())
        .all()
    )
    return render_template("pilots/import_history.html", batches=batches)


@pilots_bp.route("/pilot/logbook/import/<int:batch_id>/rollback", methods=["POST"])
@login_required
@require_pilot_access
def import_rollback(batch_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    batch = db.session.get(LogbookImportBatch, batch_id)
    if not batch or batch.pilot_user_id != uid:
        abort(404)

    # Delete all entries belonging to this batch
    PilotLogbookEntry.query.filter_by(import_batch_id=batch_id).delete()
    db.session.delete(batch)
    db.session.commit()

    flash(
        ngettext(
            "Import deleted: one entry removed.",
            "Import deleted: all %(count)d entries removed.",
            batch.row_count,
            count=batch.row_count,
        ),
        "success",
    )
    return redirect(url_for("pilots.import_history"))


# ── Pilot GPS import (airplane-agnostic batch upload) ────────────────────────

_GPS_ALLOWED_EXTS = {".gpx", ".kml", ".csv"}
_GPS_MAX_BYTES = 20 * 1024 * 1024
_BLOCK_TOLERANCE_PILOT = _td(minutes=15)


def _pilot_gps_tmp_dir() -> str:
    from aircraft.routes import _gps_tmp_dir  # noqa: PLC0415

    return _gps_tmp_dir()


def _pilot_tenant_id(user_id: int) -> int | None:
    tu = TenantUser.query.filter_by(user_id=user_id).first()
    return tu.tenant_id if tu else None


def _pilot_match_segment(
    user_id: int, block_off: "_datetime", block_on: "_datetime"
) -> list["FlightEntry"]:
    """Find FlightEntry records the pilot is associated with that overlap block times."""
    tol = _BLOCK_TOLERANCE_PILOT

    crew_flight_ids = db.session.query(FlightCrew.flight_id).filter(
        FlightCrew.user_id == user_id, FlightCrew.flight_id.isnot(None)
    )
    logbook_flight_ids = db.session.query(PilotLogbookEntry.flight_id).filter(
        PilotLogbookEntry.pilot_user_id == user_id,
        PilotLogbookEntry.flight_id.isnot(None),
    )
    all_ids = crew_flight_ids.union(logbook_flight_ids).scalar_subquery()

    return FlightEntry.query.filter(  # type: ignore[no-any-return]
        FlightEntry.id.in_(all_ids),
        FlightEntry.block_off_utc.isnot(None),
        FlightEntry.block_on_utc.isnot(None),
        FlightEntry.block_off_utc < block_on + tol,
        FlightEntry.block_on_utc > block_off - tol,
    ).all()


def _pilot_seg_match_dict(matches: list[Any]) -> dict[str, Any]:
    """Summarise match results into fields stored on the segment dict."""
    if not matches:
        return {
            "matched_flight_id": None,
            "matched_flight_str": None,
            "matched_has_existing_track": False,
            "matched_aircraft_id": None,
            "matched_aircraft_reg": None,
            "matched_ambiguous": False,
            "matched_candidates": [],
        }
    candidates = [
        {
            "id": fe.id,
            "str": f"#{fe.id} — {fe.date} {fe.departure_icao} → {fe.arrival_icao}",
            "aircraft_id": fe.aircraft_id,
            "aircraft_reg": fe.aircraft.registration if fe.aircraft else "?",
            "has_existing_track": fe.gps_track_id is not None,
        }
        for fe in matches
    ]
    primary = matches[0]
    return {
        "matched_flight_id": primary.id,
        "matched_flight_str": candidates[0]["str"],
        "matched_has_existing_track": primary.gps_track_id is not None,
        "matched_aircraft_id": primary.aircraft_id,
        "matched_aircraft_reg": candidates[0]["aircraft_reg"],
        "matched_ambiguous": len(matches) > 1,
        "matched_candidates": candidates,
    }


@pilots_bp.route("/pilot/gps-import", methods=["GET", "POST"])
@login_required
@require_pilot_access
def pilot_gps_import_upload() -> ResponseReturnValue:
    uid = _current_user_id()
    tenant_id = _pilot_tenant_id(uid)
    tenant_aircraft = (
        Aircraft.query.filter_by(tenant_id=tenant_id)
        .order_by(Aircraft.registration)
        .all()
        if tenant_id
        else []
    )

    if request.method == "GET":
        return render_template(
            "pilots/gps_import_upload.html",
            tenant_aircraft=tenant_aircraft,
        )

    # ── POST: process uploaded files ──────────────────────────────────────────
    mode = request.form.get("mode", "agnostic")  # "one_aircraft" | "agnostic"
    files = request.files.getlist("gps_files")

    if not files or all(f.filename == "" for f in files):
        flash(_("Please select at least one GPS log file."), "warning")
        return render_template(
            "pilots/gps_import_upload.html", tenant_aircraft=tenant_aircraft
        )

    from aircraft.gps_import import parse_gps_file  # noqa: PLC0415

    tmp_dir = _pilot_gps_tmp_dir()
    parsed_meta: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped_empty = 0

    for f in files:
        if not f.filename:
            continue  # pragma: no cover – Werkzeug never yields empty-filename FileStorage objects
        ext = os.path.splitext(f.filename.lower())[1]
        if ext not in _GPS_ALLOWED_EXTS:
            errors.append(
                _(
                    "%(fn)s: unsupported file type (use .gpx, .kml, or .csv).",
                    fn=f.filename,
                )
            )
            continue
        data = f.read(_GPS_MAX_BYTES + 1)
        if len(data) > _GPS_MAX_BYTES:
            errors.append(_("%(fn)s: file too large (20 MB limit).", fn=f.filename))
            continue
        try:
            parsed = parse_gps_file(data, f.filename)
        except ValueError as exc:
            errors.append(_("%(fn)s: %(err)s", fn=f.filename, err=str(exc)))
            continue
        if parsed.classification == "empty":
            skipped_empty += 1
            continue
        uid_hex = uuid.uuid4().hex
        safe_name = f"{uid_hex}_{secure_filename(f.filename)}"
        tmp_path = os.path.join(tmp_dir, safe_name)
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        parsed_meta.append(
            {
                "tmp_path": tmp_path,
                "original_filename": f.filename,
                "format": parsed.format,
                "classification": parsed.classification,
                "trkpt_count": len(parsed.trackpoints),
                "hint_dep": parsed.hint_departure_icao,
                "hint_arr": parsed.hint_arrival_icao,
                "device_id": getattr(parsed, "device_id", None),
            }
        )

    for e in errors:
        flash(e, "danger")
    if skipped_empty:
        flash(
            ngettext(
                "%(n)s file skipped — no movement detected.",
                "%(n)s files skipped — no movement detected.",
                skipped_empty,
                n=skipped_empty,
            ),
            "info",
        )
    if not parsed_meta:
        flash(_("No valid GPS files to import."), "warning")
        return render_template(
            "pilots/gps_import_upload.html", tenant_aircraft=tenant_aircraft
        )

    if mode == "one_aircraft":
        aircraft_id = request.form.get("aircraft_id", type=int)
        if not aircraft_id:
            flash(_("Please select an aircraft."), "warning")
            return render_template(
                "pilots/gps_import_upload.html", tenant_aircraft=tenant_aircraft
            )
        session["gps_import"] = {
            "user_id": session["user_id"],
            "aircraft_id": aircraft_id,
            "files": parsed_meta,
            "skipped_empty": skipped_empty,
            "other_aircraft": False,
            "other_ac_make_model": "",
            "other_ac_reg": "",
        }
        session.modified = True
        return redirect(url_for("aircraft.gps_import_review", aircraft_id=aircraft_id))

    # agnostic mode
    session["pilot_gps_import"] = {
        "user_id": session["user_id"],
        "files": parsed_meta,
        "skipped_empty": skipped_empty,
    }
    session.modified = True
    return redirect(url_for("pilots.pilot_gps_import_review"))


@pilots_bp.route("/pilot/gps-import/review", methods=["GET"])
@login_required
@require_pilot_access
def pilot_gps_import_review() -> ResponseReturnValue:
    uid = _current_user_id()
    state = session.get("pilot_gps_import")
    if not state:
        flash(_("Session expired — please upload your GPS files again."), "warning")
        return redirect(url_for("pilots.pilot_gps_import_upload"))

    from aircraft.gps_import import (  # noqa: PLC0415
        detect_segments,
        merge_and_sort,
        parse_gps_file,
    )
    from aircraft.routes import (  # noqa: PLC0415
        _linked_pilot_entries,
        _segment_for_session,
        _segment_to_dict,
        _gps_tmp_dir,
    )

    file_metas = state["files"]
    all_parsed = []
    for meta in file_metas:
        try:
            with open(meta["tmp_path"], "rb") as fh:
                data = fh.read()
            parsed = parse_gps_file(data, meta["original_filename"])
            parsed.hint_departure_icao = meta.get("hint_dep")
            parsed.hint_arrival_icao = meta.get("hint_arr")
            all_parsed.append(parsed)
        except (OSError, ValueError):
            flash(
                _(
                    "Could not read %(fn)s — please upload again.",
                    fn=meta["original_filename"],
                ),
                "warning",
            )
            return redirect(url_for("pilots.pilot_gps_import_upload"))

    merged = merge_and_sort(all_parsed)
    hint_dep = next(
        (p.hint_departure_icao for p in all_parsed if p.hint_departure_icao), None
    )
    hint_arr = next(
        (p.hint_arrival_icao for p in all_parsed if p.hint_arrival_icao), None
    )
    segments = detect_segments(merged, hint_dep=hint_dep, hint_arr=hint_arr)
    full_segs = [_segment_to_dict(seg, i) for i, seg in enumerate(segments)]

    for seg in full_segs:
        block_off = _datetime.fromisoformat(seg["block_off_utc"])
        block_on = _datetime.fromisoformat(seg["block_on_utc"])
        matches = _pilot_match_segment(uid, block_off, block_on)
        seg.update(_pilot_seg_match_dict(matches))
        if seg.get("matched_flight_id") and not seg.get("matched_ambiguous"):
            seg["linked_pilot_entries"] = _linked_pilot_entries(
                seg["matched_flight_id"], uid
            )
        else:
            seg["linked_pilot_entries"] = []

    tmp_dir = _gps_tmp_dir()
    state["segments"] = [_segment_for_session(s, tmp_dir) for s in full_segs]
    session["pilot_gps_import"] = state
    session.modified = True

    tenant_id = _pilot_tenant_id(uid)
    tenant_aircraft = (
        Aircraft.query.filter_by(tenant_id=tenant_id)
        .order_by(Aircraft.registration)
        .all()
        if tenant_id
        else []
    )

    from models import AppSetting  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    tile_setting = db.session.get(AppSetting, "openaip_api_key")
    openaip_key = tile_setting.value if tile_setting and tile_setting.value else None

    return render_template(
        "pilots/gps_import_review.html",
        segments=full_segs,
        skipped_empty=state.get("skipped_empty", 0),
        confirmed_segments=state.get("confirmed_segments", {}),
        tenant_aircraft=tenant_aircraft,
        openaip_key=openaip_key,
    )


@pilots_bp.route("/pilot/gps-import/confirm-one", methods=["POST"])
@login_required
@require_pilot_access
def pilot_gps_import_confirm_one() -> ResponseReturnValue:
    import decimal as _dec  # noqa: PLC0415
    from aircraft.routes import _load_segment_geojson, _gps_cleanup  # noqa: PLC0415
    from aircraft.gps_import import round_flight_time  # noqa: PLC0415

    uid = _current_user_id()
    state = session.get("pilot_gps_import")
    if not state:
        flash(_("Session expired — please upload your GPS files again."), "warning")
        return redirect(url_for("pilots.pilot_gps_import_upload"))

    segments_data: list[dict[str, Any]] = state.get("segments", [])
    if not segments_data:
        flash(_("No segments to import."), "warning")
        return redirect(url_for("pilots.pilot_gps_import_upload"))

    try:
        seg_idx = int(request.form.get("seg_idx", ""))
    except (ValueError, TypeError):
        flash(_("Invalid segment index."), "danger")
        return redirect(url_for("pilots.pilot_gps_import_review"))

    if seg_idx < 0 or seg_idx >= len(segments_data):
        flash(_("Invalid segment index."), "danger")
        return redirect(url_for("pilots.pilot_gps_import_review"))

    confirmed = state.get("confirmed_segments", {})
    if str(seg_idx) in confirmed:
        flash(_("This segment has already been confirmed."), "info")
        return redirect(url_for("pilots.pilot_gps_import_review"))

    pilot_role = request.form.get("pilot_role", "pic")
    if pilot_role not in ("pic", "dual", "none"):
        pilot_role = "pic"

    # ── Skip ─────────────────────────────────────────────────────────────────
    if request.form.get("skip") == "1":
        confirmed[str(seg_idx)] = "skip"
        state["confirmed_segments"] = confirmed
        session["pilot_gps_import"] = state
        session.modified = True
        if len(confirmed) == len(segments_data):
            _gps_cleanup(state)
            session.pop("pilot_gps_import", None)
            imported = sum(1 for v in confirmed.values() if v != "skip")
            skipped_count = len(segments_data) - imported
            if imported > 0:
                flash(
                    ngettext(
                        "%(n)s flight imported successfully.",
                        "%(n)s flights imported successfully.",
                        imported,
                        n=imported,
                    ),
                    "success",
                )
            flash(
                ngettext(
                    "%(n)s segment skipped.",
                    "%(n)s segments skipped.",
                    skipped_count,
                    n=skipped_count,
                ),
                "info",
            )
            if imported > 0 and pilot_role in ("pic", "dual"):
                return redirect(url_for("pilots.logbook"))
            return redirect(url_for("pilots.logbook"))
        flash(_("Segment skipped."), "info")
        return redirect(url_for("pilots.pilot_gps_import_review"))

    # ── Confirm ───────────────────────────────────────────────────────────────
    seg = segments_data[seg_idx]
    file_metas = state.get("files", [])
    block_off = _datetime.fromisoformat(seg["block_off_utc"])
    block_on = _datetime.fromisoformat(seg["block_on_utc"])

    dep_icao = (
        request.form.get("dep_icao") or seg.get("departure_icao") or ""
    ).strip().upper()[:4] or "????"
    arr_icao = (
        request.form.get("arr_icao") or seg.get("arrival_icao") or ""
    ).strip().upper()[:4] or "????"
    nature = (request.form.get("nature") or "").strip()[:100] or None
    remarks = (request.form.get("remarks") or "").strip() or None

    geojson = _load_segment_geojson(seg)
    source_filename = (
        file_metas[0]["original_filename"] if len(file_metas) == 1 else None
    )
    device_id = next(
        (m.get("device_id") for m in file_metas if m.get("device_id")), None
    )

    create_pilot_entry = pilot_role in ("pic", "dual")
    matched_flight_id = seg.get("matched_flight_id")
    entry: FlightEntry | None = None
    gps_track: GpsTrack | None = None
    ac: Aircraft | None = None

    if matched_flight_id:
        # Link GPS track to the existing matched FlightEntry
        existing = db.session.get(FlightEntry, matched_flight_id)
        if existing:
            gps_track = GpsTrack(
                source_filename=source_filename,
                device_id=device_id,
                block_off_utc=block_off,
                block_on_utc=block_on,
                departure_icao=dep_icao,
                arrival_icao=arr_icao,
                geojson=geojson,
            )
            db.session.add(gps_track)
            db.session.flush()
            existing.gps_track_id = gps_track.id
            existing.block_off_utc = block_off
            existing.block_on_utc = block_on
            # Link track to other users' pilot logbook entries for this flight
            # (only when they have no existing GPS track — preserve their own data).
            for ple in PilotLogbookEntry.query.filter(
                PilotLogbookEntry.flight_id == existing.id,
                PilotLogbookEntry.pilot_user_id != uid,
                PilotLogbookEntry.gps_track_id.is_(None),
            ).all():
                ple.gps_track_id = gps_track.id
            db.session.flush()
            entry = existing
        else:
            matched_flight_id = None  # stale — fall through to new entry

    if not matched_flight_id:
        resolution = request.form.get("resolution", "other_aircraft")

        if resolution == "managed_aircraft":
            aircraft_id = request.form.get("aircraft_id", type=int)
            if aircraft_id:
                tenant_id = _pilot_tenant_id(uid)
                ac = (
                    Aircraft.query.filter_by(
                        id=aircraft_id, tenant_id=tenant_id
                    ).first()
                    if tenant_id
                    else None
                )

        if ac:
            # Create a new FlightEntry for the managed aircraft
            gps_track = GpsTrack(
                source_filename=source_filename,
                device_id=device_id,
                block_off_utc=block_off,
                block_on_utc=block_on,
                departure_icao=dep_icao,
                arrival_icao=arr_icao,
                geojson=geojson,
            )
            db.session.add(gps_track)
            db.session.flush()
            flight_time_h = round_flight_time(
                seg.get("flight_time_raw_h", 0),
                getattr(ac, "logbook_time_precision", "tenth_hour"),
            )
            entry = FlightEntry(
                aircraft_id=ac.id,
                date=block_off.date(),
                departure_icao=dep_icao,
                arrival_icao=arr_icao,
                departure_time=block_off.time().replace(tzinfo=None),
                arrival_time=block_on.time().replace(tzinfo=None),
                flight_time=_dec.Decimal(str(flight_time_h)),
                landing_count=seg.get("landing_count") or 0,
                nature_of_flight=nature,
                source="gps_import",
                block_off_utc=block_off,
                block_on_utc=block_on,
                gps_track_id=gps_track.id,
            )
            db.session.add(entry)
            db.session.flush()
        else:
            # Other / external aircraft — pilot-only, no FlightEntry
            if geojson:
                gps_track = GpsTrack(
                    source_filename=source_filename,
                    device_id=device_id,
                    block_off_utc=block_off,
                    block_on_utc=block_on,
                    departure_icao=dep_icao,
                    arrival_icao=arr_icao,
                    geojson=geojson,
                )
                db.session.add(gps_track)
                db.session.flush()
            create_pilot_entry = (
                True  # always create logbook entry for external aircraft
            )

    if create_pilot_entry:
        ac_type: str | None = None
        ac_reg: str | None = None
        ac_cat: str = "SEP"
        if ac:
            ac_type = f"{ac.make} {ac.model}".strip()
            ac_reg = ac.registration
            ac_cat = getattr(ac, "category", "SEP")
        elif entry and entry.aircraft:
            _rel_ac = entry.aircraft
            ac_type = f"{_rel_ac.make} {_rel_ac.model}".strip()
            ac_reg = _rel_ac.registration
            ac_cat = getattr(_rel_ac, "category", "SEP")
        else:
            other_reg = (request.form.get("other_reg") or "").strip().upper()
            other_mm = (request.form.get("other_make_model") or "").strip()
            ac_type = other_mm or None
            ac_reg = other_reg or None
            ac_cat = "SEP"

        flight_time_h = round_flight_time(seg.get("flight_time_raw_h", 0), "tenth_hour")
        single_pilot_se = (
            _dec.Decimal(str(flight_time_h)) if ac_cat in ("SEP", "SET", "") else None
        )
        single_pilot_me = (
            _dec.Decimal(str(flight_time_h)) if ac_cat in ("MEP", "MET") else None
        )

        from models import User as _User  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        _pilot_user = db.session.get(_User, uid)
        pilot_display_name = _pilot_user.display_name if _pilot_user else ""

        pentry = PilotLogbookEntry(
            pilot_user_id=uid,
            flight_id=entry.id if entry else None,
            date=block_off.date(),
            aircraft_type=ac_type,
            aircraft_registration=ac_reg,
            departure_place=dep_icao,
            departure_time=block_off.time().replace(tzinfo=None),
            arrival_place=arr_icao,
            arrival_time=block_on.time().replace(tzinfo=None),
            pic_name=pilot_display_name,
            single_pilot_se=single_pilot_se,
            single_pilot_me=single_pilot_me,
            function_pic=_dec.Decimal(str(flight_time_h))
            if pilot_role == "pic"
            else None,
            function_dual=_dec.Decimal(str(flight_time_h))
            if pilot_role == "dual"
            else None,
            landings_day=seg.get("landing_count") or 0,
            remarks=remarks,
            source="gps_import",
            gps_track_id=gps_track.id if gps_track else None,
        )
        db.session.add(pentry)

    db.session.commit()

    confirmed[str(seg_idx)] = entry.id if entry else 0
    state["confirmed_segments"] = confirmed
    session["pilot_gps_import"] = state
    session.modified = True

    all_handled = len(confirmed) == len(segments_data)
    if all_handled:
        _gps_cleanup(state)
        session.pop("pilot_gps_import", None)
        total = sum(1 for v in confirmed.values() if v != "skip")
        flash(
            ngettext(
                "%(n)s flight imported successfully.",
                "%(n)s flights imported successfully.",
                total,
                n=total,
            ),
            "success",
        )
        return redirect(url_for("pilots.logbook"))

    flash(_("Flight confirmed."), "success")
    return redirect(url_for("pilots.pilot_gps_import_review"))
