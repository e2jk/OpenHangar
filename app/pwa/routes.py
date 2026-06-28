import contextlib
import mimetypes as _mimetypes
import os
import re as _re
import shutil
import tempfile
import uuid
from datetime import date as _date
from typing import cast

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
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DocCategory,
    Document,
    Role,
    Tenant,
    TenantUser,
    db,
)
from utils import login_required  # pyright: ignore[reportMissingImports]

pwa_bp = Blueprint("pwa", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)

# MIME types accepted per destination
_DEST_ACCEPT: dict[str, frozenset[str]] = {
    "document": frozenset(
        {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
            "image/heic",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/plain",
        }
    ),
    "expense": frozenset(
        {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}
    ),
    "maintenance": frozenset({"application/pdf", "image/jpeg", "image/png"}),
    "flight_photo": frozenset(
        {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
    ),
}


def _allowed_destinations(mimetypes: list[str]) -> list[str]:
    """Return destinations that accept every provided MIME type."""
    mt_set = set(mimetypes)
    return [dest for dest in _DEST_ACCEPT if mt_set.issubset(_DEST_ACCEPT[dest])]


def _dest_labels() -> dict[str, str]:
    return {
        "document": _("Aircraft document"),
        "expense": _("Expense receipt"),
        "maintenance": _("Maintenance record"),
        "flight_photo": _("Flight photo"),
    }


def _category_labels() -> list[tuple[str, str]]:
    return [
        (DocCategory.MAINTENANCE, _("Maintenance")),
        (DocCategory.INSURANCE, _("Insurance")),
        (DocCategory.POH, _("POH / Flight Manual")),
        (DocCategory.AIRWORTHINESS, _("Airworthiness")),
        (DocCategory.LOGBOOK, _("Logbook")),
        (DocCategory.INVOICE, _("Invoice")),
        (DocCategory.OTHER, _("Other")),
        (DocCategory.UNCATEGORISED, _("Uncategorised")),
    ]


def _get_user_aircraft() -> list[Aircraft]:
    tu = TenantUser.query.filter_by(user_id=session.get("user_id")).first()
    if not tu:
        return []
    return cast(
        list[Aircraft],
        Aircraft.query.filter_by(tenant_id=tu.tenant_id)
        .order_by(Aircraft.registration)
        .all(),
    )


def _ensure_tenant_slug(tenant: Tenant) -> str:
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
    return _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip()


def _cleanup_temp(tmp_dir: str) -> None:
    session.pop("share_pending", None)
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Routes ────────────────────────────────────────────────────────────────────


@pwa_bp.route("/pwa/shared", methods=["GET"])
@login_required
def share_target_get() -> ResponseReturnValue:
    return redirect(url_for("index"))


@pwa_bp.route("/pwa/shared", methods=["POST"])
@login_required
def share_target() -> ResponseReturnValue:
    files = request.files.getlist("files")
    title = request.form.get("title", "").strip()

    valid_files = [f for f in files if f.filename]
    if not valid_files:
        flash(_("No files were shared."), "warning")
        return redirect(url_for("index"))

    tmp_dir = tempfile.mkdtemp(prefix="oh-share-")
    saved: list[dict[str, str]] = []
    mimetypes: list[str] = []

    for f in valid_files:
        original_name = f.filename or "unnamed"
        safe_name = f"{uuid.uuid4().hex}_{os.path.basename(original_name)}"
        dest_path = os.path.join(tmp_dir, safe_name)
        f.save(dest_path)
        mime = (
            f.content_type
            or _mimetypes.guess_type(original_name)[0]
            or "application/octet-stream"
        )
        saved.append({"original": original_name, "saved": safe_name, "mime": mime})
        mimetypes.append(mime)

    session["share_pending"] = {
        "tmp_dir": tmp_dir,
        "files": saved,
        "title": title,
    }

    destinations = _allowed_destinations(mimetypes)
    return render_template(
        "pwa/share_target.html",
        pending_files=saved,
        title=title,
        destinations=destinations,
        dest_labels=_dest_labels(),
        aircraft_list=_get_user_aircraft(),
        categories=_category_labels(),
    )


@pwa_bp.route("/pwa/shared/confirm", methods=["POST"])
@login_required
def share_confirm() -> ResponseReturnValue:
    pending = session.get("share_pending")
    if not pending:
        flash(_("No pending shared files. Please try sharing again."), "warning")
        return redirect(url_for("index"))

    destination = request.form.get("destination", "")
    tmp_dir: str = pending["tmp_dir"]
    files_meta: list[dict[str, str]] = pending["files"]
    title: str = pending.get("title", "")

    if destination == "document":
        return _process_document(tmp_dir, files_meta, title)

    if destination == "expense":
        _cleanup_temp(tmp_dir)
        flash(_("File received — please attach it manually to the expense."), "info")
        aircraft_id_raw = request.form.get("aircraft_id", "")
        if aircraft_id_raw:
            try:
                return redirect(
                    url_for("expenses.add_expense", aircraft_id=int(aircraft_id_raw))
                )
            except (ValueError, TypeError):
                pass
        return redirect(url_for("index"))

    if destination == "maintenance":
        _cleanup_temp(tmp_dir)
        flash(
            _("File received — please reference it in the maintenance notes."), "info"
        )
        aircraft_id_raw = request.form.get("aircraft_id", "")
        if aircraft_id_raw:
            try:
                return redirect(
                    url_for(
                        "maintenance.list_triggers", aircraft_id=int(aircraft_id_raw)
                    )
                )
            except (ValueError, TypeError):
                pass
        return redirect(url_for("index"))

    if destination == "flight_photo":
        _cleanup_temp(tmp_dir)
        flash(
            _("File received — please attach it manually when logging the flight."),
            "info",
        )
        return redirect(url_for("flights.log_flight"))

    _cleanup_temp(tmp_dir)
    flash(_("Unknown destination."), "danger")
    return redirect(url_for("index"))


def _process_document(
    tmp_dir: str, files_meta: list[dict[str, str]], title: str
) -> ResponseReturnValue:
    tu = TenantUser.query.filter_by(user_id=session.get("user_id")).first()
    if not tu or tu.role not in _OWNER_ROLES:
        _cleanup_temp(tmp_dir)
        abort(403)

    aircraft_id_raw = request.form.get("aircraft_id", "")
    try:
        aircraft_id = int(aircraft_id_raw)
    except (ValueError, TypeError):
        _cleanup_temp(tmp_dir)
        flash(_("Please select an aircraft."), "danger")
        return redirect(url_for("index"))

    ac = Aircraft.query.filter_by(id=aircraft_id, tenant_id=tu.tenant_id).first()
    if not ac:
        _cleanup_temp(tmp_dir)
        abort(404)

    tenant = db.session.get(Tenant, tu.tenant_id)
    assert tenant is not None  # FK guarantees this

    category = request.form.get("category") or None
    if category and category not in DocCategory.ALL:
        category = None
    is_sensitive = bool(request.form.get("is_sensitive"))
    valid_until_str = request.form.get("valid_until", "").strip()
    valid_until: _date | None = None
    if valid_until_str:
        with contextlib.suppress(ValueError):
            valid_until = _date.fromisoformat(valid_until_str)

    doc_title = title.strip() or None
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")

    for file_meta in files_meta:
        src_path = os.path.join(tmp_dir, file_meta["saved"])
        original_name = file_meta["original"]
        mime = file_meta["mime"]
        ext = os.path.splitext(original_name)[1].lower()

        if category:
            slug = _ensure_tenant_slug(tenant)
            safe_reg = ac.registration.replace("/", "-").replace(" ", "-").upper()
            today = _date.today().isoformat()
            safe_t = _safe_path_component(
                doc_title or os.path.splitext(original_name)[0]
            )[:100]
            fname = f"{today} - {safe_t}{ext}"
            rel_dir = os.path.join(slug, safe_reg, category)
            full_dir = os.path.join(upload_folder, rel_dir)
            os.makedirs(full_dir, exist_ok=True)
            stored = os.path.join(rel_dir, fname)
            dest_full = os.path.join(upload_folder, stored)
            if os.path.exists(dest_full):
                base, ext2 = os.path.splitext(fname)
                stored = os.path.join(rel_dir, f"{base}_{uuid.uuid4().hex[:6]}{ext2}")
                dest_full = os.path.join(upload_folder, stored)
        else:
            stored_name = f"doc_share_{uuid.uuid4().hex[:12]}{ext}"
            os.makedirs(upload_folder, exist_ok=True)
            stored = stored_name
            dest_full = os.path.join(upload_folder, stored)

        shutil.copy2(src_path, dest_full)
        size = os.path.getsize(dest_full)

        doc = Document(
            aircraft_id=ac.id,
            filename=stored,
            original_filename=original_name,
            mime_type=mime,
            size_bytes=size,
            title=doc_title,
            category=category,
            valid_until=valid_until,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)

    db.session.commit()
    _cleanup_temp(tmp_dir)
    flash(_("Document uploaded."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))
