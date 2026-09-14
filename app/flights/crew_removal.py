"""Deleting a flight that is in more than one pilot's logbook.

A Flight row is shared by both crew slots, so a plain delete would remove the
flight from *every* linked pilot's logbook — letting one pilot (or an aircraft
owner) wipe someone else's hours. Every delete path goes through these
helpers instead, following one rule: you can only ever remove yourself.

- Pilot side (logbook delete, pilot logbook import rollback):
  ``remove_flight_for_user`` unlinks only the acting pilot's slot when another
  account is still linked, and deletes the row only when nobody else is.
- Aircraft side (aircraft log delete, airframe/GPS import rollback, aircraft
  deletion): ``remove_from_aircraft_log`` / ``detach_from_aircraft`` take the
  flight off the aircraft's log but keep it in the linked pilots' logbooks as
  an "other aircraft" flight, with the registration and type preserved.
"""

from flask import flash  # pyright: ignore[reportMissingImports]
from flask_babel import ngettext  # pyright: ignore[reportMissingImports]
from models import Flight, db  # pyright: ignore[reportMissingImports]


def unlink_user(fe: Flight, user_id: int | None) -> None:
    """Clear *user_id*'s crew slot identity, that slot's own function hours
    and personal remark. The free-text name stays; the flight's shared
    figures are left alone since the other pilot still depends on them."""
    if user_id is None:
        return
    if fe.pic_user_id == user_id:
        fe.pic_user_id = None
        fe.function_pic = None
        fe.pic_remarks = None
    if fe.second_crew_user_id == user_id:
        fe.second_crew_user_id = None
        fe.function_dual = None
        fe.function_copilot = None
        fe.function_instructor = None
        fe.second_crew_remarks = None


def remove_flight_for_user(fe: Flight, user_id: int) -> bool:
    """Remove *fe* from *user_id*'s logbook. Returns True when the row was
    deleted, False when it was kept for another linked pilot."""
    if fe.other_linked_user_ids(user_id):
        unlink_user(fe, user_id)
        return False
    db.session.delete(fe)
    return True


def detach_from_aircraft(fe: Flight) -> None:
    """Turn a managed-aircraft flight into a standalone ("other aircraft")
    flight, so it drops out of that aircraft's log, hours and maintenance
    tracking while staying in the crew's pilot logbooks."""
    if fe.aircraft_id is None:
        return
    fe.other_aircraft_registration = fe.display_registration
    fe.other_aircraft_type = fe.display_aircraft_type
    fe.other_aircraft_type_icao = fe.display_aircraft_type_icao
    fe.aircraft_id = None
    fe.reservation_id = None
    fe.gps_import_batch_id = None
    fe.airframe_import_batch_id = None


def remove_from_aircraft_log(fe: Flight, actor_id: int | None) -> bool:
    """Aircraft-side delete by *actor_id*. Returns True when the row was
    deleted, False when it was detached and kept for linked pilots (the
    actor's own link, if any, is removed)."""
    if fe.other_linked_user_ids(actor_id):
        unlink_user(fe, actor_id)
        detach_from_aircraft(fe)
        return False
    db.session.delete(fe)
    return True


def flash_kept_in_pilot_logbooks(kept: int) -> None:
    """Tell an aircraft-side bulk delete how many flights were detached and
    kept for pilots instead of deleted."""
    if kept:
        flash(
            ngettext(
                "One flight stays in a pilot's logbook as a flight on another "
                "aircraft; it was only removed from this aircraft's log.",
                "%(num)d flights stay in pilots' logbooks as flights on another "
                "aircraft; they were only removed from this aircraft's log.",
                kept,
            ),
            "info",
        )
