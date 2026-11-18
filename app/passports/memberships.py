"""Group/edition withdrawal and owner removal, independent of fleet roles.

Callers commit. Membership changes lock the series before all group-edition
parents, so existing voucher writers finish before access is revoked.
"""

from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    User,
    db,
)
from sqlalchemy.exc import OperationalError

from passports import groups, invitations, tokens
from passports.management import BuilderError, require_actor


def locked(
    tenant_id: int, user_id: int, series_id: int, *, owner: bool = False
) -> tuple[PassportSeries, list[Passport]]:
    series = PassportSeries.query.filter_by(
        id=series_id, tenant_id=tenant_id, is_group=True
    ).first()
    if series is None:
        raise BuilderError(_("Group not found."), 404)
    groups.lock_series(series.id)
    series = (
        PassportSeries.query.filter_by(id=series_id, tenant_id=tenant_id, is_group=True)
        .populate_existing()
        .first()
    )
    if series is None:
        raise BuilderError(_("Group not found."), 404)
    groups.require_series(series, user_id)
    if owner:
        require_actor(tenant_id, user_id)
        if series.owner_user_id != user_id:
            raise BuilderError(_("Only the group owner can manage other members."), 403)
    try:
        passports = (
            Passport.query.filter_by(
                series_id=series.id, tenant_id=tenant_id, is_group=True
            )
            .order_by(Passport.id)
            .with_for_update()
            .populate_existing()
            .all()
        )
    except OperationalError as exc:
        groups.lock_failure(exc)
    return series, passports


def _scope(passports: list[Passport], passport_id: int | None) -> list[int]:
    selected = [p.id for p in passports if passport_id is None or p.id == passport_id]
    if passport_id is not None and not selected:
        raise BuilderError(_("Passport not found."), 404)
    return selected


def _parts(ids: list[int], user_id: int) -> list[PassportParticipant]:
    parts: list[PassportParticipant] = (
        PassportParticipant.query.filter(
            PassportParticipant.passport_id.in_(ids),
            PassportParticipant.user_id == user_id,
        )
        .populate_existing()
        .all()
    )
    return parts


def _withdraw(parts: list[PassportParticipant], *, blocked: bool = False) -> None:
    for p in parts:
        p.is_active, p.share_visits, p.share_history = False, False, False
        if blocked:
            p.blocked = True


def leave(
    tenant_id: int, user_id: int, series_id: int, *, passport_id: int | None = None
) -> None:
    series, passports = locked(tenant_id, user_id, series_id)
    if series.owner_user_id == user_id:
        raise BuilderError(_("Transfer group ownership before leaving."), 409)
    ids = _scope(passports, passport_id)
    parts = _parts(ids, user_id)
    if passport_id is not None and not any(
        p.is_active and not p.blocked and p.accepted_at is not None for p in parts
    ):
        raise BuilderError(_("Passport not found."), 404)
    _withdraw(parts)
    if passport_id is None:
        groups.require_series(series, user_id).is_active = False


def remove(
    tenant_id: int,
    owner_id: int,
    series_id: int,
    user_id: int,
    *,
    passport_id: int | None = None,
) -> None:
    series, passports = locked(tenant_id, owner_id, series_id, owner=True)
    if user_id == series.owner_user_id:
        raise BuilderError(_("Transfer group ownership before leaving."), 409)
    ids = _scope(passports, passport_id)
    member = db.session.get(
        PassportSeriesMember, (series.id, user_id), populate_existing=True
    )
    parts = _parts(ids, user_id)
    if member is None or (passport_id is not None and not parts):
        raise BuilderError(_("Member not found."), 404)
    user = db.session.get(User, user_id, populate_existing=True)
    if user is None:
        raise BuilderError(_("Member not found."), 404)
    _withdraw(parts, blocked=True)
    if passport_id is None:
        member.is_active, member.blocked = False, True
    fingerprint = tokens.email_digest(user.email)
    row = PassportGroupBlock.query.filter_by(
        series_id=series.id, passport_id=passport_id, email_hash=fingerprint
    ).first()
    if row is None:
        db.session.add(
            PassportGroupBlock(
                series_id=series.id,
                passport_id=passport_id,
                user_id=user.id,
                email_hash=fingerprint,
                display_name=member.display_name,
            )
        )


def reinstate(tenant_id: int, owner_id: int, series_id: int, block_id: int) -> None:
    series, passports = locked(tenant_id, owner_id, series_id, owner=True)
    row = PassportGroupBlock.query.filter_by(id=block_id, series_id=series.id).first()
    if row is None:
        raise BuilderError(_("Member not found."), 404)
    user_id, passport_id = row.user_id, row.passport_id
    db.session.delete(row)
    db.session.flush()
    if user_id is None:
        return  # The keyed-address block alone protected a deleted identity.
    user = db.session.get(User, user_id, populate_existing=True)
    if user is None:
        return
    remaining = PassportGroupBlock.query.filter(
        PassportGroupBlock.series_id == series.id,
        db.or_(
            PassportGroupBlock.user_id == user_id,
            PassportGroupBlock.email_hash == tokens.email_digest(user.email),
        ),
    ).all()
    group_blocked = any(b.passport_id is None for b in remaining)
    member = db.session.get(
        PassportSeriesMember, (series.id, user_id), populate_existing=True
    )
    if member is not None and passport_id is None:
        member.blocked, member.is_active = group_blocked, False
    for p in _parts(_scope(passports, passport_id), user_id):
        p.blocked = group_blocked or any(
            b.passport_id == p.passport_id for b in remaining
        )
        # Reinstatement permits fresh acceptance; it never re-shares visits or
        # silently joins a new/historical edition on another person's behalf.


def roster(series: PassportSeries, actor_id: int) -> dict[str, Any]:
    groups.require_series(series, actor_id)
    owner = series.owner_user_id == actor_id
    members = PassportSeriesMember.query.filter_by(series_id=series.id)
    if not owner:
        members = members.filter_by(is_active=True, blocked=False)
    can_manage = owner
    if owner:
        try:
            require_actor(series.tenant_id, actor_id)
        except BuilderError:
            can_manage = False
    return {
        "owner": owner,
        "can_manage": can_manage,
        "members": members.order_by(
            PassportSeriesMember.display_name, PassportSeriesMember.user_id
        ).all(),
        "blocks": PassportGroupBlock.query.filter_by(series_id=series.id)
        .order_by(PassportGroupBlock.created_at)
        .all()
        if owner
        else [],
        "editions": Passport.query.filter_by(
            series_id=series.id, tenant_id=series.tenant_id, is_group=True
        )
        .order_by(Passport.series_display_order, Passport.start_date, Passport.id)
        .all(),
        "accepted": {
            p.passport_id
            for p in PassportParticipant.query.filter_by(
                user_id=actor_id, is_active=True, blocked=False
            )
            .filter(PassportParticipant.accepted_at.is_not(None))
            .all()
        },
    }


def joinable_editions(series: PassportSeries, user_id: int) -> list[dict[str, Any]]:
    groups.require_series(series, user_id)
    user = db.session.get(User, user_id)
    if user is None or user.view_only:
        return []
    rows = (
        db.session.query(Passport, PassportInvitation)
        .join(PassportInvitation, PassportInvitation.passport_id == Passport.id)
        .filter(
            Passport.series_id == series.id,
            Passport.tenant_id == series.tenant_id,
            Passport.is_group.is_(True),
        )
        .order_by(Passport.start_date, Passport.id)
        .all()
    )
    parts = {p.passport_id: p for p in _parts([p.id for p, _ in rows], user_id)}
    blocks = PassportGroupBlock.query.filter(
        PassportGroupBlock.series_id == series.id,
        db.or_(
            PassportGroupBlock.user_id == user_id,
            PassportGroupBlock.email_hash == tokens.email_digest(user.email),
        ),
    ).all()
    if any(b.passport_id is None for b in blocks):
        return []
    blocked_ids = {b.passport_id for b in blocks}
    offers = []
    for passport, invitation in rows:
        part = parts.get(passport.id)
        accepted = (
            part is not None
            and part.is_active
            and not part.blocked
            and part.accepted_at is not None
            and part.share_visits
        )
        if (
            invitations.available(invitation, passport)
            and not accepted
            and passport.id not in blocked_ids
            and not (part is not None and part.blocked)
        ):
            offers.append(
                {
                    "passport": passport,
                    "token": tokens.recover(
                        "invite",
                        passport.id,
                        invitation.key_nonce,
                        invitation.token_hash,
                    ),
                }
            )
    return offers
