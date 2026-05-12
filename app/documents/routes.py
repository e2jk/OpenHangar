import mimetypes
import os
import uuid

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
from werkzeug.utils import secure_filename  # type: ignore

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import Aircraft, Component, Document, Role, TenantUser, db  # pyright: ignore[reportMissingImports]
from utils import login_required, require_role  # pyright: ignore[reportMissingImports]

documents_bp = Blueprint("documents", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

_ALLOWED_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt",
}


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


def _get_document_or_404(aircraft: Aircraft, document_id: int) -> Document:
    doc = db.session.get(Document, document_id)
    if not doc or doc.aircraft_id != aircraft.id:
        abort(404)
    return doc


def _delete_file(filename: str | None) -> None:
    if not filename:
        return
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    try:
        os.remove(os.path.join(folder, filename))
    except OSError:
        pass


def _resolve_component(ac: Aircraft) -> Component | None:
    """Return the component from query args/form if it belongs to this aircraft."""
    raw = request.args.get("component_id") or request.form.get("component_id")
    if not raw:
        return None
    try:
        cid = int(raw)
    except (ValueError, TypeError):
        return None
    comp = db.session.get(Component, cid)
    return comp if (comp and comp.aircraft_id == ac.id) else None


# ── Document list ─────────────────────────────────────────────────────────────

@documents_bp.route("/aircraft/<int:aircraft_id>/documents")
@login_required
def list_documents(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    show_sensitive = request.args.get("sensitive") == "1"
    query = Document.query.filter_by(aircraft_id=ac.id)
    if not show_sensitive:
        query = query.filter_by(is_sensitive=False)
    docs = query.order_by(Document.uploaded_at.desc()).all()
    sensitive_count = Document.query.filter_by(
        aircraft_id=ac.id, is_sensitive=True
    ).count()
    return render_template(
        "documents/list.html",
        aircraft=ac,
        docs=docs,
        show_sensitive=show_sensitive,
        sensitive_count=sensitive_count,
    )


# ── Upload document ───────────────────────────────────────────────────────────

@documents_bp.route("/aircraft/<int:aircraft_id>/documents/upload",
                    methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def upload_document(aircraft_id):
    ac = _get_aircraft_or_404(aircraft_id)
    component = _resolve_component(ac)

    if request.method == "POST":
        file = request.files.get("file")
        title = request.form.get("title", "").strip() or None
        is_sensitive = bool(request.form.get("is_sensitive"))

        if not file or not file.filename:
            flash(_("Please select a file to upload."), "danger")
            return render_template("documents/upload_form.html",
                                   aircraft=ac, component=component)

        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in _ALLOWED_EXTS:
            flash(_("File type '%(ext)s' is not allowed.", ext=ext or "unknown"), "danger")
            return render_template("documents/upload_form.html",
                                   aircraft=ac, component=component)

        label = f"comp{component.id}" if component else f"ac{ac.id}"
        stored = f"doc_{label}_{uuid.uuid4().hex[:12]}{ext}"
        folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
        os.makedirs(folder, exist_ok=True)
        file.save(os.path.join(folder, stored))
        mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
        size = os.path.getsize(os.path.join(folder, stored))

        doc = Document(
            aircraft_id=ac.id,
            component_id=component.id if component else None,
            filename=stored,
            original_filename=original,
            mime_type=mime,
            size_bytes=size,
            title=title,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)
        db.session.commit()

        flash(_("Document uploaded."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template("documents/upload_form.html",
                           aircraft=ac, component=component)


# ── Edit document metadata ────────────────────────────────────────────────────

@documents_bp.route("/aircraft/<int:aircraft_id>/documents/<int:document_id>/edit",
                    methods=["GET", "POST"])
@login_required
@require_role(*_OWNER_ROLES)
def edit_document(aircraft_id, document_id):
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_document_or_404(ac, document_id)

    if request.method == "POST":
        doc.title = request.form.get("title", "").strip() or None
        doc.is_sensitive = bool(request.form.get("is_sensitive"))
        db.session.commit()
        flash(_("Document updated."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template("documents/edit_form.html", aircraft=ac, doc=doc)


# ── Delete document ───────────────────────────────────────────────────────────

@documents_bp.route("/aircraft/<int:aircraft_id>/documents/<int:document_id>/delete",
                    methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def delete_document(aircraft_id, document_id):
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_document_or_404(ac, document_id)
    _delete_file(doc.filename)
    db.session.delete(doc)
    db.session.commit()
    flash(_("Document deleted."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))
