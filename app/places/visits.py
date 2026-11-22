"""Visits to a place (Phase 42e): any member of its collection — editor or
reader — records their own visits and corrects or removes only those. The
place page shows the history to members only ("Last visited 12 May 2026 by
Anna and you"). A visit outlives its author's membership: it then shows as a
former member's.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from flask_babel import format_date
from flask_babel import gettext as _
from models import Place, PlaceVisit, User, db

from places.access import PlaceError, actor, place_access

MAX_NOTE = 500


class NotFound(PlaceError):
    """No such place or visit for this member: shown as a 404."""


def _place(tenant_id: int, user_id: int, place_id: int) -> Place:
    actor(tenant_id, user_id, writing=True)
    try:
        return place_access(tenant_id, user_id, place_id)
    except PlaceError as exc:
        raise NotFound(str(exc)) from exc


def _values(data: Mapping[str, str]) -> tuple[date, str | None]:
    raw = (data.get("visit_date") or "").strip()
    try:
        visited = date.fromisoformat(raw) if raw else date.today()
    except ValueError as exc:
        raise PlaceError(_("Enter the visit date as a valid date.")) from exc
    if visited > date.today():
        raise PlaceError(_("A visit cannot be in the future."))
    if visited.year < 1900:
        raise PlaceError(_("Enter the visit date as a valid date."))
    note = " ".join((data.get("note") or "").split()) or None
    if note is not None and len(note) > MAX_NOTE:
        raise PlaceError(
            _("Keep the visit note under %(count)s characters.", count=MAX_NOTE)
        )
    return visited, note


def record(
    tenant_id: int, user_id: int, place_id: int, data: Mapping[str, str]
) -> PlaceVisit:
    """A visit by the signed-in member, today unless another date is given."""
    place = _place(tenant_id, user_id, place_id)
    visited, note = _values(data)
    visit = PlaceVisit(
        place_id=place.id, user_id=user_id, visit_date=visited, note=note
    )
    db.session.add(visit)
    db.session.flush()
    return visit


def _own_visit(
    tenant_id: int, user_id: int, place_id: int, visit_id: int
) -> PlaceVisit:
    place = _place(tenant_id, user_id, place_id)
    visit = db.session.get(PlaceVisit, visit_id)
    if visit is None or visit.place_id != place.id or visit.user_id != user_id:
        raise NotFound(_("Visit not found."))
    return visit


def update(
    tenant_id: int,
    user_id: int,
    place_id: int,
    visit_id: int,
    data: Mapping[str, str],
) -> PlaceVisit:
    visit = _own_visit(tenant_id, user_id, place_id, visit_id)
    visit.visit_date, visit.note = _values(data)
    db.session.flush()
    return visit


def delete(tenant_id: int, user_id: int, place_id: int, visit_id: int) -> None:
    db.session.delete(_own_visit(tenant_id, user_id, place_id, visit_id))
    db.session.flush()


def _labels(user_ids: set[int | None], viewer_id: int) -> dict[int | None, str]:
    """How each visitor is named to the viewer, in one query."""
    known: dict[int, str] = {
        user.id: str(user.display_name)
        for user in User.query.filter(
            User.id.in_([uid for uid in user_ids if uid is not None])
        )
    }
    former = str(_("a former member"))
    return {
        uid: str(_("you"))
        if uid == viewer_id
        else known.get(uid, former)
        if uid is not None
        else former
        for uid in user_ids
    }


def _joined(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    joined = _("%(names)s and %(last)s", names=", ".join(names[:-1]), last=names[-1])
    return str(joined)


def panel(tenant_id: int, user_id: int, place: Place) -> dict[str, Any]:
    """What the place page shows about visits. The caller has already checked
    that the viewer is a member of the place's collection."""
    visits = (
        PlaceVisit.query.filter_by(place_id=place.id)
        .order_by(PlaceVisit.visit_date.desc(), PlaceVisit.id.desc())
        .all()
    )
    labels = _labels({v.user_id for v in visits}, user_id)
    summary = None
    if visits:
        last = visits[0].visit_date
        on_last: list[str] = []
        for visit in visits:
            label = labels[visit.user_id]
            if visit.visit_date == last and label not in on_last:
                on_last.append(label)
        # "you" reads best last: "by Anna and you".
        on_last.sort(key=lambda label: label == _("you"))
        summary = _(
            "Last visited %(date)s by %(people)s",
            date=format_date(last, "long"),
            people=_joined(on_last),
        )
    try:
        actor(tenant_id, user_id, writing=True)
        can_record = True
    except PlaceError:
        can_record = False
    return {
        "history": [
            {
                "id": v.id,
                "date": v.visit_date,
                "by": labels[v.user_id],
                "mine": v.user_id == user_id,
                "note": v.note,
                "flight_id": v.flight_id if v.user_id == user_id else None,
            }
            for v in visits
        ],
        "summary": summary,
        "visited": any(v.user_id == user_id for v in visits),
        "can_record": can_record,
        "today": date.today().isoformat(),
    }
