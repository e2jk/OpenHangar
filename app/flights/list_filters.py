"""Filters of the logbook lists (an aircraft's flight log, a pilot's
logbook): GPS track (with / without), a date range, and the crew — to find,
say, the flights since an aircraft was bought that still have no GPS track.

They live in the list's URL (?gps=without&from=2025-02-01&crew=me), so they
survive paging and are kept in the remembered list view like the page.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from models import Flight, LogbookEntryType  # pyright: ignore[reportMissingImports]
from sqlalchemy import or_  # pyright: ignore[reportMissingImports]

GPS_CHOICES = ("with", "without")
# Crew choices: on an aircraft's log, the user's own flights; in a pilot's
# logbook, the slot they flew in.
AIRCRAFT_CREW_CHOICES = ("me",)
PILOT_CREW_CHOICES = ("pic", "second")


def _date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def parse_filters(args: Any, crew_choices: tuple[str, ...]) -> dict[str, str]:
    """The valid filters among the request's *args*, as URL parameters
    (empty ones left out) — what the list's links carry along."""
    found: dict[str, str] = {}
    if args.get("gps") in GPS_CHOICES:
        found["gps"] = args["gps"]
    for key in ("from", "to"):
        day = _date(args.get(key))
        if day:
            found[key] = day.isoformat()
    if args.get("crew") in crew_choices:
        found["crew"] = args["crew"]
    return found


def apply_filters(query: Any, filters: dict[str, str], user_id: int) -> Any:
    """*query* (of Flight) narrowed to *filters*."""
    if filters.get("gps") == "with":
        query = query.filter(Flight.gps_track_id.isnot(None))
    elif filters.get("gps") == "without":
        # A simulator session never has a track: not "missing" one.
        query = query.filter(
            Flight.gps_track_id.is_(None),
            Flight.entry_type != LogbookEntryType.FSTD,
        )
    if "from" in filters:
        query = query.filter(Flight.date >= date.fromisoformat(filters["from"]))
    if "to" in filters:
        query = query.filter(Flight.date <= date.fromisoformat(filters["to"]))
    crew = filters.get("crew")
    if crew == "me":
        query = query.filter(
            or_(Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id)
        )
    elif crew == "pic":
        query = query.filter(Flight.pic_user_id == user_id)
    elif crew == "second":
        query = query.filter(Flight.second_crew_user_id == user_id)
    return query
