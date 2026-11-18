"""Edition-specific invitation capabilities and explicit, idempotent enrolment.

Callers commit. Lock group membership before passport parents; no network work
belongs in these transactions. Tokens are never account identities.
"""

from datetime import UTC, date, datetime, time

from flask import session
from flask_babel import gettext as _
from models import (
    AuthChallenge,
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    Tenant,
    TenantUser,
    User,
    db,
    fits_column,
)
from services import auth_challenges
from sqlalchemy.exc import OperationalError

from passports import groups, tokens, vouchers
from passports.management import BuilderError, owned_passport, require_actor

JOIN_KEY = "passport_join"
JOIN_PURPOSE = "passport_join"
JOIN_SECONDS = 7200


def _now() -> datetime:
    return datetime.now(UTC)


def _expiry(value: datetime | None) -> datetime | None:
    return (
        value.replace(tzinfo=UTC)
        if value is not None and value.tzinfo is None
        else value
    )


def available(invitation: PassportInvitation, passport: Passport) -> bool:
    expiry = _expiry(invitation.expires_at)
    return bool(
        invitation.enabled
        and (expiry is None or expiry >= _now())
        and passport.is_group
        and passport.series_id is not None
        and passport.archived_at is None
        and (passport.end_date is None or passport.end_date >= date.today())
        and tokens.recover(
            "invite", passport.id, invitation.key_nonce, invitation.token_hash
        )
        is not None
    )


def lookup(token: str) -> tuple[PassportInvitation, Passport]:
    if not tokens.valid_format(token):
        raise BuilderError(_("This passport invitation is not available."), 404)
    return lookup_hash(tokens.digest(token))


def lookup_hash(
    token_hash: str, *, passport_id: int | None = None
) -> tuple[PassportInvitation, Passport]:
    query = (
        db.session.query(PassportInvitation, Passport)
        .join(Passport, Passport.id == PassportInvitation.passport_id)
        .join(Tenant, Tenant.id == Passport.tenant_id)
        .join(PassportSeries, PassportSeries.id == Passport.series_id)
        .filter(
            PassportInvitation.token_hash == token_hash,
            Tenant.is_active.is_(True),
            PassportSeries.is_group.is_(True),
        )
    )
    if passport_id is not None:
        query = query.filter(Passport.id == passport_id)
    result = query.populate_existing().first()
    if result is None or not available(result[0], result[1]):
        raise BuilderError(_("This passport invitation is not available."), 404)
    return result[0], result[1]


def owner_passport(tenant_id: int, user_id: int, passport_id: int) -> Passport:
    require_actor(tenant_id, user_id)
    passport = Passport.query.filter_by(
        id=passport_id, tenant_id=tenant_id, owner_user_id=user_id, is_group=True
    ).first()
    if passport is None or passport.series_id is None:
        raise BuilderError(_("Group not found."), 404)
    series_id = passport.series_id
    groups.lock_series(series_id)
    try:
        passport = owned_passport(tenant_id, user_id, passport_id, archived_ok=True)
    except OperationalError as exc:
        groups.lock_failure(exc)
    if passport.series_id != series_id:
        raise BuilderError(_("This passport changed. Open it again."), 409)
    return passport


def replace(passport: Passport, expires_on: str = "") -> PassportInvitation:
    if passport.archived_at is not None or (
        passport.end_date and passport.end_date < date.today()
    ):
        raise BuilderError(_("This edition has ended or is archived."), 409)
    try:
        day = date.fromisoformat(expires_on) if expires_on else None
    except ValueError as exc:
        raise BuilderError(_("Enter a valid invitation expiry date.")) from exc
    if day is not None and day < date.today():
        raise BuilderError(_("Choose today or a future invitation expiry date."))
    nonce, _token, token_hash = tokens.create("invite", passport.id)
    row = db.session.get(PassportInvitation, passport.id)
    if row is None:
        row = PassportInvitation(passport_id=passport.id)
        db.session.add(row)
    row.key_nonce, row.token_hash, row.enabled = nonce, token_hash, True
    row.expires_at = datetime.combine(day, time.max, UTC) if day else None
    return row


def sharing_values(values: dict[str, str]) -> dict[str, str]:
    name = values.get("display_name", "").strip()
    if not name or not fits_column(PassportParticipant.display_name, name):
        raise BuilderError(_("Enter a group display name of at most 128 characters."))
    if values.get("share_visits") != "yes":
        raise BuilderError(
            _(
                "Confirm sharing your eligible visit dates with this edition's participants."
            )
        )
    return {
        "display_name": name,
        "share_visits": "yes",
        "share_history": "yes" if values.get("share_history") == "yes" else "",
    }


def prepare(token: str, values: dict[str, str]) -> Passport:
    invitation, passport = lookup(token)
    sharing = sharing_values(values)
    auth_challenges.issue(
        JOIN_KEY,
        JOIN_PURPOSE,
        payload={
            "passport_id": passport.id,
            "token_hash": invitation.token_hash,
            "sharing": sharing,
            "passport_title": passport.title,
        },
        lifetime=JOIN_SECONDS,
    )
    return passport


def pending() -> tuple[AuthChallenge, Passport] | None:
    challenge = auth_challenges.get(JOIN_KEY, JOIN_PURPOSE)
    if challenge is None:
        return None
    if challenge.payload.get("completed_by") is not None:
        passport = db.session.get(Passport, challenge.payload["passport_id"])
        return (challenge, passport) if passport is not None else None
    try:
        _invite, passport = lookup_hash(
            challenge.payload["token_hash"],
            passport_id=challenge.payload["passport_id"],
        )
    except BuilderError:
        return None
    return challenge, passport


def blocked(passport: Passport, user: User) -> bool:
    fingerprint = tokens.email_digest(user.email)
    return (
        PassportGroupBlock.query.filter(
            PassportGroupBlock.series_id == passport.series_id,
            db.or_(
                PassportGroupBlock.passport_id.is_(None),
                PassportGroupBlock.passport_id == passport.id,
            ),
            db.or_(
                PassportGroupBlock.user_id == user.id,
                PassportGroupBlock.email_hash == fingerprint,
            ),
        ).first()
        is not None
    )


def enrolled(passport: Passport, user_id: int) -> bool:
    series = (
        db.session.get(PassportSeries, passport.series_id)
        if passport.series_id is not None
        else None
    )
    if series is None or groups.membership(series, user_id) is None:
        return False
    return (
        PassportParticipant.query.filter_by(
            passport_id=passport.id,
            user_id=user_id,
            is_active=True,
            blocked=False,
            share_visits=True,
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .first()
        is not None
    )


def join(
    user_id: int, token_hash: str, passport_id: int, values: dict[str, str]
) -> Passport:
    _invite, passport = lookup_hash(token_hash, passport_id=passport_id)
    groups.lock_series(passport.series_id)
    passport = groups.lock_passport(passport)
    lookup_hash(token_hash, passport_id=passport.id)
    user = db.session.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active or user.view_only:
        raise BuilderError(_("You cannot manage passports with this account."), 403)
    if (
        auth_challenges.requires_totp(user, tenant_id=passport.tenant_id)
        and not user.totp_secret
    ):
        raise BuilderError(
            _("Set up two-factor authentication before joining this passport."), 409
        )
    series_member = db.session.get(
        PassportSeriesMember, (passport.series_id, user_id), populate_existing=True
    )
    participant = (
        PassportParticipant.query.filter_by(passport_id=passport.id, user_id=user_id)
        .populate_existing()
        .first()
    )
    if (
        blocked(passport, user)
        or (series_member is not None and series_member.blocked)
        or (participant is not None and participant.blocked)
    ):
        raise BuilderError(_("The group owner must reinstate your membership."), 403)
    if enrolled(passport, user_id):
        return passport
    sharing = sharing_values(values)
    membership = db.session.get(TenantUser, (user_id, passport.tenant_id))
    if membership is None:
        db.session.add(
            TenantUser(
                user_id=user_id,
                tenant_id=passport.tenant_id,
                role=None,
                access_scope="passport",
            )
        )
        db.session.flush()
    if participant is None:
        participant = PassportParticipant(
            passport_id=passport.id,
            user_id=user_id,
            display_name=sharing["display_name"],
        )
        db.session.add(participant)
    participant.is_active, participant.accepted_at = True, _now()
    db.session.flush()
    groups.consent(passport, user_id, sharing)
    vouchers.provision_participant(passport, user_id)
    return passport


def complete(user: User) -> Passport | None:
    context = pending()
    if context is None:
        if session.get(JOIN_KEY):
            raise BuilderError(_("This passport invitation is not available."), 404)
        return None
    challenge, passport = context
    if passport.series_id is None:
        raise BuilderError(_("Group not found."), 404)
    groups.lock_series(passport.series_id)
    passport = groups.lock_passport(passport)
    fresh = (
        AuthChallenge.query.filter_by(
            token_hash=challenge.token_hash, purpose=JOIN_PURPOSE
        )
        .populate_existing()
        .first()
    )
    if fresh is None or fresh.expires_at <= int(_now().timestamp()):
        raise BuilderError(_("This passport invitation is not available."), 409)
    expected = fresh.payload.get("expected_user_id")
    if expected is not None and expected != user.id:
        raise BuilderError(
            _("Sign in with the account used for this registration."), 403
        )
    completed_by = fresh.payload.get("completed_by")
    if completed_by is not None:
        if completed_by != user.id or not enrolled(passport, user.id):
            raise BuilderError(_("This passport invitation is not available."), 403)
    else:
        passport = join(
            user.id, fresh.payload["token_hash"], passport.id, fresh.payload["sharing"]
        )
        # Keep a short-lived result for concurrent/replayed completions of the same
        # authenticated identity. It cannot enrol a second account.
        fresh.payload = {**fresh.payload, "completed_by": user.id}
    session.pop(JOIN_KEY, None)
    return passport
