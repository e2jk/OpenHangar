"""Live, batched own-logbook evidence, shared by previews and passport views.

The pilot logbook is personal across organisations (the same identity-slot policy
as pilots._my_entries_query); passport/alias/manual-visit scope remains tenant-bound.
No automatic stamps are persisted. A logged arrival is presumed a full stop;
participants can exclude it without changing their logbook. Unknown durations
on historical flights remain eligible. Date-only visits sort after timed arrivals
on the same day; no time is invented for them.
"""

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from models import (
    Flight,
    LogbookEntryType,
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PassportArrivalExclusion,
    PassportParticipant,
    PassportVisit,
    db,
)
from sqlalchemy.orm import selectinload

from passports.catalogue import effective_airfield, public_airfield


def date_eligible(
    passport: Passport, visit_date: date, *, today: date | None = None
) -> bool:
    return _in_range(visit_date, passport.start_date, passport.end_date, today)


def _in_range(
    day: date, start: date | None, end: date | None, today: date | None = None
) -> bool:
    return (
        day <= (today or date.today())
        and (start is None or day >= start)
        and (end is None or day <= end)
    )


def eligible_flight(flight: Flight) -> bool:
    """Use the same two identity slots as the pilot logbook, never aircraft access."""
    return flight.entry_type == LogbookEntryType.FLIGHT and not (
        flight.flight_time is not None
        and flight.flight_time <= 0
        and not (flight.landing_count or flight.landings_day or flight.landings_night)
    )


def participant_for(passport_id: int, user_id: int) -> PassportParticipant | None:
    participant: PassportParticipant | None = (
        PassportParticipant.query.filter_by(
            passport_id=passport_id, user_id=user_id, is_active=True, blocked=False
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .first()
    )

    return participant


def _target_rows(passport_ids: list[int]) -> Any:
    return (
        db.session.query(PassportAirfield, PassportAirfieldIdentity)
        .join(
            PassportAirfieldIdentity,
            PassportAirfield.airfield_id == PassportAirfieldIdentity.id,
        )
        .filter(PassportAirfield.passport_id.in_(passport_ids))
        .order_by(PassportAirfield.display_order, PassportAirfield.id)
        .all()
    )


def _field(
    target: PassportAirfield, identity: PassportAirfieldIdentity
) -> dict[str, Any]:
    return {
        **{
            key: value
            for key, value in (
                public_airfield(identity)
                if target.public_catalogue
                else effective_airfield(identity)
            ).items()
            if key != "classification_provenance"
        },
        **(target.classification_overrides or {}),
        "public_catalogue": bool(target.public_catalogue),
        "id": identity.id,
        "key": str(identity.id),
        "target_id": target.id,
    }


def target_fields(passport: Passport) -> list[dict[str, Any]]:
    return [
        _field(target, identity) for target, identity in _target_rows([passport.id])
    ]


def _aliases(tenant_id: int) -> list[PassportAirfieldAlias]:
    return (
        db.session.query(PassportAirfieldAlias)
        .join(PassportAirfieldIdentity)
        .filter(PassportAirfieldIdentity.tenant_id == tenant_id)
        .all()
    )


def _flights(user_id: int) -> list[Flight]:
    flights: list[Flight] = (
        Flight.query.options(selectinload(Flight.aircraft))  # type: ignore[arg-type]
        .filter(
            db.or_(
                Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
            ),
            Flight.date <= date.today(),
        )
        .all()
    )

    return flights


@dataclass
class EvidenceSnapshot:
    """Request-local, explicit viewer scope; never stored or shared between requests."""

    tenant_id: int
    user_id: int
    aliases: list[PassportAirfieldAlias]
    flights: list[Flight]
    exclusions: dict[int, set[int]]
    visits: dict[int, list[tuple[PassportVisit, int]]]


def progress_many(
    passports: list[Passport], user_id: int, *, include_evidence: bool = True
) -> dict[int, dict[str, Any]]:
    """Batch evidence across editions, including empty/nonparticipating passports.

    Only the supplied passports are evaluated; a series relationship is never
    permission to read a linked passport or to give a nonparticipant a stamp.
    Callers supply their access-checked collection. Read-only group history can
    suppress all evidence explicitly, even if legacy participation rows exist.
    """
    if not passports:
        return {}
    tenant_id = passports[0].tenant_id
    if any(passport.tenant_id != tenant_id for passport in passports):
        raise ValueError("A passport snapshot must belong to one tenant")
    ids = [passport.id for passport in passports]
    participants = {
        participant.passport_id: participant
        for participant in (
            PassportParticipant.query.filter(
                PassportParticipant.passport_id.in_(ids),
                PassportParticipant.user_id == user_id,
                PassportParticipant.is_active.is_(True),
                PassportParticipant.blocked.is_(False),
                PassportParticipant.accepted_at.is_not(None),
            ).all()
            if include_evidence
            else []
        )
    }
    participant_ids = [participant.id for participant in participants.values()]
    snapshot = EvidenceSnapshot(
        tenant_id,
        user_id,
        _aliases(tenant_id),
        _flights(user_id) if participant_ids else [],
        {},
        {},
    )
    for exclusion in PassportArrivalExclusion.query.filter(
        PassportArrivalExclusion.participant_id.in_(participant_ids)
    ).all():
        snapshot.exclusions.setdefault(exclusion.participant_id, set()).add(
            exclusion.flight_id
        )
    for visit, airfield_id in (
        db.session.query(PassportVisit, PassportAirfield.airfield_id)
        .join(PassportAirfield, PassportVisit.target_id == PassportAirfield.id)
        .filter(PassportVisit.participant_id.in_(participant_ids))
        .all()
    ):
        snapshot.visits.setdefault(visit.participant_id, []).append(
            (visit, airfield_id)
        )
    fields: dict[int, list[dict[str, Any]]] = {pid: [] for pid in ids}
    for target, identity in _target_rows(ids):
        fields[target.passport_id].append(_field(target, identity))
    return {
        passport.id: evaluate(
            tenant_id,
            user_id,
            fields[passport.id],
            passport.start_date,
            passport.end_date,
            participants.get(passport.id),
            snapshot=snapshot,
        )
        for passport in passports
    }


def evaluate(
    tenant_id: int,
    user_id: int,
    fields: list[dict[str, Any]],
    start: date | None,
    end: date | None,
    participant: PassportParticipant | None,
    *,
    preview: bool = False,
    snapshot: EvidenceSnapshot | None = None,
) -> dict[str, Any]:
    """A fixed query count regardless of targets/flights; only the viewer's evidence.

    Alias ambiguity is checked against *all* tenant identities, including fields
    outside this passport. Otherwise selecting only one reused code could award
    credit to the wrong airfield. Projected, not-yet-materialised builder fields
    are included in this same resolver.
    """
    fields_by_id = {f["id"]: f for f in fields if f.get("id")}
    codes: dict[str, list[tuple[str, date | None, date | None]]] = {}
    if snapshot is not None and (snapshot.tenant_id, snapshot.user_id) != (
        tenant_id,
        user_id,
    ):
        raise ValueError("Passport evidence snapshot scope mismatch")
    aliases = snapshot.aliases if snapshot is not None else _aliases(tenant_id)
    public_sources = {
        field.get("source_id"): field["key"]
        for field in fields
        if field.get("public_catalogue") and field.get("source_id")
    }
    for alias in aliases:
        target = fields_by_id.get(alias.airfield_id)
        if (
            target
            and target.get("public_catalogue")
            and alias.catalogue_source_id is None
        ):
            continue
        key = (
            target["key"]
            if target
            else public_sources.get(
                alias.catalogue_source_id, "outside:" + str(alias.airfield_id)
            )
        )
        codes.setdefault(alias.code, []).append(
            (key, alias.valid_from, alias.valid_until)
        )
    for field in fields:
        if not field.get("id"):
            for code in {field.get("ident"), field.get("icao_code")} - {
                None,
                "",
                "ZZZZ",
            }:
                entries = codes.setdefault(str(code).upper(), [])
                if not any(key == field["key"] for key, _first, _last in entries):
                    entries.append((field["key"], None, None))
    evidence: dict[str, list[dict[str, Any]]] = {f["key"]: [] for f in fields}
    excluded: set[int] = set()
    if participant:
        excluded = (
            snapshot.exclusions.get(participant.id, set())
            if snapshot is not None
            else {
                e.flight_id
                for e in PassportArrivalExclusion.query.filter_by(
                    participant_id=participant.id
                ).all()
            }
        )
    flights: list[Flight] = []
    if participant is not None or preview:
        flights = snapshot.flights if snapshot is not None else _flights(user_id)
        for flight in flights:
            if not eligible_flight(flight) or not _in_range(flight.date, start, end):
                continue
            code = (flight.arrival_icao or "").strip().upper()
            if code == "ZZZZ":
                continue
            matches = {
                key
                for key, first, last in codes.get(code, [])
                if (first is None or flight.date >= first)
                and (last is None or flight.date <= last)
            }
            if len(matches) != 1:
                continue
            key = next(iter(matches))
            if key not in evidence:
                continue
            evidence[key].append(
                {
                    "kind": "arrival",
                    "id": flight.id,
                    "flight_id": flight.id,
                    "can_open": True,
                    "date": flight.date,
                    "time": flight.arrival_time,
                    "aircraft": flight.aircraft.registration
                    if flight.aircraft
                    else flight.other_aircraft_registration,
                    "note": None,
                    "excluded": flight.id in excluded,
                }
            )
    own_flight_ids = {flight.id for flight in flights}
    if participant:
        visits = (
            snapshot.visits.get(participant.id, [])
            if snapshot is not None
            else (
                db.session.query(PassportVisit, PassportAirfield.airfield_id)
                .join(PassportAirfield, PassportVisit.target_id == PassportAirfield.id)
                .filter(PassportVisit.participant_id == participant.id)
                .all()
            )
        )
        for visit, airfield_id in visits:
            if airfield_id not in fields_by_id:
                continue
            field = fields_by_id[airfield_id]
            evidence[field["key"]].append(
                {
                    "kind": visit.evidence_kind,
                    "id": visit.id,
                    "flight_id": visit.flight_id,
                    "can_open": visit.flight_id in own_flight_ids,
                    "date": visit.visit_date,
                    "time": None,
                    "aircraft": None,
                    "note": visit.note,
                    "excluded": not _in_range(visit.visit_date, start, end),
                }
            )
    rows = []
    for field in fields:
        events = sorted(
            evidence[field["key"]],
            key=lambda e: (
                e["date"],
                e["time"] is None,
                e["time"] or time.min,
                e["kind"],
                e["id"],
            ),
        )
        qualifying = [event for event in events if not event["excluded"]]
        # A linked manual/GPS confirmation of an arrival is one event. Independent
        # date-only visits have their own IDs and remain separately countable.
        seen: set[tuple[str, int]] = set()
        unique = []
        for event in qualifying:
            identity = (
                ("flight", event["flight_id"])
                if event["flight_id"] is not None
                else ("visit", event["id"])
            )
            if identity not in seen:
                seen.add(identity)
                unique.append(event)
        rows.append(
            {
                **field,
                "events": events,
                "first": qualifying[0] if qualifying else None,
                "count": len(unique),
                "visited": bool(unique),
            }
        )
    visited = sum(row["visited"] for row in rows)
    excluded_closed = sum(
        bool(row.get("is_closed")) and not row["visited"] for row in rows
    )
    total = len(rows) - excluded_closed
    return {
        "rows": rows,
        "supporting_flights": sorted(
            (
                flight
                for flight in flights
                if eligible_flight(flight) and _in_range(flight.date, start, end)
            ),
            key=lambda flight: (flight.date, flight.id),
            reverse=True,
        )[:200],
        "visited": visited,
        "total": total,
        "original": len(rows),
        "excluded_closed": excluded_closed,
        "percent": round(100 * visited / total) if total else None,
    }


def passport_progress(passport: Passport, user_id: int) -> dict[str, Any]:
    return evaluate(
        passport.tenant_id,
        user_id,
        target_fields(passport),
        passport.start_date,
        passport.end_date,
        participant_for(passport.id, user_id),
    )
