import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta

import pw_hash as _pw
import pyotp
from extensions import _rate_limiting_disabled  # pyright: ignore[reportMissingImports]
from extensions import limiter as _limiter
from flask import (
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
from flask_babel import ngettext  # pyright: ignore[reportMissingImports]
from markupsafe import Markup, escape
from models import (
    OperatingModel,
    PasswordResetToken,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)
from services import auth_challenges as challenges
from services import auth_sessions
from services import login_throttle as throttle
from services import totp as totp_codes
from utils import login_required

_log = logging.getLogger("openhangar.auth")


def _sl(value: object) -> str:
    """Sanitize a value for log output — strips CR/LF to prevent log injection (CWE-117)."""
    return str(value).replace("\r\n", "").replace("\n", "").replace("\r", "")


# Pre-computed Argon2id dummy hash used to equalise timing when no user record
# is found (prevents timing-based account enumeration — CWE-208).
_DUMMY_HASH: str = _pw.DUMMY_HASH

auth_bp = Blueprint("auth", __name__)

_COMPLEX_MODELS = frozenset({"shared_ownership", "flight_club", "flight_school"})

# ── Login brute-force protection ──────────────────────────────────────────────
# Two independent layers, stored in the database (services/login_throttle.py)
# so every worker shares them and they survive a restart:
#   1. Address backoff — after repeated failures from one client address, its
#                        next attempts are refused (HTTP 429) for a growing
#                        while; nothing sleeps in the serving thread
#   2. Account lock    — 30 minutes after 10 consecutive failures on the same
#                        e-mail address (auto-unlocks, no admin needed)

_IP_FAIL_TTL = 900  # reset the address counter after 15 min without failures
_ACCT_FAIL_TTL = 1800  # reset the account counter after 30 min
_ACCT_LOCK_MINUTES = 30
_ACCT_LOCK_TTL = _ACCT_LOCK_MINUTES * 60
_ACCT_LOCK_THRESHOLD = 10

# How long (seconds) an address must wait after its Nth consecutive failure.
_IP_BACKOFF: dict[int, int] = {3: 2, 4: 10, 5: 30}
_IP_BACKOFF_MAX = 60  # applied for 6 or more failures


def _ip_backoff_delay(count: int) -> int:
    if count < 3:
        return 0
    return _IP_BACKOFF.get(count, _IP_BACKOFF_MAX)


def _ip_key(ip: str) -> str:
    return f"ip:{ip}"


def _account_key(email: str) -> str:
    # Hashed: bounded length, and no stored list of attempted addresses.
    return "acct:" + hashlib.sha256(email.encode()).hexdigest()


def _no_users() -> bool:
    return db.session.query(User).count() == 0


def _is_demo() -> bool:
    return os.environ.get("OPENHANGAR_ENV") == "demo"


# ── /login ────────────────────────────────────────────────────────────────────


@auth_bp.route("/login", methods=["GET", "POST"])
@_limiter.limit(
    lambda: current_app.config.get("LOGIN_RATE_LIMIT", "20 per minute"),
    methods=["POST"],
    exempt_when=_rate_limiting_disabled,
)
def login() -> ResponseReturnValue:
    if _no_users():
        return redirect(url_for("auth.setup"))

    if session.get("user_id"):
        return redirect(url_for("index"))

    if request.method == "POST":
        return _login_post()

    step = request.args.get("step", "credentials")
    challenge = challenges.get("login_challenge", step)
    if step in ("totp", "totp-enrol") and challenge is None:
        return redirect(url_for("auth.login"))
    payload = challenge.payload if challenge else {}
    return render_template(
        "auth/login.html",
        step=step,
        totp_secret=payload.get("secret"),
        totp_uri=payload.get("uri"),
    )


def _login_post() -> ResponseReturnValue:
    step = request.form.get("step")
    if step == "totp":
        return _login_totp()
    if step == "totp-enrol":
        return _login_totp_enrol()
    return _login_credentials()


def _login_credentials() -> ResponseReturnValue:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    ip = _sl(request.remote_addr or "")

    # ── Address backoff and account lock (before any verification) ───────────
    wait = throttle.blocked_for(_ip_key(ip))
    if wait:
        _log.warning("[SECURITY] auth.login.backoff_refused ip=%s wait=%ss", ip, wait)
        flash(
            ngettext(
                "Too many failed sign-in attempts. Try again in one second.",
                "Too many failed sign-in attempts. Try again in %(n)s seconds.",
                wait,
                n=wait,
            ),
            "danger",
        )
        return render_template("auth/login.html", step="credentials"), 429
    if throttle.blocked_for(_account_key(email)):
        _log.warning(
            "[SECURITY] auth.login.account_blocked email=%s ip=%s",
            _sl(email),
            ip,
        )
        flash(
            _(
                "Account temporarily locked due to too many failed attempts."
                " Try again in %(minutes)s minutes.",
                minutes=_ACCT_LOCK_MINUTES,
            ),
            "danger",
        )
        return render_template("auth/login.html", step="credentials"), 429

    user = User.query.filter_by(email=email, is_active=True).first()
    password_hash = user.password_hash if user else _DUMMY_HASH
    # Always verify — never short-circuit on user is None.
    # Without this, a missing-user response is faster, enabling timing-based
    # account enumeration (CWE-208). pw_hash.verify handles both Argon2id and
    # legacy bcrypt hashes transparently.
    password_ok = _pw.verify(password, password_hash)

    if not user or not password_ok:
        _log.warning(
            "[SECURITY] auth.credentials.failed email=%s ip=%s",
            _sl(email),
            ip,
        )

        # ── Address backoff ───────────────────────────────────────────────────
        ip_count = throttle.record_failure(_ip_key(ip), _IP_FAIL_TTL)
        delay = _ip_backoff_delay(ip_count)
        if delay:
            throttle.block(_ip_key(ip), delay)
            _log.warning(
                "[SECURITY] auth.login.backoff ip=%s failures=%s delay=%ss",
                ip,
                ip_count,
                delay,
            )

        # ── Account failure tracking ──────────────────────────────────────────
        acct_count = throttle.record_failure(_account_key(email), _ACCT_FAIL_TTL)
        if acct_count >= _ACCT_LOCK_THRESHOLD:
            until = throttle.block(_account_key(email), _ACCT_LOCK_TTL, reset=True)
            _log.warning(
                "[SECURITY] auth.login.account_locked email=%s ip=%s locked_until=%s",
                _sl(email),
                ip,
                _sl(datetime.fromtimestamp(until, UTC).isoformat()),
            )

        flash(_("Invalid email or password."), "danger")
        return render_template("auth/login.html", step="credentials")

    # Credentials verified — clear failure counters for this IP and account.
    throttle.clear(_ip_key(ip), _account_key(email))

    # Upgrade legacy bcrypt hashes to Argon2id transparently at login time.
    # Same password, so the user's other logins stay valid.
    if _pw.needs_rehash(user.password_hash):
        auth_sessions.rehash_password(user, _pw.hash(password))

    # Block users whose only tenant(s) have been deactivated, unless they are
    # the instance admin (who must always be able to log in).
    if not user.is_instance_admin:
        active_tenant_ids = {
            tu.tenant_id for tu in user.tenants if tu.tenant and tu.tenant.is_active
        }
        if not active_tenant_ids:
            _log.warning(
                "[SECURITY] auth.credentials.deactivated email=%s ip=%s",
                _sl(email),
                _sl(request.remote_addr),
            )
            flash(
                _("Your account has been deactivated. Contact the administrator."),
                "danger",
            )
            return render_template("auth/login.html", step="credentials")

    if user.totp_secret:
        challenges.issue("login_challenge", "totp", user=user)
        return redirect(url_for("auth.login", step="totp"))

    # If the tenant mandates TOTP and the user has none, redirect to enrolment.
    tu = TenantUser.query.filter_by(user_id=user.id).first()
    if tu and tu.tenant and tu.tenant.require_totp:
        totp_secret = pyotp.random_base32()
        totp_uri = pyotp.TOTP(totp_secret).provisioning_uri(
            name=user.email, issuer_name="OpenHangar"
        )
        challenges.issue(
            "login_challenge",
            "totp-enrol",
            user=user,
            payload={"secret": totp_secret, "uri": totp_uri},
        )
        return redirect(url_for("auth.login", step="totp-enrol"))

    session.clear()
    auth_sessions.start(user)
    # _swr_fresh: tells the SW (sw.js) this / request must bypass whatever
    # it has cached (e.g. the landing page from before this login) and
    # re-cache the dashboard under the canonical / key. See pwa.js for the
    # matching history.replaceState() that scrubs the marker from the URL.
    return redirect(url_for("index", _swr_fresh=1))


def _login_totp() -> ResponseReturnValue:
    challenge = challenges.get("login_challenge", "totp")
    if challenge is None:
        return redirect(url_for("auth.login"))
    pending_id = challenge.user_id
    user = User.query.filter_by(id=pending_id).one()
    totp_code = request.form.get("totp_code", "").strip()

    outcome = totp_codes.use_code(user, totp_code)
    if outcome != totp_codes.ACCEPTED:
        _log.warning(
            "[SECURITY] auth.totp.%s user_id=%s ip=%s",
            "replay" if outcome == totp_codes.REUSED else "failed",
            _sl(pending_id),
            _sl(request.remote_addr),
        )
        flash(_("Invalid authenticator code."), "danger")
        return render_template("auth/login.html", step="totp")

    if not challenges.consume(challenge):
        return redirect(url_for("auth.login"))
    db.session.commit()

    session.clear()
    auth_sessions.start(user)
    # _swr_fresh: tells the SW (sw.js) this / request must bypass whatever
    # it has cached (e.g. the landing page from before this login) and
    # re-cache the dashboard under the canonical / key. See pwa.js for the
    # matching history.replaceState() that scrubs the marker from the URL.
    return redirect(url_for("index", _swr_fresh=1))


def _login_totp_enrol() -> ResponseReturnValue:
    """Mandatory TOTP enrolment during login when tenant.require_totp is True."""
    challenge = challenges.get("login_challenge", "totp-enrol")
    if challenge is None:
        return redirect(url_for("auth.login"))
    user = User.query.filter_by(id=challenge.user_id).one()
    if user.totp_secret or not challenges.requires_totp(user):
        return redirect(url_for("auth.login"))
    totp_secret = challenge.payload["secret"]
    totp_uri = challenge.payload["uri"]
    totp_code = request.form.get("totp_code", "").strip()
    step = totp_codes.matching_step(totp_secret, totp_code)
    if step is None:
        flash(_("Invalid code — please try again."), "danger")
        return render_template(
            "auth/login.html",
            step="totp-enrol",
            totp_secret=totp_secret,
            totp_uri=totp_uri,
        )

    if not challenges.consume(challenge):
        return redirect(url_for("auth.login"))
    user.totp_secret = totp_secret
    user.totp_last_step = step  # the enrolment code cannot sign in again
    db.session.commit()
    _log.warning(
        "[SECURITY] auth.totp.enrolment_forced user_id=%s ip=%s",
        _sl(str(user.id)),
        _sl(request.remote_addr),
    )

    session.clear()
    auth_sessions.start(user)
    flash(_("Two-factor authentication is now active on your account."), "success")
    # See the _swr_fresh comment on the other two login-success redirects above.
    return redirect(url_for("index", _swr_fresh=1))


# ── /logout ───────────────────────────────────────────────────────────────────


@auth_bp.route("/logout")
def logout() -> ResponseReturnValue:
    slot_id = session.get("demo_slot_id")
    auth_sessions.revoke_current()
    session.clear()
    if slot_id is not None:
        # Preserve the slot so the visitor can re-enter the same sandbox
        session["demo_slot_id"] = slot_id
    return redirect(url_for("index"))


# ── /setup ────────────────────────────────────────────────────────────────────

_WIZARD_STEPS = [
    "account",
    "totp",
    "operating_model",
    "aircraft_count",
    "org_name",
    "co_owners",
    "summary",
]

_OPERATING_MODELS = {
    OperatingModel.SOLE_PILOT,
    OperatingModel.SOLE_OPERATOR,
    OperatingModel.SHARED_OWNERSHIP,
    OperatingModel.FLIGHT_CLUB,
    OperatingModel.FLIGHT_SCHOOL,
}


def _wizard_phase(step: str) -> int:
    """Map a wizard step to a 1-based display phase (for the progress indicator)."""
    if step in ("account", "totp"):
        return 1
    if step == "operating_model":
        return 2
    if step in ("aircraft_count", "org_name", "co_owners"):
        return 3
    return 4  # summary


def _next_step(current: str) -> str:
    """Compute the next wizard step based on current step and session choices."""
    operating_model = session.get("setup_operating_model", "")

    if current == "account":  # pragma: no cover
        return "totp"
    if current == "totp":  # pragma: no cover
        return "operating_model"
    if current == "operating_model":  # pragma: no cover
        return "summary" if operating_model == "sole_pilot" else "aircraft_count"
    if current == "aircraft_count":
        if operating_model in ("flight_club", "flight_school"):
            return "org_name"
        if operating_model == "shared_ownership":
            return "co_owners"
        return "summary"
    if current in ("org_name", "co_owners"):  # pragma: no cover
        return "summary"
    return "summary"  # pragma: no cover


@auth_bp.route("/setup", methods=["GET", "POST"])
@_limiter.limit("10 per minute", methods=["POST"], exempt_when=_rate_limiting_disabled)
def setup() -> ResponseReturnValue:
    if _is_demo():
        flash(_("Account creation is disabled in demo mode."), "warning")
        return redirect(url_for("index"))

    if not _no_users():
        return redirect(url_for("config.index"))

    # Determine current step from form data (POST) or query string (GET)
    step = request.form.get("step") or request.args.get("step", "account")
    if step not in _WIZARD_STEPS:
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        if step == "account":
            return _setup_account()
        if step == "totp":
            return _setup_totp()
        if step == "operating_model":
            return _setup_operating_model()
        if step == "aircraft_count":
            return _setup_aircraft_count()
        if step == "org_name":
            return _setup_org_name()
        if step == "co_owners":
            return _setup_co_owners()
        if step == "summary":
            return _setup_finish()

    # GET handlers — validate session state before rendering each step
    phase = _wizard_phase(step)

    if step == "totp":
        challenge = challenges.get("setup_challenge", "setup")
        if challenge is None:
            return redirect(url_for("auth.setup"))
        return render_template(
            "auth/setup.html",
            step="totp",
            phase=phase,
            show_review=False,
            totp_secret=challenge.payload["secret"],
            provisioning_uri=challenge.payload["uri"],
        )

    if step == "operating_model":
        if not session.get("setup_totp_done"):
            return redirect(url_for("auth.setup"))
        return render_template(
            "auth/setup.html", step="operating_model", phase=phase, show_review=False
        )

    if step == "aircraft_count":
        if not session.get("setup_operating_model"):
            return redirect(url_for("auth.setup", step="operating_model"))
        return render_template(
            "auth/setup.html",
            step="aircraft_count",
            phase=phase,
            show_review=session.get("setup_operating_model") in _COMPLEX_MODELS,
            operating_model=session.get("setup_operating_model"),
        )

    if step == "org_name":
        model = session.get("setup_operating_model", "")
        if model not in ("flight_club", "flight_school"):
            return redirect(url_for("auth.setup", step="summary"))
        return render_template(
            "auth/setup.html",
            step="org_name",
            phase=phase,
            show_review=True,
            operating_model=model,
        )

    if step == "co_owners":
        if session.get("setup_operating_model") != "shared_ownership":
            return redirect(url_for("auth.setup", step="summary"))
        return render_template(
            "auth/setup.html", step="co_owners", phase=phase, show_review=True
        )

    if step == "summary":
        if not session.get("setup_operating_model"):
            return redirect(url_for("auth.setup", step="operating_model"))
        if session.get("setup_operating_model") in ("sole_pilot", "sole_operator"):
            return redirect(url_for("auth.setup", step="operating_model"))
        return render_template(
            "auth/setup.html",
            step="summary",
            phase=phase,
            show_review=True,
            operating_model=session.get("setup_operating_model"),
            aircraft_count=session.get("setup_aircraft_count"),
            allows_rental=session.get("setup_allows_rental", False),
            org_name=session.get("setup_org_name", ""),
            co_owners=session.get("setup_co_owners", []),
            setup_name=session.get("setup_name", ""),
            setup_email=session.get("setup_email", ""),
        )

    return render_template(
        "auth/setup.html", step="account", phase=1, show_review=False
    )


def _setup_account() -> ResponseReturnValue:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip()

    errors = []
    if not email or "@" not in email:
        errors.append(_("A valid email address is required."))
    if len(password) < 12:
        errors.append(_("Password must be at least 12 characters."))

    if errors:
        for msg in errors:
            flash(msg, "danger")
        return render_template(
            "auth/setup.html", step="account", phase=1, show_review=False
        )

    totp_secret = pyotp.random_base32()
    provisioning_uri = pyotp.TOTP(totp_secret).provisioning_uri(
        name=email, issuer_name="OpenHangar"
    )

    session["setup_email"] = email
    session["setup_name"] = name or None
    challenges.issue(
        "setup_challenge",
        "setup",
        lifetime=challenges.SETUP_SECONDS,
        payload={
            "password_hash": _pw.hash(password),
            "secret": totp_secret,
            "uri": provisioning_uri,
        },
    )

    return redirect(url_for("auth.setup", step="totp"))


def _setup_totp() -> ResponseReturnValue:
    challenge = challenges.get("setup_challenge", "setup")
    if challenge is None:
        flash(_("Session expired. Please start over."), "danger")
        return redirect(url_for("auth.setup"))
    totp_secret = challenge.payload["secret"]
    provisioning_uri = challenge.payload["uri"]
    # "Skip" path — user will not have TOTP
    if request.form.get("action") == "skip":
        challenge.payload = {**challenge.payload, "totp_to_save": None}
    else:
        totp_code = request.form.get("totp_code", "").strip()
        step = totp_codes.matching_step(str(totp_secret), totp_code)
        if step is None:
            flash(_("Invalid code. Please try again."), "danger")
            return render_template(
                "auth/setup.html",
                step="totp",
                phase=1,
                show_review=False,
                totp_secret=totp_secret,
                provisioning_uri=provisioning_uri,
            )
        challenge.payload = {
            **challenge.payload,
            "totp_to_save": totp_secret,
            "totp_step": step,
        }

    db.session.commit()
    session["setup_totp_done"] = True
    return redirect(url_for("auth.setup", step="operating_model"))


def _setup_operating_model() -> ResponseReturnValue:
    if not session.get("setup_totp_done"):
        return redirect(url_for("auth.setup"))

    model = request.form.get("operating_model", "")
    valid = {m.value for m in _OPERATING_MODELS}
    if model not in valid:
        flash(_("Please select an option."), "danger")
        return render_template(
            "auth/setup.html", step="operating_model", phase=2, show_review=False
        )

    session["setup_operating_model"] = model
    if model == "sole_pilot":
        return _setup_finish()
    return redirect(url_for("auth.setup", step="aircraft_count"))


def _setup_aircraft_count() -> ResponseReturnValue:
    if not session.get("setup_operating_model"):
        return redirect(url_for("auth.setup", step="operating_model"))

    count_str = request.form.get("aircraft_count", "").strip()
    try:
        count = int(count_str)
        if count < 0:
            raise ValueError
    except (ValueError, TypeError):
        flash(_("Please enter a valid number of aircraft (0 or more)."), "danger")
        return render_template(
            "auth/setup.html",
            step="aircraft_count",
            phase=3,
            show_review=session.get("setup_operating_model") in _COMPLEX_MODELS,
            operating_model=session.get("setup_operating_model"),
        )

    allows_rental = "allows_rental" in request.form
    session["setup_aircraft_count"] = count
    session["setup_allows_rental"] = allows_rental

    next_step = _next_step("aircraft_count")
    if next_step == "summary":
        return _setup_finish()
    return redirect(url_for("auth.setup", step=next_step))


def _setup_org_name() -> ResponseReturnValue:
    model = session.get("setup_operating_model", "")
    if model not in ("flight_club", "flight_school"):
        return redirect(url_for("auth.setup", step="summary"))

    org_name = request.form.get("org_name", "").strip()
    if not org_name:
        flash(_("Please enter a name."), "danger")
        return render_template(
            "auth/setup.html",
            step="org_name",
            phase=3,
            show_review=True,
            operating_model=model,
        )

    session["setup_org_name"] = org_name
    return redirect(url_for("auth.setup", step="summary"))


def _setup_co_owners() -> ResponseReturnValue:
    if session.get("setup_operating_model") != "shared_ownership":
        return redirect(url_for("auth.setup", step="summary"))

    names = request.form.getlist("co_owner_name")
    emails = request.form.getlist("co_owner_email")
    roles = request.form.getlist("co_owner_role")

    co_owners = []
    for name, email, role in zip(names, emails, roles):
        name = name.strip()
        email = email.strip().lower()
        role = role if role in ("owner", "admin") else "owner"
        if name or email:
            co_owners.append(
                {"name": name or None, "email": email or None, "role": role}
            )

    session["setup_co_owners"] = co_owners
    return redirect(url_for("auth.setup", step="summary"))


def _setup_finish() -> ResponseReturnValue:
    required = ["setup_email", "setup_operating_model"]
    challenge = challenges.get("setup_challenge", "setup")
    if (
        not all(session.get(k) for k in required)
        or challenge is None
        or "totp_to_save" not in challenge.payload
    ):
        flash(_("Session expired. Please start over."), "danger")
        return redirect(url_for("auth.setup"))

    if not challenges.consume(challenge):
        return redirect(url_for("auth.setup"))
    from models import UserInvitation

    operating_model_raw = session.get("setup_operating_model", "")
    aircraft_count = session.get("setup_aircraft_count")
    allows_rental = bool(session.get("setup_allows_rental", False))
    org_name = session.get("setup_org_name", "")
    co_owners = session.get("setup_co_owners", [])

    # Choose tenant name based on operating model
    tenant_name = "My Hangar"
    if operating_model_raw in ("flight_club", "flight_school") and org_name:
        tenant_name = org_name

    tenant = Tenant(name=tenant_name)
    db.session.add(tenant)
    db.session.flush()

    # Auto-generate the Hangar ID from the tenant name so canonical document
    # paths work immediately, without requiring a trip to Settings first.
    from documents.routes import (
        _ensure_tenant_slug,  # pyright: ignore[reportMissingImports]
    )

    _ensure_tenant_slug(tenant)

    user = User(
        email=session["setup_email"],
        password_hash=challenge.payload["password_hash"],
        totp_secret=challenge.payload["totp_to_save"],
        totp_last_step=challenge.payload.get("totp_step"),
        name=session.get("setup_name"),
        is_active=True,
        is_instance_admin=True,
    )
    db.session.add(user)
    db.session.flush()

    db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))

    # Determine profile values from wizard
    try:
        op_model: OperatingModel | None = OperatingModel(operating_model_raw)
    except ValueError:
        op_model = None
    planned_count: int | None = (
        0 if operating_model_raw == "sole_pilot" else aircraft_count
    )

    club_name = org_name if operating_model_raw == "flight_club" else None
    school_name = org_name if operating_model_raw == "flight_school" else None

    profile = TenantProfile(
        tenant_id=tenant.id,
        operating_model=op_model,
        planned_aircraft_count=planned_count,
        allows_rental=allows_rental,
        club_name=club_name,
        school_name=school_name,
        setup_complete=True,
    )
    db.session.add(profile)

    # Create co-owner invitations (shared_ownership path)
    for co in co_owners:
        inv_role = Role.ADMIN if co.get("role") == "admin" else Role.OWNER
        inv = UserInvitation(
            tenant_id=tenant.id,
            invited_by_user_id=user.id,
            email=co.get("email") or None,
            display_name=co.get("name") or None,
            role=inv_role,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db.session.add(inv)

    db.session.commit()

    aircraft_url = url_for("aircraft.new_aircraft")
    flight_url = url_for("flights.log_flight")
    is_pilot_ctx = operating_model_raw in (
        "sole_pilot",
        "sole_operator",
        "shared_ownership",
    )
    is_operator_ctx = operating_model_raw != "sole_pilot"
    welcome = escape(_("Setup complete. Welcome to OpenHangar!"))
    aircraft_label = _("Add your first aircraft")
    flight_label = _("Register your first flight")
    # Below: every interpolated value is either escape()'d or the output of
    # url_for() on a static (argument-less) endpoint, never user input.
    aircraft_link = Markup(  # nosec B704
        f'<a href="{aircraft_url}" class="alert-link">{escape(aircraft_label)}</a>'
    )
    flight_link = Markup(  # nosec B704
        f'<a href="{flight_url}" class="alert-link">{escape(flight_label)}</a>'
    )
    if is_pilot_ctx and is_operator_ctx:
        msg = Markup(f"{welcome} {aircraft_link} · {flight_link}")  # nosec B704
    elif is_operator_ctx:
        msg = Markup(f"{welcome} {aircraft_link}")  # nosec B704
    else:
        msg = Markup(f"{welcome} {flight_link}")  # nosec B704

    _clear_setup_session()
    auth_sessions.start(user)
    flash(msg, "success")
    # See the _swr_fresh comment on the login-success redirects above.
    return redirect(url_for("index", _swr_fresh=1))


def _clear_setup_session() -> None:
    for key in (
        "setup_email",
        "setup_name",
        "setup_challenge",
        "setup_totp_secret",
        "setup_provisioning_uri",
        "setup_totp_to_save",
        "setup_totp_done",
        "setup_operating_model",
        "setup_aircraft_count",
        "setup_allows_rental",
        "setup_org_name",
        "setup_co_owners",
    ):
        session.pop(key, None)


# ── /profile ──────────────────────────────────────────────────────────────────


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile() -> ResponseReturnValue:
    user = db.session.get(User, session["user_id"])
    if not user:
        return redirect(url_for("auth.logout"))

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_name":
            return _profile_update_name(user)
        if action == "change_password":
            return _profile_change_password(user)
        if action == "setup_totp":
            return _profile_setup_totp(user)
        if action == "confirm_totp":
            return _profile_confirm_totp(user)
        if action == "disable_totp":
            return _profile_disable_totp(user)

    return render_template(
        "auth/profile.html", user=user, totp_secret=None, totp_uri=None
    )


def _profile_update_name(user: User) -> ResponseReturnValue:
    name = request.form.get("name", "").strip()
    user.name = name or None
    db.session.commit()
    flash(_("Display name updated."), "success")
    return redirect(url_for("auth.profile"))


def _profile_change_password(user: User) -> ResponseReturnValue:
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    if not _pw.verify(current_pw, user.password_hash):
        flash(_("Current password is incorrect."), "danger")
        return render_template("auth/profile.html", user=user, totp_secret=None)
    if len(new_pw) < 12:
        flash(_("Password must be at least 12 characters."), "danger")
        return render_template("auth/profile.html", user=user, totp_secret=None)
    if new_pw != confirm_pw:
        flash(_("Passwords do not match."), "danger")
        return render_template("auth/profile.html", user=user, totp_secret=None)

    user.password_hash = _pw.hash(new_pw)
    db.session.commit()
    auth_sessions.start(user)
    _log.warning(
        "[SECURITY] auth.password.changed user_id=%s ip=%s",
        _sl(str(user.id)),
        _sl(request.remote_addr),
    )
    flash(_("Password updated successfully."), "success")
    return redirect(url_for("auth.profile"))


def _use_current_totp(user: User, code: str) -> bool:
    """Verify a code from the user's *current* authenticator, at most once."""
    return totp_codes.use_code(user, code) == totp_codes.ACCEPTED


def _profile_step_up(user: User, purpose: str) -> bool:
    """Re-authenticate before an authenticator is added, replaced or removed.

    A session cookie (and CSRF token) alone must not change the second factor:
    the current password is always required, and once an authenticator exists,
    also a code from it. Losing that authenticator goes through the
    administrator's password reset link, which clears it.
    """
    if not _pw.verify(request.form.get("current_password", ""), user.password_hash):
        flash(_("Current password is incorrect."), "danger")
    elif user.totp_secret and not _use_current_totp(
        user, request.form.get("current_totp_code", "").strip()
    ):
        flash(_("Invalid authenticator code."), "danger")
    else:
        return True
    _log.warning(
        "[SECURITY] auth.totp.step_up_failed user_id=%s purpose=%s ip=%s",
        _sl(str(user.id)),
        _sl(purpose),
        _sl(request.remote_addr),
    )
    return False


def _profile_setup_totp(user: User) -> ResponseReturnValue:
    if not _profile_step_up(user, "setup"):
        return redirect(url_for("auth.profile"))
    totp_secret = pyotp.random_base32()
    totp_uri = pyotp.TOTP(totp_secret).provisioning_uri(
        name=user.email, issuer_name="OpenHangar"
    )
    challenges.issue(
        "profile_challenge",
        "profile-totp",
        user=user,
        payload={"secret": totp_secret, "uri": totp_uri},
    )
    return render_template(
        "auth/profile.html", user=user, totp_secret=totp_secret, totp_uri=totp_uri
    )


def _profile_confirm_totp(user: User) -> ResponseReturnValue:
    challenge = challenges.get("profile_challenge", "profile-totp", user_id=user.id)
    if challenge is None:
        flash(_("Session expired. Please try again."), "danger")
        return redirect(url_for("auth.profile"))
    totp_secret = challenge.payload["secret"]
    totp_uri = challenge.payload["uri"]
    code = request.form.get("totp_code", "").strip()
    step = totp_codes.matching_step(totp_secret, code)
    if step is None:
        flash(_("Invalid code. Please try again."), "danger")
        return render_template(
            "auth/profile.html", user=user, totp_secret=totp_secret, totp_uri=totp_uri
        )

    if not challenges.consume(challenge):
        return redirect(url_for("auth.profile"))
    replaced = bool(user.totp_secret)
    user.totp_secret = totp_secret
    user.totp_last_step = step  # the confirmation code cannot sign in again
    db.session.commit()
    auth_sessions.start(user)
    session.pop("profile_challenge", None)
    _log.warning(
        "[SECURITY] auth.totp.%s user_id=%s ip=%s",
        "replaced" if replaced else "enabled",
        _sl(str(user.id)),
        _sl(request.remote_addr),
    )
    flash(_("Two-factor authentication enabled."), "success")
    return redirect(url_for("auth.profile"))


def _profile_disable_totp(user: User) -> ResponseReturnValue:
    tu = TenantUser.query.filter_by(user_id=user.id).first()
    if tu and tu.tenant and tu.tenant.require_totp:
        flash(
            _(
                "Your administrator requires two-factor authentication."
                " It cannot be disabled on this account."
            ),
            "danger",
        )
        return redirect(url_for("auth.profile"))
    if not _profile_step_up(user, "disable"):
        return redirect(url_for("auth.profile"))
    user.totp_secret = None
    user.totp_last_step = None
    db.session.commit()
    auth_sessions.start(user)
    _log.warning(
        "[SECURITY] auth.totp.disabled user_id=%s ip=%s",
        _sl(str(user.id)),
        _sl(request.remote_addr),
    )
    flash(_("Two-factor authentication disabled."), "success")
    return redirect(url_for("auth.profile"))


# ── /reset-password/<token> ───────────────────────────────────────────────────


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str) -> ResponseReturnValue:
    """Consume a PasswordResetToken generated by the instance admin."""
    prt = PasswordResetToken.query.filter_by(token=token).first_or_404()

    if prt.is_used:
        flash(_("This reset link has already been used."), "danger")
        return redirect(url_for("auth.login"))

    if prt.is_expired:
        flash(
            _("This reset link has expired. Ask the administrator for a new one."),
            "danger",
        )
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if len(new_pw) < 12:
            flash(_("Password must be at least 12 characters."), "danger")
            return render_template("auth/reset_password.html", token=token)
        if new_pw != confirm_pw:
            flash(_("Passwords do not match."), "danger")
            return render_template("auth/reset_password.html", token=token)

        user = db.session.get(User, prt.user_id)
        if not user:  # pragma: no cover — token CASCADE-deletes with user
            flash(_("User not found."), "danger")
            return redirect(url_for("auth.login"))

        user.password_hash = _pw.hash(new_pw)
        user.totp_secret = None  # clear TOTP so the user can re-enrol
        user.totp_last_step = None
        prt.used_at = datetime.now(UTC)
        db.session.commit()

        flash(_("Password reset successfully. You can now log in."), "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
