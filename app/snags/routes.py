from datetime import datetime, timezone

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

from models import Aircraft, Snag, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import login_required  # pyright: ignore[reportMissingImports]

snags_bp = Blueprint("snags", __name__)


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return tu.tenant_id


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if not ac or ac.tenant_id != _tenant_id():
        abort(404)
    return ac


def _get_snag_or_404(aircraft: Aircraft, snag_id: int) -> Snag:
    s = db.session.get(Snag, snag_id)
    if not s or s.aircraft_id != aircraft.id:
        abort(404)
    return s


# ── Snag list ─────────────────────────────────────────────────────────────────

@snags_bp.route("/aircraft/<int:aircraft_id>/snags")
@login_required
def list_snags(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    open_snags = (
        Snag.query
        .filter_by(aircraft_id=ac.id, resolved_at=None)
        .order_by(Snag.is_grounding.desc(), Snag.reported_at.desc())
        .all()
    )
    closed_snags = (
        Snag.query
        .filter(Snag.aircraft_id == ac.id, Snag.resolved_at.isnot(None))
        .order_by(Snag.resolved_at.desc())
        .all()
    )
    return render_template("snags/list.html", aircraft=ac,
                           open_snags=open_snags, closed_snags=closed_snags)


# ── Add snag ──────────────────────────────────────────────────────────────────

@snags_bp.route("/aircraft/<int:aircraft_id>/snags/new", methods=["GET", "POST"])
@login_required
def new_snag(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    if request.method == "POST":
        return _save_snag(ac, None)
    return render_template("snags/snag_form.html", aircraft=ac, snag=None)


# ── Edit snag ─────────────────────────────────────────────────────────────────

@snags_bp.route("/aircraft/<int:aircraft_id>/snags/<int:snag_id>/edit",
                methods=["GET", "POST"])
@login_required
def edit_snag(aircraft_id, snag_id):
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if not s.is_open:
        flash(_("Closed snags cannot be edited."), "danger")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))
    if request.method == "POST":
        return _save_snag(ac, s)
    return render_template("snags/snag_form.html", aircraft=ac, snag=s)


def _save_snag(ac: Aircraft, s: Snag | None):
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    reporter = request.form.get("reporter", "").strip() or None
    is_grounding = bool(request.form.get("is_grounding"))

    errors = []
    if not title:
        errors.append(_("Title is required."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("snags/snag_form.html", aircraft=ac, snag=s)

    if s is None:
        s = Snag(aircraft_id=ac.id)
        db.session.add(s)

    s.title = title
    s.description = description
    s.reporter = reporter
    s.is_grounding = is_grounding
    db.session.commit()

    flash(_("Snag '%(title)s' saved.", title=s.title), "success")
    return redirect(url_for("snags.list_snags", aircraft_id=ac.id))


# ── Resolve snag ──────────────────────────────────────────────────────────────

@snags_bp.route("/aircraft/<int:aircraft_id>/snags/<int:snag_id>/resolve",
                methods=["GET", "POST"])
@login_required
def resolve_snag(aircraft_id, snag_id):
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    if not s.is_open:
        flash(_("Snag is already closed."), "danger")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))

    if request.method == "POST":
        note = request.form.get("resolution_note", "").strip()
        if not note:
            flash(_("A resolution note is required."), "danger")
            return render_template("snags/resolve_form.html", aircraft=ac, snag=s)
        s.resolved_at = datetime.now(timezone.utc)
        s.resolution_note = note
        db.session.commit()
        flash(_("Snag '%(title)s' closed.", title=s.title), "success")
        return redirect(url_for("snags.list_snags", aircraft_id=ac.id))

    return render_template("snags/resolve_form.html", aircraft=ac, snag=s)


# ── Delete snag ───────────────────────────────────────────────────────────────

@snags_bp.route("/aircraft/<int:aircraft_id>/snags/<int:snag_id>/delete",
                methods=["POST"])
@login_required
def delete_snag(aircraft_id, snag_id):
    ac = _get_aircraft_or_404(aircraft_id)
    s = _get_snag_or_404(ac, snag_id)
    title = s.title
    db.session.delete(s)
    db.session.commit()
    flash(_("Snag '%(title)s' deleted.", title=title), "success")
    return redirect(url_for("snags.list_snags", aircraft_id=ac.id))
