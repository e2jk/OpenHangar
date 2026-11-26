"""Edition invitation controls and explicit existing-account self-enrolment."""

from extensions import _rate_limiting_disabled, limiter
from flask import (
    Response,
    abort,
    flash,
    g,
    has_request_context,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Passport,
    PassportInvitation,
    PassportParticipant,
    PassportSeries,
    User,
    db,
)
from services import auth_challenges, auth_sessions
from services import capability_tokens as tokens
from utils import (
    absolute_url,  # pyright: ignore[reportMissingImports]
    current_membership,
    login_required,
)

from passports import groups, invitations, passports_bp, registration
from passports.management import BuilderError


@passports_bp.after_app_request
def invitation_privacy(response: Response) -> Response:
    if request.endpoint in {
        "passports.invitation_settings",
        "passports.join_passport",
        "passports.register_passport",
        "passports.verify_passport_email",
    } or (
        request.blueprint == "auth"
        and (session.get(invitations.JOIN_KEY) or g.get("passport_join_request"))
    ):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@passports_bp.route("/<int:passport_id>/invitation", methods=["GET", "POST"])
@login_required
def invitation_settings(passport_id: int) -> ResponseReturnValue:
    membership = current_membership()
    if membership is None:
        return abort(403)
    try:
        passport = invitations.owner_passport(
            membership.tenant_id, session["user_id"], passport_id
        )
    except BuilderError as exc:
        db.session.rollback()
        return abort(exc.status, description=str(exc))
    error, status = None, 200
    row = db.session.get(PassportInvitation, passport.id)
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "replace":
                row = invitations.replace(
                    passport, request.form.get("expires_on", "").strip()
                )
            elif action == "disable":
                if row is not None:
                    row.enabled = False
            else:
                raise BuilderError(_("Choose an invitation action."))
            db.session.commit()
            return redirect(
                url_for("passports.invitation_settings", passport_id=passport.id)
            )
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    token = (
        tokens.recover("invite", passport.id, row.key_nonce, row.token_hash)
        if row and invitations.available(row, passport)
        else None
    )
    return render_template(
        "passports/invitation_settings.html",
        passport=passport,
        invitation=row,
        can_open=invitations.enrolled(passport, session["user_id"]),
        invite_url=absolute_url("passports.join_passport", token=token)
        if token
        else None,
        error=error,
    ), status


def _reauthenticate() -> ResponseReturnValue:
    """Retain only opaque admission state when a target requires a stronger login."""
    context = session.get(invitations.JOIN_KEY)
    auth_sessions.revoke_current()
    session.clear()
    if context:
        session[invitations.JOIN_KEY] = context
    return redirect(url_for("auth.login"))


def _open(passport: Passport, user: User) -> ResponseReturnValue:
    if (
        auth_challenges.requires_totp(user, tenant_id=passport.tenant_id)
        and not user.totp_secret
    ):
        return _reauthenticate()
    auth_sessions.start(user, tenant_id=passport.tenant_id)
    return redirect(url_for("passports.detail", passport_id=passport.id, _swr_fresh=1))


@passports_bp.route("/join/<token>", methods=["GET", "POST"])
@limiter.shared_limit(
    "10 per minute",
    scope="passport-admission",
    methods=["POST"],
    exempt_when=_rate_limiting_disabled,
)
def join_passport(token: str) -> ResponseReturnValue:
    try:
        invitation, passport = invitations.lookup(token)
    except BuilderError as exc:
        db.session.rollback()
        return abort(exc.status, description=str(exc))
    user = (
        db.session.get(User, session.get("user_id")) if session.get("user_id") else None
    )
    if user and invitations.enrolled(passport, user.id):
        if (
            auth_challenges.requires_totp(user, tenant_id=passport.tenant_id)
            and not user.totp_secret
        ):
            participant = PassportParticipant.query.filter_by(
                passport_id=passport.id, user_id=user.id
            ).one()
            invitations.prepare(
                token,
                {
                    "display_name": participant.display_name,
                    "share_visits": "yes",
                    "share_history": "yes" if participant.share_history else "",
                },
            )
        return _open(passport, user)
    error, status = None, 200
    if request.method == "POST":
        try:
            values = dict(request.form)
            if user is None or (
                auth_challenges.requires_totp(user, tenant_id=passport.tenant_id)
                and not user.totp_secret
            ):
                invitations.prepare(token, values)
                return _reauthenticate() if user else redirect(url_for("auth.login"))
            passport = invitations.join(
                user.id, invitation.token_hash, passport.id, values
            )
            return _open(passport, user)  # The session and enrolment commit together.
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    default_name = ""
    series = (
        db.session.get(PassportSeries, passport.series_id)
        if passport.series_id
        else None
    )
    member = groups.membership(series, user.id) if user and series is not None else None
    if member is not None:
        default_name = member.display_name
    return render_template(
        "passports/join.html",
        passport=passport,
        signed_in=user is not None,
        default_group_name=default_name,
        error=error,
    ), status


@passports_bp.app_context_processor
def invitation_context() -> dict[str, str | None]:
    title = None
    if (
        has_request_context()
        and request.endpoint == "auth.login"
        and session.get(invitations.JOIN_KEY)
    ):
        context = invitations.pending()
        if context:
            title = context[0].payload.get("passport_title")
    return {"passport_join_title": title}


@passports_bp.route("/join/<token>/register", methods=["GET", "POST"])
@limiter.shared_limit(
    "10 per minute",
    scope="passport-admission",
    methods=["POST"],
    exempt_when=_rate_limiting_disabled,
)
def register_passport(token: str) -> ResponseReturnValue:
    try:
        _invitation, passport = invitations.lookup(token)
    except BuilderError as exc:
        return abort(exc.status, description=str(exc))
    if session.get("user_id"):
        return redirect(url_for("passports.join_passport", token=token))
    error, status = None, 200
    if request.method == "POST":
        try:
            registration.begin(token, dict(request.form))
            return redirect(url_for("passports.verify_passport_email", token=token))
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    return render_template(
        "passports/register.html", passport=passport, token=token, error=error
    ), status


@passports_bp.route("/join/<token>/verify", methods=["GET", "POST"])
@limiter.shared_limit(
    "10 per minute",
    scope="passport-admission",
    methods=["POST"],
    exempt_when=_rate_limiting_disabled,
)
def verify_passport_email(token: str) -> ResponseReturnValue:
    try:
        _invitation, passport = invitations.lookup(token)
    except BuilderError as exc:
        return abort(exc.status, description=str(exc))
    if session.get("user_id"):
        return redirect(url_for("passports.join_passport", token=token))
    error, status = None, 200
    try:
        registration.pending(token)
        if request.method == "POST":
            user, created, proof = registration.verify(
                token, request.form.get("code", "").strip()
            )
            if created:
                from auth.routes import _finish_login

                return _finish_login(user, expected_binding=proof)
            flash(
                _(
                    "Your email is verified. Sign in with your existing account to join."
                ),
                "info",
            )
            return redirect(url_for("auth.login"))
    except BuilderError as exc:
        db.session.rollback()
        error, status = str(exc), exc.status
    return render_template(
        "passports/verify_email.html", passport=passport, token=token, error=error
    ), status
