import os

import bcrypt
import pyotp
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from models import Role, Tenant, TenantUser, User, db

auth_bp = Blueprint("auth", __name__)


def _no_users() -> bool:
    return db.session.query(User).count() == 0


def _is_demo() -> bool:
    return os.environ.get("FLASK_ENV") == "demo"


# ── /login ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if _no_users():
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        return _login_post()

    step = request.args.get("step", "credentials")
    # TOTP step only accessible after credentials have been verified
    if step == "totp" and not session.get("login_pending_user_id"):
        return redirect(url_for("auth.login"))

    return render_template("auth/login.html", step=step)


def _login_post():
    if request.form.get("step") == "totp":
        return _login_totp()
    return _login_credentials()


def _login_credentials():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = User.query.filter_by(email=email, is_active=True).first()

    if not user or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        flash("Invalid email or password.", "danger")
        return render_template("auth/login.html", step="credentials")

    if user.totp_secret:
        session["login_pending_user_id"] = user.id
        return redirect(url_for("auth.login", step="totp"))

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return redirect(url_for("index"))


def _login_totp():
    pending_id = session.get("login_pending_user_id")
    if not pending_id:
        return redirect(url_for("auth.login"))

    user = db.session.get(User, pending_id)
    if not user:
        session.pop("login_pending_user_id", None)
        return redirect(url_for("auth.login"))

    totp_code = request.form.get("totp_code", "").strip()
    if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
        flash("Invalid authenticator code.", "danger")
        return render_template("auth/login.html", step="totp")

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return redirect(url_for("index"))


# ── /logout ───────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    slot_id = session.get("demo_slot_id")
    session.clear()
    if slot_id is not None:
        # Preserve the slot so the visitor can re-enter the same sandbox
        session["demo_slot_id"] = slot_id
    return redirect(url_for("index"))


# ── /setup ────────────────────────────────────────────────────────────────────

@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    if _is_demo():
        flash("Account creation is disabled in demo mode.", "warning")
        return redirect(url_for("index"))

    if not _no_users():
        return redirect(url_for("auth.login"))

    # Determine current step from form data (POST) or query string (GET)
    step = request.form.get("step") or request.args.get("step", "account")

    if request.method == "POST":
        if step == "account":
            return _setup_account()
        if step == "totp":
            return _setup_totp()

    # GET /setup?step=totp — only allowed after step 1 has been completed
    if step == "totp":
        if not session.get("setup_totp_secret"):
            return redirect(url_for("auth.setup"))
        return render_template(
            "auth/setup.html",
            step="totp",
            totp_secret=session["setup_totp_secret"],
            provisioning_uri=session["setup_provisioning_uri"],
        )

    return render_template("auth/setup.html", step="account")


def _setup_account():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    errors = []
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    if len(password) < 12:
        errors.append("Password must be at least 12 characters.")

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template("auth/setup.html", step="account")

    totp_secret = pyotp.random_base32()
    provisioning_uri = pyotp.TOTP(totp_secret).provisioning_uri(
        name=email, issuer_name="OpenHangar"
    )

    session["setup_email"] = email
    session["setup_password_hash"] = bcrypt.hashpw(
        password.encode(), bcrypt.gensalt()
    ).decode()
    session["setup_totp_secret"] = totp_secret
    session["setup_provisioning_uri"] = provisioning_uri

    return redirect(url_for("auth.setup", step="totp"))


def _setup_totp():
    email = session.get("setup_email")
    password_hash = session.get("setup_password_hash")
    totp_secret = session.get("setup_totp_secret")
    provisioning_uri = session.get("setup_provisioning_uri")

    if not all([email, password_hash, totp_secret]):
        flash("Session expired. Please start over.", "danger")
        return redirect(url_for("auth.setup"))

    # "Skip" path — create user without TOTP
    if request.form.get("action") == "skip":
        totp_secret_to_save = None
    else:
        totp_code = request.form.get("totp_code", "").strip()
        if not pyotp.TOTP(totp_secret).verify(totp_code, valid_window=1):
            flash("Invalid code. Please try again.", "danger")
            return render_template(
                "auth/setup.html",
                step="totp",
                totp_secret=totp_secret,
                provisioning_uri=provisioning_uri,
            )
        totp_secret_to_save = totp_secret

    tenant = Tenant(name="My Hangar")
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email=email,
        password_hash=password_hash,
        totp_secret=totp_secret_to_save,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()

    db.session.add(
        TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
    )
    db.session.commit()

    for key in ("setup_email", "setup_password_hash", "setup_totp_secret", "setup_provisioning_uri"):
        session.pop(key, None)

    flash("Setup complete. You can now log in.", "success")
    return redirect(url_for("auth.login"))
