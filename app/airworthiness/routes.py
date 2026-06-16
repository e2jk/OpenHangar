import os
from datetime import date

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
from flask_babel import gettext as _, ngettext  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AirworthinessDocument,
    AirworthinessDocStatus,
    AirworthinessDocType,
    AirworthinessDocumentStatus,
    Component,
    EASASourceNode,
    InstalledSTC,
    Role,
    TenantUser,
    db,
)
from utils import login_required, require_role, user_can_access_aircraft  # pyright: ignore[reportMissingImports]

airworthiness_bp = Blueprint("airworthiness", __name__)

_OWNER_ROLES = (Role.ADMIN, Role.OWNER)
_CREW_ROLES = (Role.ADMIN, Role.OWNER, Role.PILOT, Role.MAINTENANCE)


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


def _get_node_or_404(aircraft: Aircraft, node_id: int) -> EASASourceNode:
    node = db.session.get(EASASourceNode, node_id)
    if not node or node.component.aircraft_id != aircraft.id:
        abort(404)
    return node


def _get_doc_or_404(aircraft: Aircraft, doc_id: int) -> AirworthinessDocument:
    doc = db.session.get(AirworthinessDocument, doc_id)
    if not doc:
        abort(404)
    # Document belongs to this aircraft if its component belongs to it
    component = doc.component or (
        doc.source_node.component if doc.source_node else None
    )
    if not component or component.aircraft_id != aircraft.id:
        abort(404)
    return doc


def _get_stc_or_404(aircraft: Aircraft, stc_id: int) -> InstalledSTC:
    stc = db.session.get(InstalledSTC, stc_id)
    if not stc or stc.aircraft_id != aircraft.id:
        abort(404)
    return stc


def _status_for(aircraft_id: int, doc_id: int) -> AirworthinessDocumentStatus | None:
    return AirworthinessDocumentStatus.query.filter_by(  # type: ignore[no-any-return]
        aircraft_id=aircraft_id, document_id=doc_id
    ).first()


# ── Dashboard ─────────────────────────────────────────────────────────────────


@airworthiness_bp.route("/aircraft/<int:aircraft_id>/airworthiness/")
@login_required
@require_role(*_CREW_ROLES)
def dashboard(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    # Gather all documents for this aircraft through its components
    component_ids = [c.id for c in ac.components]  # type: ignore[attr-defined]

    # Documents via EASA source nodes
    synced_docs = (
        AirworthinessDocument.query.join(EASASourceNode)
        .filter(EASASourceNode.component_id.in_(component_ids))
        .all()
        if component_ids
        else []
    )
    # Manual documents attached directly to components
    manual_docs = (
        AirworthinessDocument.query.filter(
            AirworthinessDocument.component_id.in_(component_ids),
            AirworthinessDocument.source_node_id.is_(None),
        ).all()
        if component_ids
        else []
    )
    all_docs = synced_docs + manual_docs

    # Build status map  {doc_id: AirworthinessDocumentStatus}
    existing_statuses = {
        s.document_id: s
        for s in AirworthinessDocumentStatus.query.filter_by(
            aircraft_id=aircraft_id
        ).all()
    }

    # Attach status to each doc (or None if not yet created)
    doc_rows = []
    for doc in all_docs:
        st = existing_statuses.get(doc.id)
        component = doc.component or (
            doc.source_node.component if doc.source_node else None
        )
        doc_rows.append({"doc": doc, "status": st, "component": component})

    # Sort: pending first, then by doc_type, then reference
    _STATUS_ORDER = {
        AirworthinessDocStatus.PENDING_REVIEW: 0,
        AirworthinessDocStatus.QUESTION: 1,
        AirworthinessDocStatus.DEFERRED: 2,
        AirworthinessDocStatus.COMPLIED: 3,
        AirworthinessDocStatus.NOT_APPLICABLE: 4,
        None: 0,
    }
    doc_rows.sort(
        key=lambda r: (
            _STATUS_ORDER.get(r["status"].status if r["status"] else None, 0),
            r["doc"].doc_type,
            r["doc"].reference,
        )
    )

    # Summary counts
    counts: dict[str, int] = {s: 0 for s in AirworthinessDocStatus.ALL}
    counts["total"] = len(all_docs)
    for row in doc_rows:
        st_val = (
            row["status"].status
            if row["status"]
            else AirworthinessDocStatus.PENDING_REVIEW
        )
        counts[st_val] = counts.get(st_val, 0) + 1

    # Source nodes grouped by component
    nodes_by_component: dict[int, list[EASASourceNode]] = {}
    for comp in ac.components:  # type: ignore[attr-defined]
        if comp.easa_source_nodes:
            nodes_by_component[comp.id] = comp.easa_source_nodes

    is_production = os.environ.get("OPENHANGAR_ENV", "production") == "production"
    return render_template(
        "airworthiness/dashboard.html",
        aircraft=ac,
        doc_rows=doc_rows,
        counts=counts,
        nodes_by_component=nodes_by_component,
        doc_types=AirworthinessDocType,
        statuses=AirworthinessDocStatus,
        installed_stcs=ac.installed_stcs,
        is_production=is_production,
    )


# ── EASA sync (manual trigger) ────────────────────────────────────────────────


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/sync", methods=["POST"]
)
@login_required
@require_role(*_OWNER_ROLES)
def trigger_sync(aircraft_id: int) -> ResponseReturnValue:
    if os.environ.get("OPENHANGAR_ENV", "production") != "production":
        abort(403)
    ac = _get_aircraft_or_404(aircraft_id)
    from airworthiness_sync import sync_aircraft  # pyright: ignore[reportMissingImports]

    added, errors = sync_aircraft(ac)
    if errors:
        flash(
            ngettext(
                "Sync completed with errors on one node. Check logs.",
                "Sync completed with errors on %(n)s nodes. Check logs.",
                errors,
                n=errors,
            ),
            "warning",
        )
    if added:
        flash(
            ngettext(
                "One new document discovered.",
                "%(n)s new documents discovered.",
                added,
                n=added,
            ),
            "success",
        )
    else:
        flash(_("No new documents found."), "info")
    return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))


# ── EASA source nodes ─────────────────────────────────────────────────────────


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/nodes/new",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def add_node(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    components = [c for c in ac.components if not c.removed_at]  # type: ignore[attr-defined]

    if request.method == "POST":
        component_id = request.form.get("component_id", type=int)
        comp = db.session.get(Component, component_id)
        if not comp or comp.aircraft_id != aircraft_id:
            abort(400)

        node = EASASourceNode(
            component_id=component_id,
            tc_holder_node_id=request.form["tc_holder_node_id"].strip(),
            tc_holder_name=request.form["tc_holder_name"].strip(),
            type_node_id=request.form["type_node_id"].strip(),
            type_name=request.form["type_name"].strip(),
            model_node_id=request.form["model_node_id"].strip(),
            model_name=request.form["model_name"].strip(),
        )
        db.session.add(node)
        db.session.commit()
        flash(_("EASA source node added."), "success")
        return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))

    preselect_component_id = request.args.get("component_id", type=int)
    return render_template(
        "airworthiness/node_form.html",
        aircraft=ac,
        components=components,
        preselect_component_id=preselect_component_id,
    )


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/nodes/<int:node_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_node(aircraft_id: int, node_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    node = _get_node_or_404(ac, node_id)
    db.session.delete(node)
    db.session.commit()
    flash(_("EASA source node removed."), "success")
    return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))


# ── Documents ─────────────────────────────────────────────────────────────────


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/documents/new",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def add_document(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    components = [c for c in ac.components if not c.removed_at]  # type: ignore[attr-defined]

    if request.method == "POST":
        component_id = request.form.get("component_id", type=int)
        comp = db.session.get(Component, component_id)
        if not comp or comp.aircraft_id != aircraft_id:
            abort(400)

        expiry_raw = request.form.get("expiry_date", "").strip()
        expiry = date.fromisoformat(expiry_raw) if expiry_raw else None

        doc = AirworthinessDocument(
            doc_type=request.form["doc_type"].strip(),
            reference=request.form["reference"].strip(),
            title=request.form.get("title", "").strip() or None,
            component_id=component_id,
            doc_url=request.form.get("doc_url", "").strip() or None,
            expiry_date=expiry,
        )
        db.session.add(doc)
        db.session.flush()

        status = AirworthinessDocumentStatus(
            aircraft_id=aircraft_id,
            document_id=doc.id,
            status=AirworthinessDocStatus.PENDING_REVIEW,
        )
        db.session.add(status)
        db.session.commit()
        flash(_("Document added."), "success")
        return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))

    return render_template(
        "airworthiness/document_form.html",
        aircraft=ac,
        components=components,
        doc_types=AirworthinessDocType,
    )


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/documents/<int:doc_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_document(aircraft_id: int, doc_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_doc_or_404(ac, doc_id)
    if not doc.is_manual:
        flash(_("Only manually added documents can be deleted."), "danger")
        return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))
    db.session.delete(doc)
    db.session.commit()
    flash(_("Document deleted."), "success")
    return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))


# ── Status updates ────────────────────────────────────────────────────────────


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/documents/<int:doc_id>/status",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_CREW_ROLES)
def update_status(aircraft_id: int, doc_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    doc = _get_doc_or_404(ac, doc_id)
    st = _status_for(aircraft_id, doc_id)

    if request.method == "POST":
        new_status = request.form.get("status", "").strip()
        if new_status not in AirworthinessDocStatus.ALL:
            abort(400)

        compliance_raw = request.form.get("compliance_date", "").strip()
        review_raw = request.form.get("next_review_date", "").strip()

        if st is None:
            st = AirworthinessDocumentStatus(
                aircraft_id=aircraft_id, document_id=doc_id
            )
            db.session.add(st)

        st.status = new_status
        st.notes = request.form.get("notes", "").strip() or None
        st.compliance_date = (
            date.fromisoformat(compliance_raw) if compliance_raw else None
        )
        st.next_review_date = date.fromisoformat(review_raw) if review_raw else None
        db.session.commit()
        flash(_("Status updated."), "success")
        return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))

    return render_template(
        "airworthiness/status_form.html",
        aircraft=ac,
        doc=doc,
        current_status=st,
        statuses=AirworthinessDocStatus,
    )


# ── Installed STCs ────────────────────────────────────────────────────────────


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/stcs/new",
    methods=["GET", "POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def add_stc(aircraft_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)

    if request.method == "POST":
        install_raw = request.form.get("installation_date", "").strip()
        stc = InstalledSTC(
            aircraft_id=aircraft_id,
            stc_number=request.form["stc_number"].strip(),
            title=request.form.get("title", "").strip() or None,
            tc_holder=request.form.get("tc_holder", "").strip() or None,
            installation_date=date.fromisoformat(install_raw) if install_raw else None,
            notes=request.form.get("notes", "").strip() or None,
        )
        db.session.add(stc)
        db.session.commit()
        flash(_("Installed STC added."), "success")
        return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))

    return render_template("airworthiness/stc_form.html", aircraft=ac)


@airworthiness_bp.route(
    "/aircraft/<int:aircraft_id>/airworthiness/stcs/<int:stc_id>/delete",
    methods=["POST"],
)
@login_required
@require_role(*_OWNER_ROLES)
def delete_stc(aircraft_id: int, stc_id: int) -> ResponseReturnValue:
    ac = _get_aircraft_or_404(aircraft_id)
    stc = _get_stc_or_404(ac, stc_id)
    db.session.delete(stc)
    db.session.commit()
    flash(_("Installed STC removed."), "success")
    return redirect(url_for("airworthiness.dashboard", aircraft_id=aircraft_id))
