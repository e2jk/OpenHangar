"""Email-verified, invitation-only registration with no general tenant signup.

Mail is sent outside group transactions. Verification consumes server-held state
once, under the same group lock as enrolment. Existing accounts must still sign
in with their password and authenticator; email proof never replaces those.
"""

import hashlib
import hmac
import os
import re
import secrets
import time
from email.errors import HeaderParseError
from email.headerregistry import Address

import pw_hash
from flask import current_app, session
from flask_babel import get_locale
from flask_babel import gettext as _
from models import (
    AuthChallenge,
    LoginThrottle,
    Passport,
    PassportGroupBlock,
    PassportSeriesMember,
    TenantUser,
    User,
    db,
    fits_column,
)
from services import auth_challenges, email_service, login_throttle
from services import capability_tokens as tokens
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import NoResultFound, OperationalError

from passports import groups, invitations
from passports.management import BuilderError

KEY = "passport_registration"
PURPOSE = "passport_registration"
SECONDS = 600
ATTEMPTS = 5


def _code_digest(code: str, nonce: str) -> str:
    return hmac.new(
        str(current_app.config["SECRET_KEY"]).encode(),
        ("passport-email:v1:" + nonce + ":" + code).encode(),
        hashlib.sha256,
    ).hexdigest()


def _email(value: str) -> str:
    email = value.strip().lower()
    try:
        if (
            not fits_column(User.email, email)
            or not email.isascii()
            or any(c.isspace() for c in email)
        ):
            raise ValueError("Invalid address")
        address = Address(addr_spec=email)
        email = address.addr_spec.lower()
        if (
            not email.isascii()
            or any(c.isspace() for c in email)
            or not address.username
            or "." not in address.domain
            or not fits_column(User.email, email)
        ):
            raise ValueError("Invalid address")
    except (ValueError, HeaderParseError) as exc:
        raise BuilderError(_("Enter a valid email address.")) from exc
    return email


def _claim_send(email: str) -> None:
    """One delivery per address/minute, atomically across processes and browsers."""
    login_throttle.purge_expired()
    now = int(time.time())
    table = LoginThrottle.__table__
    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    claimed = db.session.execute(
        insert(table)
        .values(
            key="passport-mail:" + tokens.email_digest(email),
            failures=0,
            window_expires_at=now + 60,
        )
        .on_conflict_do_update(
            index_elements=[table.c.key],
            set_={"window_expires_at": now + 60},
            where=table.c.window_expires_at <= now,
        )
        .returning(table.c.key)
    ).scalar_one_or_none()
    db.session.commit()
    if claimed is None:
        raise BuilderError(
            _("Please wait a minute before requesting another verification email."), 429
        )


def begin(token: str, values: dict[str, str]) -> Passport:
    invitation, passport = invitations.lookup(token)
    sharing = invitations.sharing_values(values)
    email = _email(values.get("email", ""))
    password = values.get("password", "")
    if not 12 <= len(password) <= 1024:
        raise BuilderError(_("Choose a password between 12 and 1024 characters."))
    if password != values.get("confirm_password", ""):
        raise BuilderError(_("Passwords do not match."))
    if os.environ.get("OPENHANGAR_ENV") == "demo":
        raise BuilderError(_("Registration email is disabled in demo mode."), 503)
    _claim_send(email)
    code, nonce = f"{secrets.randbelow(1000000):06d}", secrets.token_urlsafe(32)
    auth_challenges.issue(
        KEY,
        PURPOSE,
        payload={
            "passport_id": passport.id,
            "token_hash": invitation.token_hash,
            "passport_title": passport.title,
            "sharing": sharing,
            "email": email,
            "password_hash": pw_hash.hash(password),
            "locale": str(get_locale()),
            "code_hash": _code_digest(code, nonce),
            "nonce": nonce,
            "attempts": 0,
        },
        lifetime=SECONDS,
    )
    try:
        email_service.send_email(
            email,
            _("Verify your OpenHangar email"),
            _(
                "Your verification code is %(code)s. It expires in 10 minutes.\n\nYou requested an account to join %(passport)s. Your account and voucher balance belong to you. If you already have an account, you will still need to sign in with your password and authenticator.\n\nIf you did not request this, ignore this email.",
                code=code,
                passport=passport.title,
            ),
            locale=str(get_locale()),
        )
    except (email_service.EmailNotConfiguredError, email_service.EmailSendError) as exc:
        challenge = auth_challenges.get(KEY, PURPOSE)
        if challenge is not None:
            auth_challenges.consume(challenge)
            db.session.commit()
        session.pop(KEY, None)
        raise BuilderError(
            _(
                "We could not send the verification email. Please try again or contact the instance administrator."
            ),
            503,
        ) from exc
    return passport


def pending(token: str) -> tuple[AuthChallenge, Passport]:
    invitation, passport = invitations.lookup(token)
    challenge = auth_challenges.get(KEY, PURPOSE)
    if (
        challenge is None
        or challenge.payload["passport_id"] != passport.id
        or challenge.payload["token_hash"] != invitation.token_hash
    ):
        raise BuilderError(
            _("This email verification has expired. Start again from the invitation."),
            409,
        )
    return challenge, passport


def verify(token: str, code: str) -> tuple[User, bool, str | None]:
    """Return account, creation flag and new-account proof; existing users must sign in."""
    _challenge, passport = pending(token)
    groups.lock_series(passport.series_id)
    groups.lock_passport(passport)
    challenge, passport = pending(token)
    try:
        challenge = (
            AuthChallenge.query.filter_by(
                token_hash=challenge.token_hash, purpose=PURPOSE
            )
            .with_for_update()
            .populate_existing()
            .one()
        )
    except OperationalError as exc:
        groups.lock_failure(exc)
    except NoResultFound as exc:
        raise BuilderError(
            _("This email verification has expired. Start again from the invitation."),
            409,
        ) from exc
    if challenge.expires_at <= int(time.time()):
        raise BuilderError(
            _("This email verification has expired. Start again from the invitation."),
            409,
        )
    payload = challenge.payload
    if payload["attempts"] >= ATTEMPTS:
        raise BuilderError(
            _("Too many incorrect codes. Request a new verification email."), 429
        )
    if not re.fullmatch(r"[0-9]{6}", code) or not hmac.compare_digest(
        _code_digest(code, payload["nonce"]), payload["code_hash"]
    ):
        challenge.payload = {**payload, "attempts": payload["attempts"] + 1}
        db.session.commit()  # Failed attempts survive caller rollback and other workers.
        raise BuilderError(_("That verification code is not correct."))
    fingerprint = tokens.email_digest(payload["email"])
    if PassportGroupBlock.query.filter(
        PassportGroupBlock.series_id == passport.series_id,
        db.or_(
            PassportGroupBlock.passport_id.is_(None),
            PassportGroupBlock.passport_id == passport.id,
        ),
        PassportGroupBlock.email_hash == fingerprint,
    ).first():
        raise BuilderError(_("The group owner must reinstate your membership."), 403)
    if not auth_challenges.consume(challenge):
        raise BuilderError(
            _("This email verification has expired. Start again from the invitation."),
            409,
        )
    table = User.__table__
    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    try:
        user_id = db.session.execute(
            insert(table)
            .values(
                email=payload["email"],
                password_hash=payload["password_hash"],
                name=payload["sharing"]["display_name"],
                language=payload["locale"],
                is_pilot=False,
                is_maintenance=False,
                is_instance_admin=False,
                view_only=False,
            )
            .on_conflict_do_nothing(index_elements=[table.c.email])
            .returning(table.c.id)
        ).scalar_one_or_none()
        user = (
            User.query.filter_by(email=payload["email"])
            .populate_existing()
            .one_or_none()
        )
    except OperationalError as exc:
        groups.lock_failure(exc)
    if user is None:
        raise BuilderError(_("This account changed. Please try again."), 409)
    member = db.session.get(
        PassportSeriesMember, (passport.series_id, user.id), populate_existing=True
    )
    if invitations.blocked(passport, user) or (member is not None and member.blocked):
        raise BuilderError(_("The group owner must reinstate your membership."), 403)
    created = user_id is not None
    if created:
        db.session.add(
            TenantUser(
                user_id=user.id,
                tenant_id=passport.tenant_id,
                role=None,
                access_scope="personal",
            )
        )
    proof = auth_challenges.credential_binding(user) if created else None
    # Issuing the opaque hand-off commits account creation and challenge consumption
    # together. Sharing/enrolment follows only after any mandated authenticator.
    auth_challenges.issue(
        invitations.JOIN_KEY,
        invitations.JOIN_PURPOSE,
        payload={
            "passport_id": passport.id,
            "passport_title": passport.title,
            "token_hash": payload["token_hash"],
            "sharing": payload["sharing"],
            "expected_user_id": user.id,
        },
        lifetime=invitations.JOIN_SECONDS,
    )
    session.pop(KEY, None)
    return user, created, proof
