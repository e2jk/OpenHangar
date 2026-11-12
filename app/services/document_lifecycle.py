"""Lifecycle of typed certificates: which one is in force, which ones it
superseded, and how long coverage runs without a gap.

The same rules for an aircraft's certificates (insurance, ARC) and a
pilot's (medical, licence):

- In force today (`active`): among the documents of that type whose
  valid_from has arrived (or is unset — "in force as soon as uploaded"),
  the one valid the longest (latest valid_until; an open-ended one ranks
  last), then the latest valid_from, then the most recently uploaded. So
  uploading an older certificate for the record never displaces the
  current one, and a renewal valid for longer replaces it.
- Superseded: every other document of that type that has started. One
  that hasn't started yet (a renewal uploaded ahead of time) is
  "upcoming" — the current one stays in force until the renewal's
  valid_from, and only then is marked superseded. `refresh` stores this
  in Document.superseded_by_id; it runs on every upload, edit and delete,
  and daily (services.notification_service) so a renewal takes over on
  its start date even when nobody touches the documents that day.
- Covered until (`coverage_until`): the in-force document's valid_until,
  extended through any renewal already on file that starts no later than
  the day after — so there's no "expires soon" warning while the next
  certificate is already there, but a real gap is still reported.
- Synced dates: the in-force document's valid_until is copied to
  Aircraft.insurance_expiry/arc_expiry and PilotProfile.medical_expiry
  (see AIRCRAFT_FIELDS/PILOT_FIELDS), which the rest of the app reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DocType,
    Document,
    PilotProfile,
    Tenant,
    db,
)

# doc_type -> the Aircraft column its in-force document's valid_until is
# copied to (synced caches, not edited directly — see models.py).
AIRCRAFT_FIELDS: dict[str, str] = {
    DocType.INSURANCE_CERT: "insurance_expiry",
    DocType.ARC: "arc_expiry",
}
# doc_type -> the PilotProfile column it drives, or None (a licence has a
# lifecycle — replaced, superseded — but no expiry date on the profile).
PILOT_FIELDS: dict[str, str | None] = {
    DocType.MEDICAL: "medical_expiry",
    DocType.LICENSE: None,
}

ACTIVE = "active"
UPCOMING = "upcoming"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Owner:
    """Whose documents: an aircraft's own (component_id None), one of its
    components', or a pilot's."""

    aircraft_id: int | None = None
    component_id: int | None = None
    pilot_user_id: int | None = None

    def filters(self) -> list[Any]:
        if self.pilot_user_id is not None:
            return [Document.pilot_user_id == self.pilot_user_id]
        return [
            Document.aircraft_id == self.aircraft_id,
            Document.component_id == self.component_id,
        ]

    def lifecycle_types(self) -> set[str]:
        return set(PILOT_FIELDS if self.pilot_user_id is not None else AIRCRAFT_FIELDS)


def _of_type(owner: Owner, doc_type: str) -> Any:
    return Document.query.filter(*owner.filters(), Document.doc_type == doc_type)


def active(owner: Owner, doc_type: str, as_of: date | None = None) -> Document | None:
    """The document of that type in force on *as_of* (default today)."""
    as_of = as_of or date.today()
    # Ordered in SQL, not with a Python max(): a just-flushed Document's
    # tz-aware uploaded_at can't be compared with a committed sibling's
    # SQLite-naive one.
    result: Document | None = (
        _of_type(owner, doc_type)
        .filter(db.or_(Document.valid_from.is_(None), Document.valid_from <= as_of))
        .order_by(
            Document.valid_until.desc().nullslast(),
            Document.valid_from.desc().nullslast(),
            Document.uploaded_at.desc(),
            Document.id.desc(),
        )
        .first()
    )
    return result


def upcoming(owner: Owner, doc_type: str, as_of: date | None = None) -> Document | None:
    """The next document of that type on file that hasn't started yet."""
    as_of = as_of or date.today()
    result: Document | None = (
        _of_type(owner, doc_type)
        .filter(Document.valid_from > as_of)
        .order_by(Document.valid_from.asc(), Document.id.asc())
        .first()
    )
    return result


def coverage_until(
    owner: Owner, doc_type: str, as_of: date | None = None
) -> date | None:
    """How far coverage runs unbroken: the in-force document's valid_until,
    followed through renewals starting no later than the day after the
    previous end. None when nothing is in force or it has no end date."""
    as_of = as_of or date.today()
    current = active(owner, doc_type, as_of)
    if current is None or current.valid_until is None:
        return None
    frontier: date = current.valid_until
    while True:
        # Each step needs a valid_until past the frontier, so a document
        # can't be picked twice: no visited-set needed to stop the loop.
        nxt = (
            _of_type(owner, doc_type)
            .filter(
                Document.valid_from > as_of,
                Document.valid_from <= frontier + timedelta(days=1),
                Document.valid_until.isnot(None),
                Document.valid_until > frontier,
            )
            .order_by(Document.valid_until.desc())
            .first()
        )
        if nxt is None:
            return frontier
        frontier = nxt.valid_until


def states(owner: Owner, doc_type: str, as_of: date | None = None) -> dict[int, str]:
    """{document id: ACTIVE | UPCOMING | SUPERSEDED} for that type."""
    as_of = as_of or date.today()
    current = active(owner, doc_type, as_of)
    result: dict[int, str] = {}
    for doc in _of_type(owner, doc_type).all():
        if current is not None and doc.id == current.id:
            result[doc.id] = ACTIVE
        elif doc.valid_from is not None and doc.valid_from > as_of:
            result[doc.id] = UPCOMING
        else:
            result[doc.id] = SUPERSEDED
    return result


def refresh(owner: Owner, doc_type: str | None, as_of: date | None = None) -> None:
    """Store who supersedes whom for that type, and sync the date it drives.
    A no-op for a type without a lifecycle. Caller commits."""
    if not doc_type or doc_type not in owner.lifecycle_types():
        return
    as_of = as_of or date.today()
    db.session.flush()
    current = active(owner, doc_type, as_of)
    doc_states = states(owner, doc_type, as_of)
    for doc in _of_type(owner, doc_type).all():
        doc.superseded_by_id = (
            current.id
            if current is not None and doc_states.get(doc.id) == SUPERSEDED
            else None
        )

    if owner.pilot_user_id is None:
        if owner.component_id is None:
            ac = db.session.get(Aircraft, owner.aircraft_id)
            if ac is not None:
                # Its remembered "covered until" dates (Aircraft._covered_until).
                memo = getattr(ac, "_coverage_memo", None)
                if memo:
                    memo.clear()
                setattr(
                    ac,
                    AIRCRAFT_FIELDS[doc_type],
                    current.valid_until if current else None,
                )
        return
    field = PILOT_FIELDS[doc_type]
    # Only once a document of that type is in force: until then (none on
    # file, or only a renewal that hasn't started) the date typed on the
    # profile stays as it is.
    if field and current is not None and current.valid_until is not None:
        profile = PilotProfile.query.filter_by(user_id=owner.pilot_user_id).first()
        if profile is None:
            profile = PilotProfile(user_id=owner.pilot_user_id)
            db.session.add(profile)
        setattr(profile, field, current.valid_until)


def refresh_all(as_of: date | None = None) -> None:
    """Daily: every active tenant's aircraft, and every pilot with typed
    documents, so a renewal takes over on its start date. Caller commits."""
    for tenant in Tenant.query.filter_by(is_active=True).all():
        for ac in Aircraft.query.filter_by(tenant_id=tenant.id, archived_at=None).all():
            for doc_type in AIRCRAFT_FIELDS:
                refresh(Owner(aircraft_id=ac.id), doc_type, as_of)
    pilots = (
        db.session.query(Document.pilot_user_id, Document.doc_type)
        .filter(
            Document.pilot_user_id.isnot(None),
            Document.doc_type.in_(list(PILOT_FIELDS)),
        )
        .distinct()
        .all()
    )
    for user_id, doc_type in pilots:
        refresh(Owner(pilot_user_id=user_id), doc_type, as_of)


def medical_covered_until(profile: Any, as_of: date | None = None) -> date | None:
    """A pilot's medical as far as unbroken coverage goes — through a
    renewal already on file — or the profile's own date when no medical
    document is in force (none uploaded, or a profile without a user)."""
    expiry: date | None = getattr(profile, "medical_expiry", None)
    user_id = getattr(profile, "user_id", None)
    if user_id is None:
        return expiry
    return (
        coverage_until(Owner(pilot_user_id=user_id), DocType.MEDICAL, as_of) or expiry
    )
