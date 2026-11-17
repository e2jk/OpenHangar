"""Live, batched own-logbook evidence, shared by previews and passport views.

The pilot logbook is personal across organisations (the same identity-slot policy
as pilots._my_entries_query); passport/alias/manual-visit scope remains tenant-bound.
No automatic stamps are persisted. A logged arrival is presumed a full stop;
participants can exclude it without changing their logbook. Unknown durations
on historical flights remain eligible. Date-only visits sort after timed arrivals
on the same day; no time is invented for them.
"""

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

from passports.catalogue import effective_airfield


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
            passport_id=passport_id, user_id=user_id, is_active=True
        )
        .filter(PassportParticipant.accepted_at.is_not(None))
        .first()
    )

    return participant


def target_fields(passport: Passport) -> list[dict[str, Any]]:
    rows = (
        db.session.query(PassportAirfield, PassportAirfieldIdentity)
        .join(
            PassportAirfieldIdentity,
            PassportAirfield.airfield_id == PassportAirfieldIdentity.id,
        )
        .filter(PassportAirfield.passport_id == passport.id)
        .order_by(PassportAirfield.display_order, PassportAirfield.id)
        .all()
    )
    return [
        {
            **effective_airfield(field),
            "id": field.id,
            "key": str(field.id),
            "target_id": target.id,
        }
        for target, field in rows
    ]


def evaluate(
    tenant_id: int,
    user_id: int,
    fields: list[dict[str, Any]],
    start: date | None,
    end: date | None,
    participant: PassportParticipant | None,
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """A fixed query count regardless of targets/flights; only the viewer's evidence.

    Alias ambiguity is checked against *all* tenant identities, including fields
    outside this passport. Otherwise selecting only one reused code could award
    credit to the wrong airfield. Projected, not-yet-materialised builder fields
    are included in this same resolver.
    """
    fields_by_id = {f["id"]: f for f in fields if f.get("id")}
    codes: dict[str, list[tuple[str, date | None, date | None]]] = {}
    aliases = (
        db.session.query(PassportAirfieldAlias)
        .join(PassportAirfieldIdentity)
        .filter(PassportAirfieldIdentity.tenant_id == tenant_id)
        .all()
    )
    for alias in aliases:
        key = (
            fields_by_id[alias.airfield_id]["key"]
            if alias.airfield_id in fields_by_id
            else "outside:" + str(alias.airfield_id)
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
                codes.setdefault(str(code).upper(), []).append(
                    (field["key"], None, None)
                )
    evidence: dict[str, list[dict[str, Any]]] = {f["key"]: [] for f in fields}
    excluded = (
        {
            e.flight_id
            for e in PassportArrivalExclusion.query.filter_by(
                participant_id=participant.id
            ).all()
        }
        if participant
        else set()
    )
    flights: list[Flight] = []
    if participant is not None or preview:
        flights = (
            Flight.query.options(
                selectinload(Flight.aircraft)  # type: ignore[arg-type]
            )
            .filter(
                db.or_(
                    Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
                ),
                Flight.date <= date.today(),
            )
            .all()
        )
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
            db.session.query(PassportVisit, PassportAirfield.airfield_id)
            .join(PassportAirfield, PassportVisit.target_id == PassportAirfield.id)
            .filter(PassportVisit.participant_id == participant.id)
            .all()
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
