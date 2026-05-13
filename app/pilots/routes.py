from datetime import date as _date, time as _time

from sqlalchemy import func  # pyright: ignore[reportMissingImports]
from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import PilotLogbookEntry, PilotProfile, User, db  # pyright: ignore[reportMissingImports]
from utils import login_required  # pyright: ignore[reportMissingImports]

pilots_bp = Blueprint("pilots", __name__)


def _current_user_id() -> int:
    return session["user_id"]


def _get_or_create_profile(user_id: int) -> PilotProfile:
    profile = PilotProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = PilotProfile(user_id=user_id)
        db.session.add(profile)
        db.session.flush()
    return profile


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
def profile():
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

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("pilots/profile.html", profile=p), 422

        db.session.commit()
        flash(_("Profile saved."), "success")
        return redirect(url_for("pilots.profile"))

    return render_template("pilots/profile.html", profile=p)


_VALID_PER_PAGE = (10, 20, 50, 100)
_DEFAULT_PER_PAGE = 20


# ── Logbook list ──────────────────────────────────────────────────────────────

@pilots_bp.route("/pilot/logbook")
@login_required
def logbook():
    uid   = _current_user_id()
    order = request.args.get("order", "desc")
    page  = request.args.get("page", 1, type=int)
    pp_raw = request.args.get("per_page", str(_DEFAULT_PER_PAGE))
    show_all = pp_raw == "all"
    per_page = None if show_all else (
        int(pp_raw) if pp_raw.isdigit() and int(pp_raw) in _VALID_PER_PAGE
        else _DEFAULT_PER_PAGE
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

    return render_template(
        "pilots/logbook.html",
        entries=entries,
        pagination=pagination,
        totals=totals,
        order=order,
        per_page=pp_raw,
        valid_per_page=_VALID_PER_PAGE,
    )


def _compute_totals_sql(pilot_user_id: int) -> dict:
    """Aggregate totals over ALL entries for the pilot via a single SQL query."""
    row = db.session.query(
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
    ).filter(PilotLogbookEntry.pilot_user_id == pilot_user_id).one()

    sp_se  = round(float(row[4] or 0), 1)
    sp_me  = round(float(row[5] or 0), 1)
    multi  = round(float(row[6] or 0), 1)

    return {
        "night_time":          round(float(row[0] or 0), 1),
        "instrument_time":     round(float(row[1] or 0), 1),
        "landings_day":        int(row[2] or 0),
        "landings_night":      int(row[3] or 0),
        "single_pilot_se":     sp_se,
        "single_pilot_me":     sp_me,
        "multi_pilot":         multi,
        "total_flight_time":   round(sp_se + sp_me + multi, 1),
        "function_pic":        round(float(row[7] or 0), 1),
        "function_copilot":    round(float(row[8] or 0), 1),
        "function_dual":       round(float(row[9] or 0), 1),
        "function_instructor": round(float(row[10] or 0), 1),
    }


# ── New entry ─────────────────────────────────────────────────────────────────

@pilots_bp.route("/pilot/logbook/new", methods=["GET", "POST"])
@login_required
def new_entry():
    uid = _current_user_id()
    if request.method == "POST":
        entry, errors = _entry_from_form(uid)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("pilots/entry_form.html", entry=None,
                                   form=request.form, action="new"), 422
        db.session.add(entry)
        db.session.commit()
        flash(_("Logbook entry added."), "success")
        return redirect(url_for("pilots.logbook"))
    from models import User
    _u = db.session.get(User, uid)
    return render_template("pilots/entry_form.html", entry=None,
                           form={"pic_name": _u.display_name if _u else ""},
                           action="new")


# ── Edit entry ────────────────────────────────────────────────────────────────

@pilots_bp.route("/pilot/logbook/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id):
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)

    if request.method == "POST":
        updated, errors = _entry_from_form(uid)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("pilots/entry_form.html", entry=entry,
                                   form=request.form, action="edit"), 422
        # Apply updated fields to existing row
        for col in PilotLogbookEntry.__table__.columns:
            if col.name not in ("id", "pilot_user_id"):
                setattr(entry, col.name, getattr(updated, col.name))
        db.session.commit()
        flash(_("Logbook entry updated."), "success")
        return redirect(url_for("pilots.logbook"))

    return render_template("pilots/entry_form.html", entry=entry,
                           form={}, action="edit")


# ── Delete entry ──────────────────────────────────────────────────────────────

@pilots_bp.route("/pilot/logbook/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_entry(entry_id):
    uid = _current_user_id()
    entry = db.session.get(PilotLogbookEntry, entry_id)
    if not entry or entry.pilot_user_id != uid:
        abort(404)
    db.session.delete(entry)
    db.session.commit()
    flash(_("Logbook entry deleted."), "success")
    return redirect(url_for("pilots.logbook"))


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

    night_time, err       = _parse_decimal(f.get("night_time", ""),       "Night time")
    if err: errors.append(err)
    instrument_time, err  = _parse_decimal(f.get("instrument_time", ""),  "Instrument time")
    if err: errors.append(err)
    landings_day, err     = _parse_int(f.get("landings_day", ""),         "Day landings")
    if err: errors.append(err)
    landings_night, err   = _parse_int(f.get("landings_night", ""),       "Night landings")
    if err: errors.append(err)
    sp_se, err            = _parse_decimal(f.get("single_pilot_se", ""),  "S/E time")
    if err: errors.append(err)
    sp_me, err            = _parse_decimal(f.get("single_pilot_me", ""),  "M/E time")
    if err: errors.append(err)
    multi_pilot, err      = _parse_decimal(f.get("multi_pilot", ""),      "Multi-pilot time")
    if err: errors.append(err)
    fn_pic, err           = _parse_decimal(f.get("function_pic", ""),     "PIC function")
    if err: errors.append(err)
    fn_co, err            = _parse_decimal(f.get("function_copilot", ""), "Co-pilot function")
    if err: errors.append(err)
    fn_dual, err          = _parse_decimal(f.get("function_dual", ""),    "Dual function")
    if err: errors.append(err)
    fn_inst, err          = _parse_decimal(f.get("function_instructor", ""), "Instructor function")
    if err: errors.append(err)

    entry = PilotLogbookEntry(
        pilot_user_id        = pilot_user_id,
        date                 = date_val,
        aircraft_type        = f.get("aircraft_type", "").strip() or None,
        aircraft_registration = f.get("aircraft_registration", "").strip() or None,
        departure_place      = f.get("departure_place", "").strip() or None,
        departure_time       = dep_time,
        arrival_place        = f.get("arrival_place", "").strip() or None,
        arrival_time         = arr_time,
        pic_name             = f.get("pic_name", "").strip() or None,
        night_time           = night_time,
        instrument_time      = instrument_time,
        landings_day         = landings_day,
        landings_night       = landings_night,
        single_pilot_se      = sp_se,
        single_pilot_me      = sp_me,
        multi_pilot          = multi_pilot,
        function_pic         = fn_pic,
        function_copilot     = fn_co,
        function_dual        = fn_dual,
        function_instructor  = fn_inst,
        remarks              = f.get("remarks", "").strip() or None,
    )
    return entry, errors
