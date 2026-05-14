"""
Users blueprint — user management, invitations, and role changes.
Only ADMIN/OWNER roles can manage users; the invitation-accept route is public.
"""
import json as _json
import os
from datetime import datetime, timedelta, timezone

import bcrypt  # pyright: ignore[reportMissingImports]
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
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import Aircraft, PermissionBit, Role, TenantUser, User, UserAircraftAccess, UserAllAircraftAccess, UserInvitation, db
from utils import login_required, require_role

users_bp = Blueprint("users", __name__, url_prefix="/config/users")

_INVITATION_EXPIRY_DAYS = 7


@users_bp.before_request
def _block_in_demo():
    if os.environ.get("FLASK_ENV") == "demo":
        abort(403)

ROLE_LABELS = {
    Role.ADMIN:       "Admin",
    Role.OWNER:       "Owner",
    Role.PILOT:       "Pilot / Renter",
    Role.STUDENT:     "Student",
    Role.INSTRUCTOR:  "Instructor",
    Role.MAINTENANCE: "Maintenance",
    Role.VIEWER:      "Viewer",
}


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover
    return tu.tenant_id


# ── User list ─────────────────────────────────────────────────────────────────

@users_bp.route("/")
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def list_users():
    tid = _tenant_id()
    tenant_users = (
        TenantUser.query
        .filter_by(tenant_id=tid)
        .join(User)
        .all()
    )
    invitations = (
        UserInvitation.query
        .filter_by(tenant_id=tid)
        .filter(UserInvitation.accepted_at.is_(None))
        .order_by(UserInvitation.created_at.desc())
        .all()
    )
    all_aircraft = (
        Aircraft.query
        .filter_by(tenant_id=tid)
        .order_by(Aircraft.registration)
        .all()
    )
    # Build per-user set of accessible aircraft IDs for the template
    access_rows = UserAircraftAccess.query.filter(
        UserAircraftAccess.aircraft_id.in_([ac.id for ac in all_aircraft])
    ).all()
    user_aircraft_ids: dict[int, set[int]] = {}
    for row in access_rows:
        user_aircraft_ids.setdefault(row.user_id, set()).add(row.aircraft_id)
    # Build per-user all-planes flag
    all_planes_user_ids: set[int] = {
        row.user_id
        for row in UserAllAircraftAccess.query.filter_by(tenant_id=tid).all()
    }
    return render_template(
        "users/list.html",
        tenant_users=tenant_users,
        invitations=invitations,
        role_labels=ROLE_LABELS,
        current_user_id=session["user_id"],
        all_roles=[r for r in Role if r not in (Role.ADMIN,)],
        all_aircraft=all_aircraft,
        user_aircraft_ids=user_aircraft_ids,
        all_planes_user_ids=all_planes_user_ids,
        Role=Role,
    )


# ── Invite ────────────────────────────────────────────────────────────────────

@users_bp.route("/invite", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def invite():
    tid = _tenant_id()
    email_raw = request.form.get("email", "").strip().lower() or None
    role_raw = request.form.get("role", Role.PILOT.value)
    try:
        role = Role(role_raw)
    except ValueError:
        role = Role.PILOT
    if role == Role.ADMIN:
        role = Role.OWNER

    # Parse aircraft access checkboxes (only meaningful for non-owner roles)
    invited_aircraft_ids: list[int] | None = None
    if role not in (Role.ADMIN, Role.OWNER):
        raw_ids = request.form.getlist("aircraft_ids")
        try:
            invited_aircraft_ids = [int(x) for x in raw_ids if x]
        except ValueError:
            invited_aircraft_ids = []

    inv = UserInvitation(
        tenant_id=tid,
        invited_by_user_id=session["user_id"],
        email=email_raw,
        role=role,
        aircraft_ids=invited_aircraft_ids,
        expires_at=datetime.now(timezone.utc) + timedelta(days=_INVITATION_EXPIRY_DAYS),
    )
    db.session.add(inv)
    db.session.commit()

    accept_url = url_for("users.accept_invite", token=inv.token, _external=True)

    if email_raw:
        _try_send_invite_email(email_raw, accept_url, role)

    flash(_("Invitation created. Share this link: %(url)s", url=accept_url), "success")
    return redirect(url_for("users.list_users"))


def _try_send_invite_email(to: str, accept_url: str, role: Role) -> None:
    try:
        from services.email_service import EmailNotConfiguredError, EmailSendError, send_email  # pyright: ignore[reportMissingImports]
        send_email(
            to=to,
            subject=_("You've been invited to OpenHangar"),
            text_body=(
                f"You have been invited to join an OpenHangar hangar as {ROLE_LABELS[role]}.\n\n"
                f"Accept your invitation here:\n{accept_url}\n\n"
                f"This link expires in {_INVITATION_EXPIRY_DAYS} days."
            ),
        )
    except Exception:
        current_app.logger.warning("Failed to send invitation email to %s", to, exc_info=True)


# ── Accept invitation ─────────────────────────────────────────────────────────

@users_bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    inv = UserInvitation.query.filter_by(token=token).first_or_404()

    if inv.is_accepted:
        flash(_("This invitation has already been used."), "warning")
        return redirect(url_for("auth.login"))

    if inv.is_expired:
        flash(_("This invitation has expired."), "danger")
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("users/invite_accept.html", invitation=inv,
                               role_labels=ROLE_LABELS)

    # POST — create user
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    password2 = request.form.get("password2", "")

    errors = []
    if not email or "@" not in email:
        errors.append(_("A valid email address is required."))
    if len(password) < 12:
        errors.append(_("Password must be at least 12 characters."))
    if password != password2:
        errors.append(_("Passwords do not match."))
    if User.query.filter_by(email=email).first():
        errors.append(_("An account with this email already exists."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("users/invite_accept.html", invitation=inv,
                               role_labels=ROLE_LABELS, prefill_email=email)

    user = User(
        email=email,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    db.session.add(TenantUser(
        user_id=user.id,
        tenant_id=inv.tenant_id,
        role=inv.role,
    ))

    # Grant per-aircraft access for non-owner roles
    if inv.role not in (Role.ADMIN, Role.OWNER) and inv.aircraft_ids:
        for acid in inv.aircraft_ids:
            db.session.add(UserAircraftAccess(user_id=user.id, aircraft_id=acid))

    inv.accepted_at = datetime.now(timezone.utc)
    db.session.commit()

    flash(_("Account created. You can now log in."), "success")
    return redirect(url_for("auth.login"))


# ── Change role ───────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/role", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def change_role(user_id: int):
    tid = _tenant_id()
    if user_id == session["user_id"]:
        flash(_("You cannot change your own role."), "danger")
        return redirect(url_for("users.list_users"))

    tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()
    role_raw = request.form.get("role", "")
    try:
        new_role = Role(role_raw)
    except ValueError:
        abort(400)
    if new_role == Role.ADMIN:
        abort(400)
    tu.role = new_role
    # When promoted to owner/admin, per-aircraft access rows are no longer needed
    if new_role in (Role.ADMIN, Role.OWNER):
        UserAircraftAccess.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    flash(_("Role updated."), "success")
    return redirect(url_for("users.list_users"))


# ── Revoke access ─────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/revoke", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def revoke_access(user_id: int):
    tid = _tenant_id()
    if user_id == session["user_id"]:
        flash(_("You cannot revoke your own access."), "danger")
        return redirect(url_for("users.list_users"))

    tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()
    user = db.session.get(User, user_id)
    if user:
        user.is_active = False
    db.session.delete(tu)
    db.session.commit()
    flash(_("Access revoked."), "success")
    return redirect(url_for("users.list_users"))


# ── Revoke pending invitation ─────────────────────────────────────────────────

@users_bp.route("/invite/<int:inv_id>/revoke", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def revoke_invite(inv_id: int):
    tid = _tenant_id()
    inv = UserInvitation.query.filter_by(id=inv_id, tenant_id=tid).first_or_404()
    db.session.delete(inv)
    db.session.commit()
    flash(_("Invitation revoked."), "success")
    return redirect(url_for("users.list_users"))


# ── Update aircraft access ────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/aircraft-access", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def update_aircraft_access(user_id: int):
    tid = _tenant_id()
    tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()

    # Owners/admins bypass the access table — no rows needed
    if tu.role in (Role.ADMIN, Role.OWNER):
        flash(_("Owners and admins always have full fleet access."), "info")
        return redirect(url_for("users.list_users"))

    raw_ids = request.form.getlist("aircraft_ids")
    try:
        new_ids = {int(x) for x in raw_ids if x}
    except ValueError:
        abort(400)

    # Verify all aircraft belong to this tenant
    valid_ids = {
        ac.id for ac in Aircraft.query.filter(
            Aircraft.id.in_(new_ids), Aircraft.tenant_id == tid
        ).all()
    }

    # Replace existing access rows for this user in this tenant
    existing = UserAircraftAccess.query.filter(
        UserAircraftAccess.user_id == user_id,
        UserAircraftAccess.aircraft_id.in_(
            [ac.id for ac in Aircraft.query.filter_by(tenant_id=tid).all()]
        ),
    ).all()
    for row in existing:
        db.session.delete(row)

    for acid in valid_ids:
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=acid))

    db.session.commit()
    flash(_("Aircraft access updated."), "success")
    return redirect(url_for("users.list_users"))


# ── Toggle all-planes access ──────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/all-planes", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def toggle_all_planes(user_id: int):
    tid = _tenant_id()
    tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()

    if tu.role in (Role.ADMIN, Role.OWNER):
        flash(_("Owners and admins always have full fleet access."), "info")
        return redirect(url_for("users.list_users"))

    existing = UserAllAircraftAccess.query.filter_by(user_id=user_id, tenant_id=tid).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(UserAllAircraftAccess(user_id=user_id, tenant_id=tid))
    db.session.commit()
    flash(_("All-aircraft access updated."), "success")
    return redirect(url_for("users.list_users"))


# ── Toggle user capability flags ─────────────────────────────────────────────

@users_bp.route("/<int:user_id>/flags", methods=["POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def update_user_flags(user_id: int):
    tid = _tenant_id()
    TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()
    if user_id == session["user_id"]:
        flash(_("You cannot change your own capability flags."), "danger")
        return redirect(url_for("users.list_users"))

    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    user.is_pilot = "is_pilot" in request.form
    user.is_maintenance = "is_maintenance" in request.form
    user.view_only = "view_only" in request.form
    db.session.commit()
    flash(_("User flags updated."), "success")
    return redirect(url_for("users.list_users"))


# ── Per-aircraft permission editor ───────────────────────────────────────────

_PERM_BITS: list[tuple[int, str, str]] = [
    (PermissionBit.VIEW_AIRCRAFT,      "view_aircraft",      "View"),
    (PermissionBit.EDIT_AIRCRAFT,      "edit_aircraft",      "Edit aircraft"),
    (PermissionBit.READ_MAINT_FULL,    "read_maint_full",    "Full maintenance"),
    (PermissionBit.READ_MAINT_LIMITED, "read_maint_limited", "Limited maintenance"),
    (PermissionBit.WRITE_MAINTENANCE,  "write_maintenance",  "Write maintenance"),
    (PermissionBit.EDIT_COMPONENTS,    "edit_components",    "Edit components"),
    (PermissionBit.WRITE_LOGBOOK,      "write_logbook",      "Write logbook"),
    (PermissionBit.RESERVE_AIRCRAFT,   "reserve_aircraft",   "Reserve"),
]
_BIT_VALUES = [bit for bit, _, _ in _PERM_BITS]


@users_bp.route("/<int:user_id>/permissions", methods=["GET", "POST"])
@login_required
@require_role(Role.ADMIN, Role.OWNER)
def edit_permissions(user_id: int):
    tid = _tenant_id()
    tu = TenantUser.query.filter_by(user_id=user_id, tenant_id=tid).first_or_404()
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if tu.role in (Role.ADMIN, Role.OWNER):
        flash(_("Owners and admins always have full fleet access — no custom permissions needed."), "info")
        return redirect(url_for("users.list_users"))

    all_aircraft = Aircraft.query.filter_by(tenant_id=tid).order_by(Aircraft.registration).all()
    all_planes_row = UserAllAircraftAccess.query.filter_by(user_id=user_id, tenant_id=tid).first()
    aircraft_access = {
        row.aircraft_id: row
        for row in UserAircraftAccess.query.filter(
            UserAircraftAccess.user_id == user_id,
            UserAircraftAccess.aircraft_id.in_([ac.id for ac in all_aircraft]),
        ).all()
    }

    role_default_mask = PermissionBit.ROLE_DEFAULTS.get(tu.role.value, 0)

    def _parse_mask(scope: str) -> int | None:
        mask = 0
        for b in _BIT_VALUES:
            if request.form.get(f"bit_{scope}_{b}"):
                mask |= b
        return None if mask == role_default_mask else mask

    if request.method == "POST":
        if all_planes_row:
            all_planes_row.permissions_mask = _parse_mask("all")
        for ac in all_aircraft:
            row = aircraft_access.get(ac.id)
            if row:
                row.permissions_mask = _parse_mask(str(ac.id))
        db.session.commit()
        flash(_("Permissions updated."), "success")
        return redirect(url_for("users.edit_permissions", user_id=user_id))

    def _bits_for(mask: int | None) -> set[int]:
        m = mask if mask is not None else role_default_mask
        return {b for b in _BIT_VALUES if m & b}

    all_planes_bits = _bits_for(all_planes_row.permissions_mask) if all_planes_row else set()
    per_aircraft_bits: dict[int, set[int]] = {
        ac.id: _bits_for(aircraft_access[ac.id].permissions_mask)
        for ac in all_aircraft if ac.id in aircraft_access
    }
    per_aircraft_is_custom: dict[int, bool] = {
        ac.id: aircraft_access[ac.id].permissions_mask is not None
        for ac in all_aircraft if ac.id in aircraft_access
    }

    return render_template(
        "users/permissions.html",
        tu=tu,
        user=user,
        all_aircraft=all_aircraft,
        all_planes_row=all_planes_row,
        all_planes_bits=all_planes_bits,
        all_planes_is_custom=all_planes_row is not None and all_planes_row.permissions_mask is not None,
        per_aircraft_bits=per_aircraft_bits,
        per_aircraft_is_custom=per_aircraft_is_custom,
        aircraft_access=aircraft_access,
        perm_bits=_PERM_BITS,
        role_default_mask=role_default_mask,
        role_defaults_json=_json.dumps(PermissionBit.ROLE_DEFAULTS),
    )
