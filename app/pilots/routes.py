import json
import logging
import os
import uuid
from datetime import (
    date as _date,
    datetime as _datetime,
    time as _time,
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
    Document,
    GpsTrack,
    LogbookImportBatch,
    LogbookImportMapping,
    PilotLogbookEntry,
    PilotProfile,
    db,
)
from utils import login_required, require_pilot_access  # pyright: ignore[reportMissingImports]
from pilots.logbook_import import (  # pyright: ignore[reportMissingImports]
    TARGET_FIELDS,
    _norm,
    execute_import,
    parse_duration_value,
    parse_file,
    preview_rows,
    propose_mapping,
    type_hints,
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


def _parse_time(val: str, field: str) -> tuple[_time | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        h, m = val.split(":")
        t = _time(int(h), int(m))
        return t, None
    except (ValueError, AttributeError):
        return None, _("%(field)s: enter a valid HH:MM time.", field=field)


def _parse_decimal(val: str, field: str) -> tuple[float | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        n = float(val)
        if n < 0:
            return None, _("%(field)s: must be non-negative.", field=field)
        return n, None
    except ValueError:
        return None, _("%(field)s: must be a number.", field=field)


def _parse_int(val: str, field: str) -> tuple[int | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        n = int(val)
        if n < 0:
            return None, _("%(field)s: must be non-negative.", field=field)
        return n, None
    except ValueError:
        return None, _("%(field)s: must be a whole number.", field=field)


def _parse_date(val: str, field: str) -> tuple[_date | None, str | None]:
    val = val.strip()
    if not val:
        return None, None
    try:
        return _date.fromisoformat(val), None
    except ValueError:
        return None, _("%(field)s: enter a valid date (YYYY-MM-DD).", field=field)


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
        q = q.order_by(PilotLogbookEntry.date.asc(), PilotLogbookEntry.id.asc())
    else:
        q = q.order_by(PilotLogbookEntry.date.desc(), PilotLogbookEntry.id.desc())

    if show_all:
        entries = q.all()
        pagination = None
    else:
        pagination = q.paginate(page=page, per_page=per_page, error_out=False)
        entries = pagination.items

    totals = _compute_totals_sql(uid)
    logbook_milestone = session.pop("logbook_milestone", None)

    return render_template(
        "pilots/logbook.html",
        entries=entries,
        pagination=pagination,
        totals=totals,
        order=order,
        per_page=pp_raw,
        valid_per_page=_VALID_PER_PAGE,
        logbook_milestone=logbook_milestone,
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
        "total_flight_time": round(sp_se + sp_me + multi, 1),
        "function_pic": round(float(row[7] or 0), 1),
        "function_copilot": round(float(row[8] or 0), 1),
        "function_dual": round(float(row[9] or 0), 1),
        "function_instructor": round(float(row[10] or 0), 1),
    }


# ── New entry ────────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/logbook/new", methods=["GET", "POST"])
@login_required
@require_pilot_access
def new_entry() -> ResponseReturnValue:
    uid = _current_user_id()

    if request.method == "POST":
        entry, errors = _entry_from_form(uid)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pilots/entry_form.html",
                entry=None,
                form=request.form,
                action="new",
                openaip_key=_openaip_key(),
            ), 422
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
        updated, errors = _entry_from_form(uid)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "pilots/entry_form.html",
                entry=entry,
                form=request.form,
                action="edit",
                openaip_key=_openaip_key(),
            ), 422
        for col in PilotLogbookEntry.__table__.columns:
            if col.name not in ("id", "pilot_user_id", "gps_track_id"):
                setattr(entry, col.name, getattr(updated, col.name))
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
    )


# ── Delete entry ──────────────────────────────────────────────────────────────


@pilots_bp.route("/pilot/logbook/<int:entry_id>/delete", methods=["POST"])
@login_required
@require_pilot_access
def delete_entry(entry_id: int) -> ResponseReturnValue:
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)
    db.session.delete(entry)
    db.session.commit()
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
        try:
            geojson = json.loads(gps_geojson_raw)
        except ValueError:
            pass  # malformed hidden field — GPS track simply not applied

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


# ── Form parsing ──────────────────────────────────────────────────────────────


def _entry_from_form(pilot_user_id: int) -> tuple[PilotLogbookEntry, list[str]]:
    f = request.form
    errors = []

    date_str = f.get("date", "")
    date_val, err = _parse_date(date_str, "Date")
    if err:
        errors.append(err)
    elif date_val is None:
        errors.append(_("Date is required."))

    dep_time, err = _parse_time(f.get("departure_time", ""), "Departure time")
    if err:
        errors.append(err)
    arr_time, err = _parse_time(f.get("arrival_time", ""), "Arrival time")
    if err:
        errors.append(err)

    night_time, err = _parse_decimal(f.get("night_time", ""), "Night time")
    if err:
        errors.append(err)
    instrument_time, err = _parse_decimal(
        f.get("instrument_time", ""), "Instrument time"
    )
    if err:
        errors.append(err)
    landings_day, err = _parse_int(f.get("landings_day", ""), "Day landings")
    if err:
        errors.append(err)
    landings_night, err = _parse_int(f.get("landings_night", ""), "Night landings")
    if err:
        errors.append(err)
    sp_se, err = _parse_decimal(f.get("single_pilot_se", ""), "S/E time")
    if err:
        errors.append(err)
    sp_me, err = _parse_decimal(f.get("single_pilot_me", ""), "M/E time")
    if err:
        errors.append(err)
    multi_pilot, err = _parse_decimal(f.get("multi_pilot", ""), "Multi-pilot time")
    if err:
        errors.append(err)
    fn_pic, err = _parse_decimal(f.get("function_pic", ""), "PIC function")
    if err:
        errors.append(err)
    fn_co, err = _parse_decimal(f.get("function_copilot", ""), "Co-pilot function")
    if err:
        errors.append(err)
    fn_dual, err = _parse_decimal(f.get("function_dual", ""), "Dual function")
    if err:
        errors.append(err)
    fn_inst, err = _parse_decimal(
        f.get("function_instructor", ""), "Instructor function"
    )
    if err:
        errors.append(err)

    entry = PilotLogbookEntry(
        pilot_user_id=pilot_user_id,
        date=date_val,
        aircraft_type=f.get("aircraft_type", "").strip() or None,
        aircraft_type_icao=f.get("aircraft_type_icao", "").strip() or None,
        aircraft_registration=f.get("aircraft_registration", "").strip() or None,
        departure_place=f.get("departure_place", "").strip() or None,
        departure_time=dep_time,
        arrival_place=f.get("arrival_place", "").strip() or None,
        arrival_time=arr_time,
        pic_name=f.get("pic_name", "").strip() or None,
        night_time=night_time,
        instrument_time=instrument_time,
        landings_day=landings_day,
        landings_night=landings_night,
        single_pilot_se=sp_se,
        single_pilot_me=sp_me,
        multi_pilot=multi_pilot,
        function_pic=fn_pic,
        function_copilot=fn_co,
        function_dual=fn_dual,
        function_instructor=fn_inst,
        remarks=f.get("remarks", "").strip() or None,
    )
    return entry, errors


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
