"""
Users blueprint — user management, invitations, and role changes.
Only ADMIN/OWNER roles can manage users; the invitation-accept route is public.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt  # pyright: ignore[reportMissingImports]
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

from models import Role, Tenant, TenantUser, User, UserInvitation, db
from utils import current_user_role, login_required, require_role

users_bp = Blueprint("users", __name__, url_prefix="/config/users")

_OWNER_ROLES = {Role.ADMIN, Role.OWNER}
_INVITATION_EXPIRY_DAYS = 7


@users_bp.before_request
def _block_in_demo():
    if os.environ.get("FLASK_ENV") == "demo":
        abort(403)

ROLE_LABELS = {
    Role.ADMIN:       "Admin",
    Role.OWNER:       "Owner",
    Role.PILOT:       "Pilot / Renter",
    Role.MAINTENANCE: "Maintenance",
    Role.VIEWER:      "Viewer",
}


def _tenant_id() -> int:
    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)
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
    return render_template(
        "users/list.html",
        tenant_users=tenant_users,
        invitations=invitations,
        role_labels=ROLE_LABELS,
        current_user_id=session["user_id"],
        all_roles=[r for r in Role if r not in (Role.ADMIN,)],
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

    inv = UserInvitation(
        tenant_id=tid,
        invited_by_user_id=session["user_id"],
        email=email_raw,
        role=role,
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
        pass


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
