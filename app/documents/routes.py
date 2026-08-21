import contextlib
import io
import logging
import mimetypes
import os
import re as _re
import uuid
import zipfile
from datetime import UTC, datetime
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
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]
from flask_babel import lazy_gettext as _l
from flask_babel import ngettext
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Component,
    DocCategory,
    DocType,
    Document,
    PendingReconcile,
    Role,
    Tenant,
    TenantUser,
    db,
)
from utils import (  # pyright: ignore[reportMissingImports]
    activity,
    login_required,
    require_role,
    user_can_access_aircraft,
)
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

log = logging.getLogger(__name__)

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
    (DocType.LICENSE, _l("Licence")),
    (DocType.MEDICAL, _l("Medical certificate")),
]

_AIRCRAFT_DOC_TYPES = [
    (DocType.INSURANCE_CERT, _l("Insurance Certificate")),
    (DocType.ARC, _l("Airworthiness Review Certificate (ARC)")),
]

# doc_type -> Aircraft attribute it drives. Aircraft.insurance_expiry/arc_expiry
# are synced caches, not directly user-editable — see the comment on those
# columns in models.py. Kept in step by _recompute_expiry_field() below
# whenever a Document of one of these types is uploaded, edited, or deleted.
_EXPIRY_DRIVING_DOC_TYPES: dict[str, str] = {
    DocType.INSURANCE_CERT: "insurance_expiry",
    DocType.ARC: "arc_expiry",
}

# Human-readable labels for each DocCategory value
_CATEGORY_LABELS: dict[str, str] = {
    DocCategory.MAINTENANCE: "Maintenance",
    DocCategory.INSURANCE: "Insurance",
    DocCategory.POH: "POH / Flight Manual",
    DocCategory.AIRWORTHINESS: "Airworthiness",
    DocCategory.LOGBOOK: "Logbook",
    DocCategory.INVOICE: "Invoice",
    DocCategory.OTHER: "Other",
    DocCategory.UNCATEGORISED: "Uncategorised",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
    return int(tu.tenant_id)


def _get_tenant() -> Tenant:
    tid = _tenant_id()
    t = db.session.get(Tenant, tid)
    if not t:
        abort(403)  # pragma: no cover
    return t


def _get_aircraft_or_404(aircraft_id: int) -> Aircraft:
    ac = db.session.get(Aircraft, aircraft_id)
    if (
        not ac
        or ac.tenant_id != _tenant_id()
        or not user_can_access_aircraft(aircraft_id)
    ):
        abort(404)
    return ac


def active_document_for(
    aircraft_id: int,
    doc_type: str,
    component_id: int | None = None,
    as_of: _date | None = None,
) -> Document | None:
    """The document of this (aircraft, doc_type[, component]) that is in
    force as of `as_of` (default today): valid_from is None (meaning "in
    force as soon as uploaded") or has already arrived. Among those, the
    one with the latest valid_from wins; ties -- including the common
    valid_from=None case -- break on uploaded_at, so behaviour for
    documents that never set valid_from is unchanged from before this
    concept existed. Deliberately ignores superseded_by_id: that's a
    structural "replaced by" pointer set at upload time, not a statement
    about which document is in force today."""
    as_of = as_of or _date.today()
    # Ordering (not a Python-side max()) so the comparison stays in SQL --
    # a freshly-flushed-but-uncommitted Document's tz-aware uploaded_at can
    # otherwise be compared against an already-committed sibling's
    # SQLite-naive one and raise. NULL valid_from sorts last (lowest
    # priority, same as treating it as `date.min`).
    result: Document | None = (
        Document.query.filter(
            Document.aircraft_id == aircraft_id,
            Document.doc_type == doc_type,
            Document.component_id == component_id,
            db.or_(Document.valid_from.is_(None), Document.valid_from <= as_of),
        )
        .order_by(Document.valid_from.desc().nullslast(), Document.uploaded_at.desc())
        .first()
    )
    return result


def _recompute_expiry_field(ac: Aircraft, doc_type: str | None) -> None:
    """Keep Aircraft.insurance_expiry/arc_expiry in step with whichever
    Document of that doc_type is currently active (see
    active_document_for). Called after any create/edit/delete that could
    change which document is active, and once daily (see
    services.notification_service._recompute_all_expiry_fields) so a
    future-dated document activates on its valid_from even without a new
    edit that day — safe to call for any doc_type, a no-op for ones that
    don't drive an Aircraft field."""
    field = _EXPIRY_DRIVING_DOC_TYPES.get(doc_type or "")
    if not field:
        return
    assert doc_type is not None  # implied by `field` being truthy above
    active = active_document_for(ac.id, doc_type, component_id=None)
    setattr(ac, field, active.valid_until if active else None)


def _parse_date_field(raw: str) -> _date | None:
    """Parse an optional YYYY-MM-DD form field; invalid/blank input is
    treated as "not set" rather than rejected outright, matching how
    valid_until has always been handled here."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return _date.fromisoformat(raw)
    except ValueError as exc:
        log.debug("Invalid date field: %s", exc)
        return None


def _get_aircraft_document_or_404(aircraft: Aircraft, document_id: int) -> Document:
    doc = db.session.get(Document, document_id)
    if not doc or doc.aircraft_id != aircraft.id:
        abort(404)
    return doc


def _delete_file(filename: str | None) -> None:
    """Move file to _trash/ instead of hard-deleting.

    Syncthing propagates the move to all peers; the file is recoverable.
    If the file is not found, log and continue silently.
    """
    if not filename:
        return
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    src = os.path.join(folder, filename)
    if not os.path.exists(src):
        current_app.logger.debug("File already absent, skipping trash: %s", filename)
        return
    try:
        trash_dir = os.path.join(folder, "_trash")
        os.makedirs(trash_dir, exist_ok=True)
        dest_name = os.path.basename(filename)
        dest = os.path.join(trash_dir, dest_name)
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest_name)
            dest = os.path.join(trash_dir, f"{base}_{uuid.uuid4().hex[:8]}{ext}")
        os.rename(src, dest)
    except OSError:
        current_app.logger.debug("Could not move to trash: %s", filename)


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
    """Save *file* flat to upload folder (legacy path, no canonical structure)."""
    original = secure_filename(file.filename or "")
    ext = os.path.splitext(original)[1].lower()
    stored = f"doc_{label}_{uuid.uuid4().hex[:12]}{ext}"
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, stored))
    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
    size = os.path.getsize(os.path.join(folder, stored))
    return stored, mime, size


def _ensure_tenant_slug(tenant: Tenant) -> str:
    """Return tenant.slug, generating one from the name if not yet set."""
    if tenant.slug:
        return str(tenant.slug)
    base = _re.sub(r"[^a-z0-9]+", "-", tenant.name.lower()).strip("-")[:64]
    slug = base
    n = 1
    while Tenant.query.filter(Tenant.slug == slug, Tenant.id != tenant.id).first():
        slug = f"{base}-{n}"
        n += 1
    tenant.slug = slug
    db.session.flush()
    return slug


def _safe_path_component(s: str) -> str:
    """Strip characters that are unsafe in filesystem path segments."""
    return _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip()


def _safe_join(upload_folder: str, *parts: str) -> str:
    """Join parts under upload_folder; abort(400) if the result would escape it."""
    root = os.path.realpath(upload_folder)
    joined = os.path.normpath(os.path.join(root, *parts))
    if not (joined == root or joined.startswith(root + os.sep)):
        abort(400)
    return joined


_RECONCILE_FILENAME_RE = _re.compile(r"^(\d{4}-\d{2}-\d{2}) - (.+?)(\.[^.]+)?$")


def _parse_reconcile_filename(
    cat_str: str, name_part: str
) -> tuple[str | None, str | None, _date | None]:
    """Parse a Syncthing-mounted path's category segment and its
    "YYYY-MM-DD - title.ext" filename segment into (category, title_hint,
    date_hint). Shared by scan_documents and rename_reconcile_folder's
    inline rescan so a scan and a post-rename rescan can never disagree on
    how to interpret the same on-disk layout."""
    category = cat_str.lower() if cat_str.lower() in DocCategory.ALL else None
    title_hint: str | None = None
    date_hint: _date | None = None
    m = _RECONCILE_FILENAME_RE.match(name_part)
    if m:
        with contextlib.suppress(
            ValueError
        ):  # regex matched date-like string but it's invalid; treat as no date
            date_hint = _date.fromisoformat(m.group(1))
        title_hint = m.group(2)
    else:
        title_hint = os.path.splitext(name_part)[0]
    return category, title_hint, date_hint


def _save_upload_canonical(
    file: FileStorage,
    tenant: Tenant,
    aircraft: Aircraft,
    category: str,
    title: str | None,
) -> tuple[str, str, int]:
    """Save *file* to the canonical Syncthing-compatible path structure.

    Returns (relpath, mime_type, size_bytes) where relpath is relative to
    UPLOAD_FOLDER and suitable for storage in Document.filename.
    """
    original = secure_filename(file.filename or "unnamed")
    ext = os.path.splitext(original)[1].lower()
    today = _date.today().isoformat()
    safe_title = _safe_path_component(title or os.path.splitext(original)[0])[:100]
    fname = f"{today} - {safe_title}{ext}"

    slug = _ensure_tenant_slug(tenant)
    safe_reg = aircraft.registration.replace("/", "-").replace(" ", "-").upper()
    relpath = os.path.join(slug, safe_reg, category, fname)

    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    full_dir = _safe_join(folder, slug, safe_reg, category)
    os.makedirs(full_dir, exist_ok=True)

    dest = _safe_join(folder, relpath)
    # If a file with this name already exists (e.g. same title + date), add a short suffix
    if os.path.exists(dest):
        base, ext2 = os.path.splitext(fname)
        relpath = os.path.join(
            slug, safe_reg, category, f"{base}_{uuid.uuid4().hex[:6]}{ext2}"
        )
        dest = _safe_join(folder, relpath)

    file.save(dest)
    mime = mimetypes.guess_type(original)[0] or "application/octet-stream"
    size = os.path.getsize(dest)
    return relpath, mime, size


def _current_role() -> Role | None:
    tu = TenantUser.query.filter_by(user_id=session.get("user_id")).first()
    return tu.role if tu else None


def _doc_broken(doc: Document) -> bool:
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    return not os.path.exists(os.path.join(folder, doc.filename))


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
        base.with_entities(Document.title)
        .distinct()
        .order_by(Document.title)
        .limit(10)
        .all()
    )
    return jsonify([r.title for r in rows])


# ── Aircraft document list ────────────────────────────────────────────────────


@documents_bp.route("/aircraft/<aircraft_ref:aircraft_id>/documents")
@login_required
def list_documents(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    show_sensitive = request.args.get("sensitive") == "1"
    doc_type_labels = dict(_AIRCRAFT_DOC_TYPES)
    filter_doc_type = request.args.get("doc_type") or None
    if filter_doc_type not in doc_type_labels:
        filter_doc_type = None
    query = Document.query.filter_by(aircraft_id=ac.id)
    if not show_sensitive:
        query = query.filter_by(is_sensitive=False)
    if filter_doc_type:
        query = query.filter_by(doc_type=filter_doc_type)
    docs = query.order_by(Document.uploaded_at.desc()).all()
    sensitive_count = Document.query.filter_by(
        aircraft_id=ac.id, is_sensitive=True
    ).count()
    role = _current_role()
    is_owner = role in _OWNER_ROLES
    broken_ids = {doc.id for doc in docs if _doc_broken(doc)}
    # One active document per doc_type actually present in this list --
    # cheap even unfiltered, since an aircraft only ever has the two
    # expiry-driving types (ARC/insurance) to look up.
    doc_types_shown = {
        d.doc_type for d in docs if d.doc_type in _EXPIRY_DRIVING_DOC_TYPES
    }
    active_doc_ids = {
        active.id
        for dt in doc_types_shown
        for active in [active_document_for(ac.id, dt, component_id=None)]
        if active is not None
    }
    return render_template(
        "documents/list.html",
        aircraft=ac,
        docs=docs,
        show_sensitive=show_sensitive,
        sensitive_count=sensitive_count,
        is_owner=is_owner,
        broken_ids=broken_ids,
        category_labels=_CATEGORY_LABELS,
        filter_doc_type=filter_doc_type,
        filter_doc_type_label=doc_type_labels.get(filter_doc_type or ""),
        active_doc_ids=active_doc_ids,
        expiry_driving_doc_types=_EXPIRY_DRIVING_DOC_TYPES,
    )


# ── Upload aircraft document ──────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/documents/upload", methods=["GET", "POST"]
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
        category = request.form.get("category") or None
        if category and category not in DocCategory.ALL:
            category = None
        valid_from = _parse_date_field(request.form.get("valid_from", ""))
        valid_until = _parse_date_field(request.form.get("valid_until", ""))

        def _re_render(msg: str | None = None) -> str:
            if msg:
                flash(msg, "danger")
            return render_template(
                "documents/upload_form.html",
                aircraft=ac,
                component=component,
                doc_types=_AIRCRAFT_DOC_TYPES,
                categories=list(_CATEGORY_LABELS.items()),
            )

        if not file or not file.filename:
            return _re_render(_("Please select a file to upload."))

        original = secure_filename(file.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in _ALLOWED_EXTS:
            return _re_render(
                _("File type '%(ext)s' is not allowed.", ext=ext or "unknown")
            )

        if valid_from and valid_until and valid_from > valid_until:
            return _re_render(_("'Valid from' must not be after 'Valid until'."))

        # Use canonical path when a category is set and this is an aircraft doc
        if category and not component:
            tenant = _get_tenant()
            stored, mime, size = _save_upload_canonical(
                file, tenant, ac, category, title
            )
        else:
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
            category=category,
            valid_from=valid_from,
            valid_until=valid_until,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)

        # Insurance document without an explicit type: infer it from the
        # category for backward compatibility with the category-only flow.
        if doc.doc_type is None and category == DocCategory.INSURANCE and not component:
            doc.doc_type = DocType.INSURANCE_CERT

        # A new insurance/ARC document becomes the active certificate for its
        # type — supersede whichever one was active before it, then recompute
        # the aircraft's synced insurance_expiry/arc_expiry from what's now
        # active (see _recompute_expiry_field).
        if doc.doc_type in _EXPIRY_DRIVING_DOC_TYPES and not component:
            db.session.flush()
            prev_cert = Document.query.filter(
                Document.aircraft_id == ac.id,
                Document.doc_type == doc.doc_type,
                Document.superseded_by_id.is_(None),
                Document.id != doc.id,
            ).first()
            if prev_cert:
                prev_cert.superseded_by_id = doc.id
            _recompute_expiry_field(ac, doc.doc_type)

        db.session.commit()
        activity(
            "document.uploaded",
            document_id=doc.id,
            aircraft_id=ac.id,
            title=doc.title or "",
        )

        flash(_("Document uploaded."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template(
        "documents/upload_form.html",
        aircraft=ac,
        component=component,
        doc_types=_AIRCRAFT_DOC_TYPES,
        categories=list(_CATEGORY_LABELS.items()),
    )


# ── Edit aircraft document ────────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/documents/<int:document_id>/edit",
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
        category = request.form.get("category") or None
        if category and category not in DocCategory.ALL:
            category = None

        old_doc_type = doc.doc_type
        if not doc.component_id:
            new_doc_type = request.form.get("doc_type") or None
            if new_doc_type in dict(_AIRCRAFT_DOC_TYPES) or new_doc_type is None:
                doc.doc_type = new_doc_type

        old_category = doc.category
        doc.category = category

        # If the category changed and the file lives in the canonical path, move it
        if category and old_category and category != old_category and doc.filename:
            parts = doc.filename.replace("\\", "/").split("/")
            if len(parts) >= 4 and parts[2] == old_category:
                folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
                old_full = _safe_join(folder, doc.filename)
                if os.path.exists(old_full):
                    new_relpath = "/".join(parts[:2] + [category] + parts[3:])
                    new_full = _safe_join(folder, new_relpath)
                    os.makedirs(os.path.dirname(new_full), exist_ok=True)
                    try:
                        os.rename(old_full, new_full)
                        doc.filename = new_relpath
                    except OSError as exc:
                        log.warning("Could not move document file: %s", exc)

        new_valid_from = _parse_date_field(request.form.get("valid_from", ""))
        new_valid_until = _parse_date_field(request.form.get("valid_until", ""))
        if new_valid_from and new_valid_until and new_valid_from > new_valid_until:
            flash(_("'Valid from' must not be after 'Valid until'."), "danger")
            return render_template(
                "documents/edit_form.html",
                aircraft=ac,
                doc=doc,
                doc_types=_AIRCRAFT_DOC_TYPES,
                categories=list(_CATEGORY_LABELS.items()),
            )
        doc.valid_from = new_valid_from
        doc.valid_until = new_valid_until

        # Recompute whichever Aircraft field(s) this document could affect —
        # both its old and new doc_type, in case the type itself changed.
        _recompute_expiry_field(ac, old_doc_type)
        _recompute_expiry_field(ac, doc.doc_type)

        db.session.commit()
        flash(_("Document updated."), "success")
        return redirect(url_for("documents.list_documents", aircraft_id=ac.id))

    return render_template(
        "documents/edit_form.html",
        aircraft=ac,
        doc=doc,
        doc_types=_AIRCRAFT_DOC_TYPES,
        categories=list(_CATEGORY_LABELS.items()),
    )


# ── Delete aircraft document ──────────────────────────────────────────────────


@documents_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/documents/<int:document_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_document(aircraft_id: int, document_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_aircraft_document_or_404(ac, document_id)
    doc_type = doc.doc_type
    activity("document.deleted", document_id=document_id, aircraft_id=aircraft_id)
    _delete_file(doc.filename)
    db.session.delete(doc)
    db.session.commit()

    # If the deleted document drove an Aircraft expiry field, recompute it —
    # deleting the active cert reactivates whatever it had superseded (its
    # own superseded_by_id is cleared by the FK's ON DELETE SET NULL) or
    # clears the field if nothing is left.
    if doc_type in _EXPIRY_DRIVING_DOC_TYPES:
        _recompute_expiry_field(ac, doc_type)
        db.session.commit()

    flash(_("Document deleted."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))


# ── Download all aircraft documents as ZIP ────────────────────────────────────


@documents_bp.route("/aircraft/<aircraft_ref:aircraft_id>/documents/download-all")
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
    manifest_lines = ["filename\ttitle\tcategory\ttype\tuploaded\n"]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            path = os.path.join(folder, doc.filename)
            arcname = doc.original_filename
            if os.path.exists(path):
                zf.write(path, arcname=arcname)
            doc_type_label = doc.doc_type or ""
            category_label = _CATEGORY_LABELS.get(
                doc.category or "", doc.category or ""
            )
            uploaded = doc.uploaded_at.strftime("%Y-%m-%d") if doc.uploaded_at else ""
            manifest_lines.append(
                f"{arcname}\t{doc.title or ''}\t{category_label}\t{doc_type_label}\t{uploaded}\n"
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


# ── Insurance / ARC certificate quick upload ──────────────────────────────────


def _upload_expiry_cert(
    aircraft_id: int, doc_type: str, category: str, title: str
) -> ResponseReturnValue:
    """Shared handler for the small "Upload cert" forms on the aircraft
    detail page's Insurance and ARC sections — a file plus its valid-until
    date, which becomes the new active certificate for that doc_type."""
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

    valid_from = _parse_date_field(request.form.get("valid_from", ""))
    valid_until = _parse_date_field(request.form.get("valid_until", ""))
    if valid_from and valid_until and valid_from > valid_until:
        flash(_("'Valid from' must not be after 'Valid until'."), "danger")
        return redirect(url_for("aircraft.detail", aircraft_id=ac.id))

    tenant = _get_tenant()
    stored, mime, size = _save_upload_canonical(file, tenant, ac, category, title)

    prev = Document.query.filter_by(
        aircraft_id=ac.id,
        doc_type=doc_type,
        superseded_by_id=None,
        component_id=None,
    ).first()

    new_cert = Document(
        aircraft_id=ac.id,
        filename=stored,
        original_filename=original,
        mime_type=mime,
        size_bytes=size,
        title=title,
        doc_type=doc_type,
        category=category,
        valid_from=valid_from,
        valid_until=valid_until,
        is_sensitive=True,
    )
    db.session.add(new_cert)
    db.session.flush()

    if prev:
        prev.superseded_by_id = new_cert.id

    _recompute_expiry_field(ac, doc_type)
    db.session.commit()
    flash(_("Certificate uploaded."), "success")
    return redirect(url_for("aircraft.detail", aircraft_id=ac.id))


@documents_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/insurance-cert/upload", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def upload_insurance_cert(aircraft_id: int) -> ResponseReturnValue:
    return _upload_expiry_cert(
        aircraft_id,
        DocType.INSURANCE_CERT,
        DocCategory.INSURANCE,
        _("Insurance Certificate"),
    )


@documents_bp.route(
    "/aircraft/<aircraft_ref:aircraft_id>/arc-cert/upload", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def upload_arc_cert(aircraft_id: int) -> ResponseReturnValue:
    return _upload_expiry_cert(
        aircraft_id,
        DocType.ARC,
        DocCategory.AIRWORTHINESS,
        _("Airworthiness Review Certificate"),
    )


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
            except ValueError as exc:
                log.debug("Invalid valid_until date: %s", exc)

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


# ── Reconcile: scan + list + import + ignore ──────────────────────────────────


@documents_bp.route("/documents/reconcile")
@login_required
@require_role(*_OWNER_ROLES)
def list_reconcile() -> ResponseReturnValue:
    import difflib

    tenant = _get_tenant()
    pending = (
        PendingReconcile.query.filter_by(
            tenant_id=tenant.id, reconciled_at=None, ignored=False
        )
        .order_by(PendingReconcile.detected_at.desc())
        .all()
    )
    aircraft_list = (
        Aircraft.query.filter_by(tenant_id=tenant.id)
        .order_by(Aircraft.registration)
        .all()
    )

    # For entries with an unrecognised category folder, suggest the closest match
    category_suggestions: dict[
        int, tuple[str, str]
    ] = {}  # pr.id → (raw_folder, suggestion)
    for pr in pending:
        parts = pr.filepath.replace("\\", "/").split("/")
        if len(parts) >= 4:
            raw = parts[2]
            if raw.lower() not in DocCategory.ALL:
                close = difflib.get_close_matches(
                    raw.lower(), DocCategory.ALL, n=1, cutoff=0.6
                )
                if close:
                    # folder path up to and including the bad category dir
                    bad_folder = "/".join(parts[:3])
                    category_suggestions[pr.id] = (bad_folder, close[0])

    return render_template(
        "documents/reconcile.html",
        tenant=tenant,
        pending=pending,
        aircraft_list=aircraft_list,
        categories=list(_CATEGORY_LABELS.items()),
        category_suggestions=category_suggestions,
    )


@documents_bp.route("/documents/reconcile/scan", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def scan_documents() -> ResponseReturnValue:
    tenant = _get_tenant()
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")

    if not tenant.slug:
        flash(_("Set a Hangar ID in Settings before scanning for files."), "warning")
        return redirect(url_for("documents.list_reconcile"))

    slug_dir = os.path.join(folder, tenant.slug)
    if not os.path.isdir(slug_dir):
        flash(
            _(
                "No files found in '%(slug)s/'. Mount your Syncthing folder and try again.",
                slug=tenant.slug,
            ),
            "info",
        )
        return redirect(url_for("documents.list_reconcile"))

    # Build set of filenames already tracked in the documents table
    tid = tenant.id
    known: set[str] = {
        doc.filename
        for doc in Document.query.filter(
            Document.aircraft_id.in_(
                Aircraft.query.filter_by(tenant_id=tid).with_entities(Aircraft.id)
            )
        ).all()
    }
    # Prune stale pending entries whose file no longer exists on disk
    stale_removed = 0
    for pr in PendingReconcile.query.filter_by(
        tenant_id=tid, reconciled_at=None, ignored=False
    ).all():
        if not os.path.exists(os.path.join(folder, pr.filepath)):
            db.session.delete(pr)
            stale_removed += 1
    if stale_removed:
        db.session.flush()

    existing_pending: set[str] = {
        pr.filepath for pr in PendingReconcile.query.filter_by(tenant_id=tid).all()
    }

    aircraft_by_reg: dict[str, Aircraft] = {
        ac.registration.upper().replace("-", "").replace(" ", ""): ac
        for ac in Aircraft.query.filter_by(tenant_id=tid).all()
    }

    new_count = 0
    for dirpath, _dirs, filenames in os.walk(slug_dir):
        for fname in filenames:
            if fname.startswith((".", "_")):
                continue
            full = os.path.join(dirpath, fname)
            relpath = os.path.relpath(full, folder)
            # Normalise to forward slashes for DB consistency
            relpath = relpath.replace("\\", "/")

            if relpath in known or relpath in existing_pending:
                continue

            # Parse canonical path: slug/reg/category/YYYY-MM-DD - title.ext
            parts = relpath.split("/")
            aircraft_obj: Aircraft | None = None
            category: str | None = None
            title_hint: str | None = None
            date_hint: _date | None = None

            if len(parts) >= 4:
                reg_raw = parts[1].upper().replace("-", "").replace(" ", "")
                aircraft_obj = aircraft_by_reg.get(reg_raw)
                category, title_hint, date_hint = _parse_reconcile_filename(
                    parts[2], parts[3]
                )

            pr = PendingReconcile(
                tenant_id=tid,
                aircraft_id=aircraft_obj.id if aircraft_obj else None,
                filepath=relpath,
                category=category,
                title_hint=title_hint,
                date_hint=date_hint,
            )
            db.session.add(pr)
            new_count += 1

    db.session.commit()
    parts_msg = []
    if new_count:
        parts_msg.append(
            ngettext(
                "one new file queued for review",
                "%(n)s new files queued for review",
                new_count,
                n=new_count,
            )
        )
    if stale_removed:
        parts_msg.append(
            ngettext(
                "one missing file removed from queue",
                "%(n)s missing files removed from queue",
                stale_removed,
                n=stale_removed,
            )
        )
    if parts_msg:
        flash(
            _("Scan complete — %(details)s.", details=", ".join(parts_msg)), "success"
        )
    else:
        flash(_("Scan complete — no new files found."), "info")
    return redirect(url_for("documents.list_reconcile"))


@documents_bp.route("/documents/reconcile/rename-folder", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def rename_reconcile_folder() -> ResponseReturnValue:
    """Rename a misnamed category folder on disk (e.g. 'Maintenance' → 'maintenance',
    or a typo like 'maintenence' → 'maintenance'), prune stale pending entries for
    the old path, then run a fresh scan so the corrected files are picked up immediately.
    """
    import shutil

    tenant = _get_tenant()
    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")

    bad_folder = request.form.get("bad_folder", "").strip().replace("\\", "/")
    new_category = request.form.get("new_category", "").strip()

    if new_category not in DocCategory.ALL:
        flash(_("Invalid category."), "danger")
        return redirect(url_for("documents.list_reconcile"))

    if not tenant.slug or not bad_folder.startswith(tenant.slug + "/"):
        abort(403)

    old_dir = _safe_join(folder, bad_folder)
    parent_rel = "/".join(bad_folder.split("/")[:2])  # slug/reg
    new_rel = parent_rel + "/" + new_category
    new_dir = _safe_join(folder, new_rel)

    if os.path.isdir(old_dir):
        if os.path.isdir(new_dir):
            for dirpath, _dirs, filenames in os.walk(old_dir):
                rel = os.path.relpath(dirpath, old_dir)
                dest_d = os.path.join(new_dir, rel)
                os.makedirs(dest_d, exist_ok=True)
                for fname in filenames:
                    shutil.move(
                        os.path.join(dirpath, fname), os.path.join(dest_d, fname)
                    )
            shutil.rmtree(old_dir, ignore_errors=True)
        else:
            os.rename(old_dir, new_dir)

    # Prune stale pending entries for the old folder path
    tid = tenant.id
    for pr in PendingReconcile.query.filter(
        PendingReconcile.tenant_id == tid,
        PendingReconcile.filepath.like(bad_folder + "/%"),
    ).all():
        db.session.delete(pr)
    db.session.flush()

    # Inline scan: pick up the files now in the correct folder
    known: set[str] = {
        doc.filename
        for doc in Document.query.filter(
            Document.aircraft_id.in_(
                Aircraft.query.filter_by(tenant_id=tid).with_entities(Aircraft.id)
            )
        ).all()
    }
    existing_pending: set[str] = {
        pr.filepath for pr in PendingReconcile.query.filter_by(tenant_id=tid).all()
    }
    aircraft_by_reg: dict[str, Aircraft] = {
        ac.registration.upper().replace("-", "").replace(" ", ""): ac
        for ac in Aircraft.query.filter_by(tenant_id=tid).all()
    }
    new_count = 0
    if os.path.isdir(new_dir):
        for dirpath, _dirs, filenames in os.walk(new_dir):
            for fname in filenames:
                if fname.startswith((".", "_")):
                    continue
                relpath = os.path.relpath(os.path.join(dirpath, fname), folder).replace(
                    "\\", "/"
                )
                full = _safe_join(folder, relpath)
                if relpath in known or relpath in existing_pending:
                    continue
                parts = relpath.split("/")
                aircraft_obj: Aircraft | None = None
                category: str | None = None
                title_hint: str | None = None
                date_hint: _date | None = None
                if len(parts) >= 4:
                    reg_raw = parts[1].upper().replace("-", "").replace(" ", "")
                    aircraft_obj = aircraft_by_reg.get(reg_raw)
                    category, title_hint, date_hint = _parse_reconcile_filename(
                        parts[2], parts[3]
                    )
                if aircraft_obj and category:
                    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                    size = os.path.getsize(full) if os.path.exists(full) else None
                    doc = Document(
                        aircraft_id=aircraft_obj.id,
                        filename=relpath,
                        original_filename=fname,
                        mime_type=mime,
                        size_bytes=size,
                        title=title_hint,
                        category=category,
                    )
                    db.session.add(doc)
                else:
                    pr = PendingReconcile(
                        tenant_id=tid,
                        aircraft_id=aircraft_obj.id if aircraft_obj else None,
                        filepath=relpath,
                        category=category,
                        title_hint=title_hint,
                        date_hint=date_hint,
                    )
                    db.session.add(pr)
                new_count += 1

    db.session.commit()
    flash(
        ngettext(
            "Folder renamed to '%(cat)s' — one file processed.",
            "Folder renamed to '%(cat)s' — %(n)s files processed.",
            new_count,
            cat=new_category,
            n=new_count,
        ),
        "success",
    )
    return redirect(url_for("documents.list_reconcile"))


@documents_bp.route("/documents/reconcile/<int:pending_id>/import", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def import_reconcile(pending_id: int) -> ResponseReturnValue:
    tenant = _get_tenant()
    pr = PendingReconcile.query.filter_by(
        id=pending_id, tenant_id=tenant.id
    ).first_or_404()

    aircraft_id_raw = request.form.get("aircraft_id")
    try:
        aircraft_id: int | None = int(aircraft_id_raw) if aircraft_id_raw else None
    except (ValueError, TypeError):
        aircraft_id = None
    if aircraft_id is not None:
        _get_aircraft_or_404(aircraft_id)

    title = request.form.get("title", "").strip() or pr.title_hint
    category = request.form.get("category") or pr.category
    if category and category not in DocCategory.ALL:
        category = None
    valid_until_str = request.form.get("valid_until", "").strip()
    valid_until: _date | None = None
    if valid_until_str:
        with contextlib.suppress(
            ValueError
        ):  # malformed date submitted; valid_until stays None
            valid_until = _date.fromisoformat(valid_until_str)

    folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    full_path = os.path.join(folder, pr.filepath)
    mime = (
        mimetypes.guess_type(os.path.basename(pr.filepath))[0]
        or "application/octet-stream"
    )
    size = os.path.getsize(full_path) if os.path.exists(full_path) else None

    doc = Document(
        aircraft_id=aircraft_id,
        filename=pr.filepath,
        original_filename=os.path.basename(pr.filepath),
        mime_type=mime,
        size_bytes=size,
        title=title,
        category=category,
        valid_until=valid_until,
        is_sensitive=False,
    )
    db.session.add(doc)
    pr.reconciled_at = datetime.now(UTC)
    db.session.commit()

    flash(_("Document imported."), "success")
    return redirect(url_for("documents.list_reconcile"))


@documents_bp.route("/documents/reconcile/<int:pending_id>/ignore", methods=["POST"])
@login_required
@require_role(*_OWNER_ROLES)
def ignore_reconcile(pending_id: int) -> ResponseReturnValue:
    tenant = _get_tenant()
    pr = PendingReconcile.query.filter_by(
        id=pending_id, tenant_id=tenant.id
    ).first_or_404()
    pr.ignored = True
    db.session.commit()
    flash(_("File ignored."), "info")
    return redirect(url_for("documents.list_reconcile"))
