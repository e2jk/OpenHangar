"""Explicit publication choices and standalone public passport representations."""

from datetime import UTC, date, datetime, time

from extensions import _rate_limiting_disabled, limiter
from flask import (
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import Passport, PassportStampPhoto, PublicShare, db, fits_column
from services import public_shares
from utils import db_id, login_required

from passports import groups, passports_bp, public_access, public_projection
from passports.access import require_passport
from passports.evidence import photo_path
from passports.management import BuilderError, require_actor


@passports_bp.after_app_request
def public_privacy(response: Response) -> Response:
    if request.path.startswith("/passports/public/") and response.status_code >= 400:
        # Global error templates can contain the logged-in viewer's navigation.
        response = Response(
            _("This public link is not available."),
            status=response.status_code,
            mimetype="text/plain",
        )
    if (
        request.path.startswith("/passports/public/")
        or request.endpoint == "passports.public_settings"
    ):
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers.pop("Last-Modified", None)
        response.headers.pop("ETag", None)
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def _unavailable() -> Response:
    return Response(
        _("This public link is not available."), status=404, mimetype="text/plain"
    )


def _expiry(value: str) -> datetime | None:
    try:
        day = date.fromisoformat(value) if value else None
    except ValueError as exc:
        raise BuilderError(_("Enter a valid public-link expiry date.")) from exc
    if day is not None and day < date.today():
        raise BuilderError(_("Choose today or a future public-link expiry date."))
    return datetime.combine(day, time.max, UTC) if day else None


def _save_choices(passport: Passport, *, withdraw: bool = False) -> None:
    actor = session["user_id"]
    participant = next(
        (p for p in public_access.participants(passport) if p.user_id == actor), None
    )
    if participant is None:
        raise BuilderError(_("Passport not found."), 404)
    publish = not withdraw and request.form.get("publish_visits") == "1"
    frequency = not withdraw and request.form.get("publish_frequency") == "1"
    name = "" if withdraw else request.form.get("public_name", "").strip()
    raw_photos = [] if withdraw else request.form.getlist("photos")
    if len(raw_photos) > 5000:
        raise BuilderError(_("Choose fewer stamp photos."))
    try:
        chosen = {db_id(value) for value in raw_photos}
    except ValueError as exc:
        raise BuilderError(_("Choose valid stamp photos.")) from exc
    if publish or frequency or name or chosen:
        require_actor(passport.tenant_id, actor)
    if frequency and not publish or chosen and not publish or name and not publish:
        raise BuilderError(
            _("Publish your visits before choosing public counts, a name or photos.")
        )
    if publish and passport.is_group and not participant.share_visits:
        raise BuilderError(
            _("Share your visits with this group before publishing them.")
        )
    if not fits_column(type(participant).public_name, name):
        raise BuilderError(_("The public name is too long."))
    photos = PassportStampPhoto.query.filter_by(
        passport_id=passport.id, participant_id=participant.id, uploader_user_id=actor
    ).all()
    if chosen - {photo.id for photo in photos}:
        raise BuilderError(_("Stamp photo not found."), 404)
    now = datetime.now(UTC)
    participant.public_shared_at = now if publish else None
    participant.public_frequency_at = now if frequency else None
    participant.public_name = name or None
    for photo in photos:
        photo.public_consent = photo.id in chosen
        photo.public_shared_at = now if photo.public_consent else None


@passports_bp.route("/<int:passport_id>/public-sharing", methods=["GET", "POST"])
@login_required
def public_settings(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    if request.method == "POST":
        try:
            if passport.series_id is not None:
                groups.lock_series(passport.series_id)
            passport = groups.lock_passport(passport)
        except BuilderError as exc:
            db.session.rollback()
            return abort(exc.status, description=str(exc))
    owner = passport.owner_user_id == session["user_id"]
    share = PublicShare.query.filter_by(
        tenant_id=passport.tenant_id, resource_type="passport", resource_id=passport.id
    ).first()
    error, status = None, 200
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action in {"choices", "withdraw"}:
                _save_choices(passport, withdraw=action == "withdraw")
            elif owner and action in {"rotate", "revoke"}:
                if action == "rotate":
                    require_actor(passport.tenant_id, session["user_id"])
                    expiry = _expiry(request.form.get("expires_on", "").strip())
                    if share is None:
                        share = PublicShare(
                            tenant_id=passport.tenant_id,
                            issuer_user_id=session["user_id"],
                            resource_type="passport",
                            resource_id=passport.id,
                            resource_created_at=passport.created_at,
                        )
                        db.session.add(share)
                    share.issuer_user_id = session["user_id"]
                    share.resource_created_at = passport.created_at
                    public_shares.rotate(share, expiry)
                elif share is not None:
                    public_shares.revoke(share)
            else:
                raise BuilderError(_("Choose a public-sharing action."), 403)
            db.session.commit()
            return redirect(
                url_for("passports.public_settings", passport_id=passport.id)
            )
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
    participant = next(
        (
            p
            for p in public_access.participants(passport)
            if p.user_id == session["user_id"]
        ),
        None,
    )
    token = (
        public_shares.recover(share)
        if share is not None
        and public_shares.matches_resource(
            share,
            tenant_id=passport.tenant_id,
            owner_user_id=passport.owner_user_id,
            created_at=passport.created_at,
        )
        else None
    )
    photos = PassportStampPhoto.query.filter_by(
        passport_id=passport.id, uploader_user_id=session["user_id"]
    ).all()
    return render_template(
        "passports/public_settings.html",
        passport=passport,
        owner=owner,
        participant=participant,
        share=share,
        error=error,
        public_url=url_for("passports.public_view", token=token, _external=True)
        if token and owner
        else None,
        publishes=participant is not None
        and public_access.publishes_visits(passport, participant),
        frequency=participant is not None
        and public_access.current_consent(participant.public_frequency_at, participant),
        photos=photos,
        photo_selected=lambda photo: (
            participant is not None
            and public_access.publishes_photo(passport, participant, photo)
        ),
    ), status


@passports_bp.route("/public/<token>/print")
@passports_bp.route("/public/<token>/data.json")
@passports_bp.route("/public/<token>")
@limiter.limit("120 per minute", exempt_when=_rate_limiting_disabled)
def public_view(token: str) -> ResponseReturnValue:
    context = public_access.resolve(token)
    if context is None:
        return _unavailable()
    _share, passport = context
    view = public_projection.project(passport, token)
    if request.path.endswith("/data.json"):
        return jsonify(view)
    return render_template(
        "passports/public_view.html",
        view=view,
        public_passport=True,
        printable=request.path.endswith("/print"),
        include_photos=request.args.get("photos") == "1",
        public_json=url_for("passports.public_view", token=token) + "/data.json",
        public_print=url_for("passports.public_view", token=token) + "/print",
    )


@passports_bp.route("/public/<token>/photos/<int:photo_id>")
@limiter.limit("240 per minute", exempt_when=_rate_limiting_disabled)
def public_photo(token: str, photo_id: int) -> ResponseReturnValue:
    context = public_access.resolve(token)
    if context is None:
        return _unavailable()
    _share, passport = context
    photo = db.session.get(PassportStampPhoto, photo_id, populate_existing=True)
    participant = next(
        (
            p
            for p in public_access.participants(passport)
            if photo is not None and p.id == photo.participant_id
        ),
        None,
    )
    if (
        photo is None
        or participant is None
        or not public_access.publishes_photo(passport, participant, photo)
    ):
        return _unavailable()
    path = photo_path(photo.filename)
    if not path.is_file():
        return _unavailable()
    return send_file(
        path, mimetype="image/jpeg", conditional=False, etag=False, max_age=0
    )
