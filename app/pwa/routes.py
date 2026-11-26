import contextlib
from datetime import date as _date
from typing import cast

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
from services import account_scope  # pyright: ignore[reportMissingImports]
from utils import (  # pyright: ignore[reportMissingImports]
    accessible_aircraft,
    cross_site_request,
    current_membership,
    db_id,
    has_pilot_access,
    login_required,
)

from pwa import pending, shared_links

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
    # Cleaned and re-encoded on save (services.images): JPEG, PNG, WebP only.
    "flight_photo": frozenset({"image/jpeg", "image/png", "image/webp"}),
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


def _permitted_destinations(mimetypes: list[str]) -> list[str]:
    """Destinations that accept these files and that the user may open:
    documents and expenses for owners and admins, maintenance also for
    maintenance users, flight photos for anyone with pilot access. A form
    takes one file: several files can only become aircraft documents."""
    tu = current_membership()
    if account_scope.restricted(tu):
        # A Personal account has no organisation pages to send a file to.
        return []
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
    if cross_site_request():
        # Not CSRF-protected (see init.py): the share sheet itself sends
        # Sec-Fetch-Site "none", another site "cross-site".
        return abort(403)
    files = request.files.getlist("files")
    title = request.form.get("title", "").strip()

    valid_files = [f for f in files if f.filename]
    if not valid_files:
        # A link without files (an Instagram reel, a YouTube video…) can only
        # become a new place: straight to its form, nothing saved yet. The
        # server never fetches the link.
        link = shared_links.shared_link(
            request.form.get("url"), request.form.get("text")
        )
        if link is not None:
            return redirect(
                url_for(
                    "places.new_place",
                    title=shared_links.suggested_title(
                        title, request.form.get("text"), link
                    ),
                    external_url=link,
                )
            )
        flash(_("Nothing that OpenHangar can save was shared."), "warning")
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
    from documents.routes import (  # pyright: ignore[reportMissingImports]
        _save_upload,
        _save_upload_canonical,
    )

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

    # The documents' own storage helpers: the canonical
    # <tenant>/<REG>/<category>/ layout (paths checked to stay inside the
    # uploads folder), or the flat legacy name without a category.
    for index, shared in enumerate(share.files):
        with share.upload(index) as upload:
            if category:
                stored, mime, size = _save_upload_canonical(
                    upload, tenant, ac, category, doc_title
                )
            else:
                stored, mime, size = _save_upload(upload, "share")
        db.session.add(
            Document(
                aircraft_id=ac.id,
                filename=stored,
                original_filename=shared.original,
                mime_type=mime,
                size_bytes=size,
                title=doc_title,
                category=category,
                valid_until=valid_until,
                is_sensitive=is_sensitive,
            )
        )

    db.session.commit()
    pending.discard()
    flash(_("Document uploaded."), "success")
    return redirect(url_for("documents.list_documents", aircraft_id=ac.id))
