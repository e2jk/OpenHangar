"""Crew invites: ask another pilot of the same tenant to confirm their slot on
a flight someone else logged (docs/backlog.md "Follow-up: let a second crew
member claim their own slot on an existing flight").

Flow: the logger picks a tenant pilot in the PIC / second-crew name field
(the picker posts that pilot's user id alongside the free-text name) →
``sync_crew_invites`` records a pending ``FlightCrewInvite`` → the invited
pilot is e-mailed and sees it on their dashboard and pilot logbook →
``accept_invite`` writes their user id into the slot, ``decline_invite``
leaves the slot name-only.

The reverse direction — a *claim* — uses the same table (``kind="claim"``):
a pilot logging a flight that the duplicate warning finds already logged by
someone else asks to be added to it (``claim_option`` / ``create_claim``),
and the pilot who owns that flight's shared fields approves or declines.

The slot's user id is only ever set on acceptance — never silently by the
logger or the claimant — so a flight can't appear in someone's logbook (or
count towards their totals/currency) without both sides agreeing.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from flask import url_for  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    CrewInviteKind,
    CrewInviteStatus,
    CrewRole,
    CrewSlot,
    Flight,
    FlightCrewInvite,
    NotificationType,
    User,
    db,
)
from sqlalchemy import or_  # pyright: ignore[reportMissingImports]
from utils import tenant_pilots  # pyright: ignore[reportMissingImports]

from flights.shared_flight import (  # pyright: ignore[reportMissingImports]
    SECOND_CREW_FUNCTION_FIELD,
    dispatch_to_user,
    shared_editor_ids,
)

log = logging.getLogger(__name__)

# Form field carrying the picked pilot's user id, per slot.
SLOT_USER_ID_FIELDS: dict[str, str] = {
    CrewSlot.PIC: "crew_user_id_0",
    CrewSlot.SECOND: "crew_user_id_1",
}
# Free-text name field of the same slot.
SLOT_NAME_FIELDS: dict[str, str] = {
    CrewSlot.PIC: "crew_name_0",
    CrewSlot.SECOND: "crew_name_1",
}


def _slot_user_id(fe: Flight, slot: str) -> int | None:
    user_id: int | None = (
        fe.pic_user_id if slot == CrewSlot.PIC else fe.second_crew_user_id
    )
    return user_id


def _invite_flight(invite: FlightCrewInvite) -> Flight:
    fe = db.session.get(Flight, invite.flight_id)
    assert fe is not None, "flight_id is a non-null CASCADE foreign key"
    return fe


def own_slot(pilot_role: str) -> str | None:
    """The slot the logged-in user occupies for a flight form ``pilot_role``."""
    return {"pic": CrewSlot.PIC, "dual": CrewSlot.SECOND}.get(pilot_role)


def requested_invites(
    form: Any, uid: int, tenant_id: int, pilot_role: str
) -> dict[str, int | None]:
    """Resolve the picker's hidden user-id fields into ``{slot: user_id}``.

    A user id is only honoured when it belongs to an active pilot of
    *tenant_id* and isn't the logged-in user; the slot the logged-in user
    occupies themselves (per ``pilot_role``) never gets an invite. Anything
    else resolves to ``None`` — the name stays free text.
    """
    labels = dict(tenant_pilots(tenant_id))
    mine = own_slot(pilot_role)
    result: dict[str, int | None] = {}
    for slot, field in SLOT_USER_ID_FIELDS.items():
        raw = (form.get(field) or "").strip()
        user_id = int(raw) if raw.isdigit() else None
        # The name actually submitted must still be that pilot's suggestion
        # label — guards against a stale hidden id after the text was edited.
        name = (form.get(SLOT_NAME_FIELDS[slot]) or "").strip().casefold()
        if (
            slot == mine
            or user_id is None
            or user_id == uid
            or user_id not in labels
            or labels[user_id].casefold() != name
        ):
            user_id = None
        result[slot] = user_id
    return result


def pending_invites_for_flight(fe: Flight) -> dict[str, FlightCrewInvite]:
    return {
        inv.slot: inv
        for inv in FlightCrewInvite.query.filter_by(
            flight_id=fe.id,
            status=CrewInviteStatus.PENDING,
            kind=CrewInviteKind.INVITE,
        ).all()
    }


def sync_crew_invites(
    fe: Flight, inviter_id: int, requested: dict[str, int | None]
) -> list[FlightCrewInvite]:
    """Bring *fe*'s pending invites in line with *requested* and return the
    newly created ones (the caller notifies them after committing).

    - A slot that already holds a user id is left alone: an accepted invite
      is never undone by the logger re-saving the form.
    - A pending invite whose user is no longer requested is cancelled.
    - A user who already declined this slot isn't asked again automatically.
    """
    now = datetime.now(UTC)
    pending = pending_invites_for_flight(fe)
    created: list[FlightCrewInvite] = []
    for slot in CrewSlot.ALL:
        target = requested.get(slot)
        current = pending.get(slot)
        if _slot_user_id(fe, slot) is not None:
            target = None
        if current is not None and current.invited_user_id == target:
            continue
        if current is not None:
            current.status = CrewInviteStatus.CANCELLED
            current.responded_at = now
        if target is None or target in (fe.pic_user_id, fe.second_crew_user_id):
            continue
        already_declined = FlightCrewInvite.query.filter_by(
            flight_id=fe.id,
            slot=slot,
            invited_user_id=target,
            status=CrewInviteStatus.DECLINED,
            kind=CrewInviteKind.INVITE,
        ).first()
        if already_declined:
            continue
        # A flight logged before created_by was tracked: whoever names the
        # crew becomes its logger, so the invited pilot can't edit its shared
        # fields once they confirm (flights/shared_flight.py).
        if fe.created_by_user_id is None:
            fe.created_by_user_id = inviter_id
        invite = FlightCrewInvite(
            flight_id=fe.id,
            slot=slot,
            invited_user_id=target,
            invited_by_user_id=inviter_id,
        )
        db.session.add(invite)
        created.append(invite)
    return created


def pending_invites_for_user(user_id: int) -> list[FlightCrewInvite]:
    invites: list[FlightCrewInvite] = (
        FlightCrewInvite.query.join(Flight, Flight.id == FlightCrewInvite.flight_id)
        .filter(
            FlightCrewInvite.invited_user_id == user_id,
            FlightCrewInvite.status == CrewInviteStatus.PENDING,
            FlightCrewInvite.kind == CrewInviteKind.INVITE,
        )
        .order_by(Flight.date.desc(), Flight.id.desc())
        .all()
    )
    return invites


def pending_invite_count(user_id: int) -> int:
    count: int = FlightCrewInvite.query.filter_by(
        invited_user_id=user_id,
        status=CrewInviteStatus.PENDING,
        kind=CrewInviteKind.INVITE,
    ).count()
    return count


# ── Claims: asking to be added to a flight someone else logged ────────────────


def claim_option(fe: Flight, user_id: int, pilot_role: str) -> dict[str, Any] | None:
    """Whether *user_id*, logging what turns out to be *fe* (duplicate
    warning) in role *pilot_role*, can ask to be added to it instead.

    Needs a free slot matching their role and at least one pilot who owns the
    flight's shared fields to approve. Returns the slot, the approvers' names
    and whether they already asked, or ``None``.
    """
    slot = own_slot(pilot_role)
    if slot is None or fe.slot_for(user_id) is not None:
        return None
    if _slot_user_id(fe, slot) is not None:
        return None
    approvers = sorted(shared_editor_ids(fe))
    if not approvers:
        return None
    already = (
        FlightCrewInvite.query.filter_by(
            flight_id=fe.id,
            invited_user_id=user_id,
            status=CrewInviteStatus.PENDING,
            kind=CrewInviteKind.CLAIM,
        ).first()
        is not None
    )
    names = []
    for approver_id in approvers:
        approver = db.session.get(User, approver_id)
        names.append(approver.display_name if approver else "—")
    return {
        "slot": slot,
        "approver_names": ", ".join(names),
        "already_requested": already,
    }


def create_claim(
    fe: Flight, user_id: int, slot: str, requested_role: str | None
) -> FlightCrewInvite:
    role = (
        requested_role
        if slot == CrewSlot.SECOND and requested_role in SECOND_CREW_ROLES
        else None
    )
    claim = FlightCrewInvite(
        flight_id=fe.id,
        slot=slot,
        invited_user_id=user_id,
        invited_by_user_id=user_id,
        kind=CrewInviteKind.CLAIM,
        requested_role=role,
    )
    db.session.add(claim)
    return claim


SECOND_CREW_ROLES = (CrewRole.IP, CrewRole.COPILOT, CrewRole.STUDENT, CrewRole.SP)


def pending_claims_to_review(user_id: int) -> list[FlightCrewInvite]:
    """Pending claims on flights *user_id* owns the shared fields of."""
    candidates: list[FlightCrewInvite] = (
        FlightCrewInvite.query.join(Flight, Flight.id == FlightCrewInvite.flight_id)
        .filter(
            FlightCrewInvite.status == CrewInviteStatus.PENDING,
            FlightCrewInvite.kind == CrewInviteKind.CLAIM,
            or_(Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id),
        )
        .order_by(Flight.date.desc(), Flight.id.desc())
        .all()
    )
    return [c for c in candidates if user_id in shared_editor_ids(_invite_flight(c))]


def accept_invite(invite: FlightCrewInvite, user: User) -> bool:
    """Write *user* into the invite's slot. Returns False (and cancels the
    invite) when the slot was meanwhile filled or the user already occupies
    the flight's other slot."""
    fe = _invite_flight(invite)
    now = datetime.now(UTC)
    if _slot_user_id(fe, invite.slot) is not None or user.id in (
        fe.pic_user_id,
        fe.second_crew_user_id,
    ):
        invite.status = CrewInviteStatus.CANCELLED
        invite.responded_at = now
        return False

    if invite.slot == CrewSlot.PIC:
        fe.pic_user_id = user.id
        if not fe.pic_name:
            fe.pic_name = user.display_name
        if fe.function_pic is None:
            fe.function_pic = fe.flight_time
    else:
        fe.second_crew_user_id = user.id
        if not fe.second_crew_name:
            fe.second_crew_name = user.display_name
        if not fe.second_crew_role and invite.requested_role:
            fe.second_crew_role = invite.requested_role
        # A safety pilot has no dedicated EASA function column, so none is set.
        field = SECOND_CREW_FUNCTION_FIELD.get(fe.second_crew_role or "")
        if field and getattr(fe, field) is None:
            setattr(fe, field, fe.flight_time)

    invite.status = CrewInviteStatus.ACCEPTED
    invite.responded_at = now
    return True


def decline_invite(invite: FlightCrewInvite) -> None:
    invite.status = CrewInviteStatus.DECLINED
    invite.responded_at = datetime.now(UTC)


def notify_invite_answered(invite: FlightCrewInvite, accepted: bool) -> None:
    """Tell the pilot who sent the invite that it was confirmed or declined."""
    if invite.invited_by_user_id is None:
        return
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]

    fe = _invite_flight(invite)
    answered_by = db.session.get(User, invite.invited_user_id)
    name = answered_by.display_name if answered_by else "—"
    route = f"{fe.departure_icao or '?'} → {fe.arrival_icao or '?'}"
    if accepted:
        title = _l("%(name)s confirmed your flight")
        message = _l(
            "%(name)s confirmed the flight %(route)s on %(date)s; it is now in "
            "their pilot logbook too."
        )
    else:
        title = _l("%(name)s declined your flight")
        message = _l(
            "%(name)s declined the flight %(route)s on %(date)s; their name stays "
            "on your entry without a link to their logbook."
        )
    dispatch_to_user(
        invite.invited_by_user_id,
        NotificationType.CREW_INVITE_ANSWERED,
        {
            "subject_key": title,
            "subject_args": {"name": name},
            "notification_title_key": title,
            "notification_title_args": {"name": name},
            "notification_message_key": message,
            "notification_message_args": {
                "name": name,
                "route": route,
                "date": fe.date.isoformat(),
            },
            "cta_url": url_for("pilots.view_entry", entry_id=fe.id, _external=True),
        },
    )


def notify_claim_created(claim: FlightCrewInvite) -> None:
    """Ask the pilot(s) owning the flight's shared fields to approve a claim."""
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]

    fe = _invite_flight(claim)
    claimant = db.session.get(User, claim.invited_user_id)
    name = claimant.display_name if claimant else "—"
    route = f"{fe.departure_icao or '?'} → {fe.arrival_icao or '?'}"
    for approver_id in sorted(shared_editor_ids(fe)):
        dispatch_to_user(
            approver_id,
            NotificationType.CREW_CLAIM,
            {
                "subject_key": _l("%(name)s asks to be added to your flight"),
                "subject_args": {"name": name},
                "notification_title_key": _l(
                    "%(name)s asks to be added to your flight"
                ),
                "notification_title_args": {"name": name},
                "notification_message_key": _l(
                    "%(name)s says they were on your flight %(route)s on %(date)s "
                    "and asks to add it to their pilot logbook. Approve or decline "
                    "the request."
                ),
                "notification_message_args": {
                    "name": name,
                    "route": route,
                    "date": fe.date.isoformat(),
                },
                "cta_url": url_for("pilots.logbook", _external=True) + "#crew-claims",
                "cta_label": _l("Review in OpenHangar"),
            },
        )


def notify_claim_answered(
    claim: FlightCrewInvite, accepted: bool, actor_id: int
) -> None:
    """Tell the pilot who claimed a slot whether they were added."""
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]

    fe = _invite_flight(claim)
    actor = db.session.get(User, actor_id)
    name = actor.display_name if actor else "—"
    route = f"{fe.departure_icao or '?'} → {fe.arrival_icao or '?'}"
    if accepted:
        title = _l("%(name)s approved your request")
        message = _l(
            "%(name)s approved your request: the flight %(route)s on %(date)s is "
            "now in your pilot logbook."
        )
    else:
        title = _l("%(name)s declined your request")
        message = _l(
            "%(name)s declined your request to be added to the flight %(route)s "
            "on %(date)s."
        )
    dispatch_to_user(
        claim.invited_user_id,
        NotificationType.CREW_INVITE_ANSWERED,
        {
            "subject_key": title,
            "subject_args": {"name": name},
            "notification_title_key": title,
            "notification_title_args": {"name": name},
            "notification_message_key": message,
            "notification_message_args": {
                "name": name,
                "route": route,
                "date": fe.date.isoformat(),
            },
            "cta_url": url_for("pilots.logbook", _external=True),
        },
    )


def notify_invites(invites: list[FlightCrewInvite], tenant_id: int) -> None:
    """E-mail each invited pilot (respecting their notification preferences).
    Failures are logged, never raised — the flight is already saved."""
    if not invites:
        return
    from flask_babel import lazy_gettext as _l  # pyright: ignore[reportMissingImports]
    from services.notification_service import (  # pyright: ignore[reportMissingImports]
        dispatch,
    )

    for invite in invites:
        fe = _invite_flight(invite)
        inviter_user = (
            db.session.get(User, invite.invited_by_user_id)
            if invite.invited_by_user_id
            else None
        )
        inviter = inviter_user.display_name if inviter_user else "—"
        route = f"{fe.departure_icao or '?'} → {fe.arrival_icao or '?'}"
        if invite.slot == CrewSlot.PIC:
            slot_label = _l("PIC / Commander")
        elif fe.second_crew_role in CrewRole.LABELS:
            slot_label = _l(CrewRole.LABELS[fe.second_crew_role])
        else:
            slot_label = _l("Second crew")
        try:
            dispatch(
                NotificationType.CREW_INVITE,
                tenant_id,
                {
                    "subject_key": _l("Confirm your flight on %(date)s"),
                    "subject_args": {"date": fe.date.isoformat()},
                    "notification_title_key": _l("%(name)s added you to a flight"),
                    "notification_title_args": {"name": inviter},
                    "notification_message_key": _l(
                        "%(name)s logged a flight with you on board. Confirm it "
                        "to add it to your pilot logbook, or decline if it isn't "
                        "yours."
                    ),
                    "notification_message_args": {"name": inviter},
                    "details": [
                        (_l("Date"), fe.date.isoformat()),
                        (_l("Route"), route),
                        (_l("Aircraft"), fe.display_registration or "—"),
                        (_l("Your role"), slot_label),
                    ],
                    "cta_url": url_for("pilots.logbook", _external=True)
                    + "#crew-invites",
                    "cta_label": _l("Review in OpenHangar"),
                },
                target_user_ids=[invite.invited_user_id],
            )
        except Exception:
            log.exception("Failed to dispatch crew invite notification")
