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


# ── /login ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if _no_users():
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        return _login_post()

    return render_template("auth/login.html")


def _login_post():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    totp_code = request.form.get("totp_code", "").strip()

    user = User.query.filter_by(email=email, is_active=True).first()

    # Use a single generic error to avoid leaking which field was wrong
    _invalid = "Invalid email, password, or authenticator code."

    if not user:
        flash(_invalid, "danger")
        return render_template("auth/login.html")

    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        flash(_invalid, "danger")
        return render_template("auth/login.html")

    if not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1):
        flash(_invalid, "danger")
        return render_template("auth/login.html")

    session.clear()
    session["user_id"] = user.id
    session.permanent = True

    return redirect(url_for("index"))


# ── /logout ───────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── /setup ────────────────────────────────────────────────────────────────────

@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
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
    confirm = request.form.get("confirm_password", "")

    errors = []
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    if len(password) < 12:
        errors.append("Password must be at least 12 characters.")
    if password != confirm:
        errors.append("Passwords do not match.")

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
    totp_code = request.form.get("totp_code", "").strip()
    email = session.get("setup_email")
    password_hash = session.get("setup_password_hash")
    totp_secret = session.get("setup_totp_secret")
    provisioning_uri = session.get("setup_provisioning_uri")

    if not all([email, password_hash, totp_secret]):
        flash("Session expired. Please start over.", "danger")
        return redirect(url_for("auth.setup"))

    if not pyotp.TOTP(totp_secret).verify(totp_code, valid_window=1):
        flash("Invalid code. Please try again.", "danger")
        return render_template(
            "auth/setup.html",
            step="totp",
            totp_secret=totp_secret,
            provisioning_uri=provisioning_uri,
        )

    tenant = Tenant(name="My Hangar")
    db.session.add(tenant)
    db.session.flush()

    user = User(
        email=email,
        password_hash=password_hash,
        totp_secret=totp_secret,
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
