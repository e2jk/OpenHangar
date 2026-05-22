import io
import mimetypes
import os
import uuid
import zipfile
from datetime import date as _date

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    DocType,
    Document,
    Role,
    TenantUser,
    db,
)
from utils import login_required, require_role, user_can_access_aircraft  # pyright: ignore[reportMissingImports]

documents_bp = Blueprint("documents", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

_ALLOWED_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".txt",
}

_PILOT_DOC_TYPES = [
    (DocType.LICENSE, "Licence"),
    (DocType.MEDICAL, "Medical certificate"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
        abort(404)
    return ac


def _get_aircraft_document_or_404(aircraft: Aircraft, document_id: int) -> Document:
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
        current_app.logger.debug(
            "Could not delete upload %s (already absent?)", filename
        )


def _resolve_component(ac: Aircraft) -> Component | None:
    raw = request.args.get("component_id") or request.form.get("component_id")
    if not raw:
        return None
    try:
        cid = int(raw)
    except (ValueError, TypeError):
        return None
    comp = db.session.get(Component, cid)
    return comp if (comp and comp.aircraft_id == ac.id) else None


def _save_upload(file: FileStorage, label: str) -> tuple[str, str, int]:
    """Save *file* to upload folder; return (stored_name, mime_type, size_bytes)."""
    original = secure_filename(file.filename or "")
    ext = os.path.splitext(original)[1].lower()
    stored = f"doc_{label}_{uuid.uuid4().hex[:12]}{ext}"
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, stored))
    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
    size = os.path.getsize(os.path.join(folder, stored))
    return stored, mime, size


def _current_role() -> Role | None:
    tu = TenantUser.query.filter_by(user_id=session.get("user_id")).first()
    return tu.role if tu else None


# ── Title suggestions ─────────────────────────────────────────────────────────


@documents_bp.route("/documents/title-suggestions")
@login_required
def title_suggestions() -> ResponseReturnValue:
    q = request.args.get("q", "").strip()
    owner_type = request.args.get("owner_type", "aircraft")
    uid = int(session["user_id"])

    if owner_type == "pilot":
        base = Document.query.filter(
            Document.pilot_user_id == uid,
            Document.title.isnot(None),
        )
    else:
        tid = _tenant_id()
        aircraft_ids = [
            row.id
            for row in Aircraft.query.filter_by(tenant_id=tid)
            .with_entities(Aircraft.id)
            .all()
        ]
        base = Document.query.filter(
            Document.aircraft_id.in_(aircraft_ids),
            Document.title.isnot(None),
        )
        if owner_type == "component":
            base = base.filter(Document.component_id.isnot(None))
        else:
            base = base.filter(
                Document.component_id.is_(None),
                Document.flight_entry_id.is_(None),
            )

    if q:
        base = base.filter(Document.title.ilike(f"{q}%"))

    rows = (
        base.order_by(Document.uploaded_at.desc())
        .with_entities(Document.title)
        .distinct()
        .limit(10)
        .all()
    )
    return jsonify([r.title for r in rows])


# ── Aircraft document list ────────────────────────────────────────────────────


@documents_bp.route("/aircraft/<int:aircraft_id>/documents")
@login_required
def list_documents(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    show_sensitive = request.args.get("sensitive") == "1"
    query = Document.query.filter_by(aircraft_id=ac.id)
    if not show_sensitive:
        query = query.filter_by(is_sensitive=False)
    docs = query.order_by(Document.uploaded_at.desc()).all()
    sensitive_count = Document.query.filter_by(
        aircraft_id=ac.id, is_sensitive=True
    ).count()
    role = _current_role()
    is_owner = role in _OWNER_ROLES
    return render_template(
        "documents/list.html",
        aircraft=ac,
        docs=docs,
        show_sensitive=show_sensitive,
        sensitive_count=sensitive_count,
        is_owner=is_owner,
    )


# ── Upload aircraft document ──────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<int:aircraft_id>/documents/upload", methods=["GET", "POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def upload_document(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    component = _resolve_component(ac)

    if request.method == "POST":
        file = request.files.get("file")
        title = request.form.get("title", "").strip() or None
        is_sensitive = bool(request.form.get("is_sensitive"))
        doc_type = request.form.get("doc_type") or None
        valid_until_str = request.form.get("valid_until", "").strip()
        valid_until = None
        if valid_until_str:
            try:
                valid_until = _date.fromisoformat(valid_until_str)
            except ValueError:
                pass

        if not file or not file.filename:
            flash(_("Please select a file to upload."), "danger")
            return render_template(
                "documents/upload_form.html",
                aircraft=ac,
                component=component,
                doc_types=_PILOT_DOC_TYPES,
            )

        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in _ALLOWED_EXTS:
            flash(
                _("File type '%(ext)s' is not allowed.", ext=ext or "unknown"), "danger"
            )
            return render_template(
                "documents/upload_form.html",
                aircraft=ac,
                component=component,
                doc_types=_PILOT_DOC_TYPES,
            )

        label = f"comp{component.id}" if component else f"ac{ac.id}"
        stored, mime, size = _save_upload(file, label)

        doc = Document(
            aircraft_id=ac.id,
            component_id=component.id if component else None,
            filename=stored,
            original_filename=original,
            mime_type=mime,
            size_bytes=size,
            title=title,
            doc_type=doc_type,
            valid_until=valid_until,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)
        db.session.commit()

        flash(_("Document uploaded."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template(
        "documents/upload_form.html",
        aircraft=ac,
        component=component,
        doc_types=_PILOT_DOC_TYPES,
    )


# ── Edit aircraft document ────────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<int:aircraft_id>/documents/<int:document_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def edit_document(aircraft_id: int, document_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_aircraft_document_or_404(ac, document_id)

    if request.method == "POST":
        doc.title = request.form.get("title", "").strip() or None
        doc.is_sensitive = bool(request.form.get("is_sensitive"))
        valid_until_str = request.form.get("valid_until", "").strip()
        doc.valid_until = None
        if valid_until_str:
            try:
                doc.valid_until = _date.fromisoformat(valid_until_str)
            except ValueError:
                pass
        db.session.commit()
        flash(_("Document updated."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template("documents/edit_form.html", aircraft=ac, doc=doc)


# ── Delete aircraft document ──────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<int:aircraft_id>/documents/<int:document_id>/delete", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_document(aircraft_id: int, document_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_aircraft_document_or_404(ac, document_id)
    _delete_file(doc.filename)
    db.session.delete(doc)
    db.session.commit()
    flash(_("Document deleted."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))


# ── Download all aircraft documents as ZIP ────────────────────────────────────


@documents_bp.route("/aircraft/<int:aircraft_id>/documents/download-all")
@login_required
def download_all_documents(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    role = _current_role()
    include_sensitive = role in _OWNER_ROLES

    query = Document.query.filter_by(aircraft_id=ac.id)
    if not include_sensitive:
        query = query.filter_by(is_sensitive=False)
    docs = query.order_by(Document.uploaded_at.asc()).all()

    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    buf = io.BytesIO()
    manifest_lines = ["filename\ttitle\ttype\tuploaded\n"]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            path = os.path.join(folder, doc.filename)
            arcname = doc.original_filename
            if os.path.exists(path):
                zf.write(path, arcname=arcname)
            doc_type_label = doc.doc_type or ""
            uploaded = doc.uploaded_at.strftime("%Y-%m-%d") if doc.uploaded_at else ""
            manifest_lines.append(
                f"{arcname}\t{doc.title or ''}\t{doc_type_label}\t{uploaded}\n"
            )
        zf.writestr("manifest.txt", "".join(manifest_lines))

    buf.seek(0)
    reg = ac.registration.replace("/", "-")
    zip_name = f"aircraft-{reg}-documents.zip"
    return Response(
        buf.read(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ── Insurance certificate upload ──────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<int:aircraft_id>/insurance-cert/upload", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def upload_insurance_cert(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    file = request.files.get("file")

    if not file or not file.filename:
        flash(_("Please select a file to upload."), "danger")
        return redirect(url_for("aircraft.detail", aircraft_id=ac.id))

    original = secure_filename(file.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in _ALLOWED_EXTS:
        flash(_("File type '%(ext)s' is not allowed.", ext=ext or "unknown"), "danger")
        return redirect(url_for("aircraft.detail", aircraft_id=ac.id))

    stored, mime, size = _save_upload(file, f"ac{ac.id}")

    # Mark previous insurance certificate as superseded
    prev = (
        Document.query.filter_by(
            aircraft_id=ac.id,
            doc_type=DocType.INSURANCE_CERT,
        )
        .filter(Document.superseded_by_id.is_(None))
        .first()
    )

    new_cert = Document(
        aircraft_id=ac.id,
        filename=stored,
        original_filename=original,
        mime_type=mime,
        size_bytes=size,
        title=_("Insurance Certificate"),
        doc_type=DocType.INSURANCE_CERT,
        valid_until=ac.insurance_expiry,
        is_sensitive=True,
    )
    db.session.add(new_cert)
    db.session.flush()

    if prev:
        prev.superseded_by_id = new_cert.id

    db.session.commit()
    flash(_("Insurance certificate uploaded."), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


# ── Pilot document upload ─────────────────────────────────────────────────────


@documents_bp.route("/pilot/documents/upload", methods=["GET", "POST"])
@login_required
def upload_pilot_document() -> ResponseReturnValue:
    uid = int(session["user_id"])

    if request.method == "POST":
        file = request.files.get("file")
        title = request.form.get("title", "").strip() or None
        doc_type = request.form.get("doc_type") or None
        valid_until_str = request.form.get("valid_until", "").strip()
        valid_until = None
        if valid_until_str:
            try:
                valid_until = _date.fromisoformat(valid_until_str)
            except ValueError:
                pass

        if not file or not file.filename:
            flash(_("Please select a file to upload."), "danger")
            return render_template(
                "documents/pilot_upload_form.html", doc_types=_PILOT_DOC_TYPES
            )

        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in _ALLOWED_EXTS:
            flash(
                _("File type '%(ext)s' is not allowed.", ext=ext or "unknown"), "danger"
            )
            return render_template(
                "documents/pilot_upload_form.html", doc_types=_PILOT_DOC_TYPES
            )

        stored, mime, size = _save_upload(file, f"pilot{uid}")

        doc = Document(
            pilot_user_id=uid,
            filename=stored,
            original_filename=original,
            mime_type=mime,
            size_bytes=size,
            title=title,
            doc_type=doc_type,
            valid_until=valid_until,
            is_sensitive=True,
        )
        db.session.add(doc)
        db.session.commit()

        flash(_("Document uploaded."), "success")
        return redirect(url_for("pilots.profile"))

    return render_template(
        "documents/pilot_upload_form.html", doc_types=_PILOT_DOC_TYPES
    )


# ── Pilot document delete ─────────────────────────────────────────────────────


@documents_bp.route("/pilot/documents/<int:document_id>/delete", methods=["POST"])
@login_required
def delete_pilot_document(document_id: int) -> ResponseReturnValue:
    uid = int(session["user_id"])
    doc = db.session.get(Document, document_id)
    if not doc or doc.pilot_user_id != uid:
        abort(404)
    _delete_file(doc.filename)
    db.session.delete(doc)
    db.session.commit()
    flash(_("Document deleted."), "success")
    return redirect(url_for("pilots.profile"))
