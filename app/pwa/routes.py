import contextlib
import os
import re as _re
import shutil
import uuid
from datetime import date as _date
from typing import cast

from flask import (  # pyright: ignore[reportMissingImports]
    Blueprint,
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
    db,
)
from utils import (  # pyright: ignore[reportMissingImports]
    accessible_aircraft,
    current_membership,
    db_id,
    has_pilot_access,
    login_required,
)

from pwa import pending

pwa_bp = Blueprint("pwa", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)
_MAINT_ROLES = (Role.ADMIN, Role.OWNER, Role.MAINTENANCE)

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
    tu = current_membership()
    if not tu:
        return []
    return cast(list[Aircraft], accessible_aircraft(tu.tenant_id).all())


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


def _permitted_destinations(mimetypes: list[str]) -> list[str]:
    """Destinations that accept these files and that the user may open:
    documents and expenses for owners and admins, maintenance also for
    maintenance users, flight photos for anyone with pilot access. A form
    takes one file: several files can only become aircraft documents."""
    tu = current_membership()
    role = tu.role if tu else None
    allowed = {
        "document": role in _OWNER_ROLES,
        "expense": role in _OWNER_ROLES,
        "maintenance": role in _MAINT_ROLES,
        "flight_photo": has_pilot_access(),
    }
    return [
        dest
        for dest in _allowed_destinations(mimetypes)
        if allowed[dest] and (dest == "document" or len(mimetypes) == 1)
    ]


def _render_chooser(share: pending.PendingShare) -> ResponseReturnValue:
    return render_template(
        "pwa/share_target.html",
        pending_files=share.files,
        title=share.title,
        destinations=_permitted_destinations(share.mimetypes),
        dest_labels=_dest_labels(),
        aircraft_list=_get_user_aircraft(),
        categories=_category_labels(),
    )


def _chosen_aircraft() -> Aircraft | None:
    tu = current_membership()
    aircraft_id = request.form.get("aircraft_id", type=db_id)
    if tu is None or aircraft_id is None:
        return None
    return cast(
        Aircraft | None,
        accessible_aircraft(tu.tenant_id).filter(Aircraft.id == aircraft_id).first(),
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@pwa_bp.route("/pwa/shared", methods=["GET"])
@login_required
def share_target_get() -> ResponseReturnValue:
    """Back to the destination choice of a share still pending, if any."""
    share = pending.current()
    if share is None:
        return redirect(url_for("index"))
    return _render_chooser(share)


@pwa_bp.route("/pwa/shared", methods=["POST"])
@login_required
def share_target() -> ResponseReturnValue:
    files = request.files.getlist("files")
    title = request.form.get("title", "").strip()

    valid_files = [f for f in files if f.filename]
    if not valid_files:
        flash(_("No files were shared."), "warning")
        return redirect(url_for("index"))

    share = pending.start(valid_files, title[:255], int(session["user_id"]))
    return _render_chooser(share)


@pwa_bp.route("/pwa/shared/discard", methods=["POST"])
@login_required
def share_discard() -> ResponseReturnValue:
    pending.discard()
    flash(_("The shared file was discarded."), "info")
    return redirect(url_for("index"))


@pwa_bp.route("/pwa/shared/confirm", methods=["POST"])
@login_required
def share_confirm() -> ResponseReturnValue:
    share = pending.current()
    if share is None:
        flash(_("No pending shared files. Please try sharing again."), "warning")
        return redirect(url_for("index"))

    destination = request.form.get("destination", "")
    if destination not in _permitted_destinations(share.mimetypes):
        pending.discard()
        flash(_("Unknown destination."), "danger")
        return redirect(url_for("index"))

    if destination == "flight_photo":
        pending.choose(share, destination, None)
        return redirect(url_for("flights.log_flight"))

    ac = _chosen_aircraft()
    if ac is None:
        flash(_("Please select an aircraft."), "danger")
        return _render_chooser(share)

    if destination == "document":
        return _process_document(share, ac)

    pending.choose(share, destination, ac.id)
    if destination == "expense":
        return redirect(url_for("expenses.add_expense", aircraft_id=ac.id))
    # destination == "maintenance": pick the item serviced on the list.
    flash(
        _("Choose the item you serviced: your shared file is attached to its form."),
        "info",
    )
    return redirect(url_for("maintenance.list_triggers", aircraft_id=ac.id))


def _process_document(share: pending.PendingShare, ac: Aircraft) -> ResponseReturnValue:
    tenant = db.session.get(Tenant, ac.tenant_id)
    assert tenant is not None  # FK guarantees this

    _raw_category = request.form.get("category") or ""
    category = next((c for c in DocCategory.ALL if c == _raw_category), None)
    is_sensitive = bool(request.form.get("is_sensitive"))
    valid_until_str = request.form.get("valid_until", "").strip()
    valid_until: _date | None = None
    if valid_until_str:
        with contextlib.suppress(ValueError):
            valid_until = _date.fromisoformat(valid_until_str)

    doc_title = (request.form.get("title") or share.title).strip()[:128] or None
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")

    for shared in share.files:
        original_name = shared.original
        ext = pending.MIME_TO_EXT.get(shared.mime, "")

        if category:
            slug = _ensure_tenant_slug(tenant)
            safe_reg = ac.registration.replace("/", "-").replace(" ", "-").upper()
            today = _date.today().isoformat()
            safe_t = os.path.basename(
                _safe_path_component(doc_title or os.path.splitext(original_name)[0])
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
            stored = f"doc_share_{uuid.uuid4().hex[:12]}{ext}"
            os.makedirs(upload_folder, exist_ok=True)
            dest_full = os.path.join(upload_folder, stored)

        shutil.copy2(share.path(shared), dest_full)
        size = os.path.getsize(dest_full)

        doc = Document(
            aircraft_id=ac.id,
            filename=stored,
            original_filename=original_name,
            mime_type=shared.mime,
            size_bytes=size,
            title=doc_title,
            category=category,
            valid_until=valid_until,
            is_sensitive=is_sensitive,
        )
        db.session.add(doc)

    db.session.commit()
    pending.discard()
    flash(_("Document uploaded."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))
