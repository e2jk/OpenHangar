"""Canonical string serialization for offline-sync conflict detection.

Conflict detection (see ``offline/routes.py``) compares strings, so every
editable field must have exactly one canonical string form. These functions
are the single authority used by the snapshot API, the sync API's conflict
scan, and the sync API's response — never re-derive these formats elsewhere.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import time as _time

    from models import FlightCrew, FlightEntry, PilotLogbookEntry


# The editable field set for FlightEntry offline sync (app/offline routes,
# workbench, outbox). Order matches the canonical serialization table in
# docs/phase38_offline_logbook_spec.md §38a.
FLIGHT_EDITABLE_FIELDS: tuple[str, ...] = (
    "date",
    "departure_icao",
    "arrival_icao",
    "departure_time",
    "arrival_time",
    "flight_time",
    "flight_time_counter_start",
    "flight_time_counter_end",
    "engine_time_counter_start",
    "engine_time_counter_end",
    "fuel_added_qty",
    "fuel_remaining_qty",
    "oil_added_l",
    "passenger_count",
    "landing_count",
    "nature_of_flight",
    "notes",
    "fuel_added_unit",
    "fuel_event",
    "crew_name_0",
    "crew_role_0",
    "crew_name_1",
    "crew_role_1",
)


def _fmt_decimal(value: Decimal | float | None, decimals: int) -> str:
    if value is None:
        return ""
    return f"{float(value):.{decimals}f}"


def _fmt_int(value: int | None) -> str:
    return "" if value is None else str(int(value))


def _fmt_time(value: "_time | None") -> str:
    return "" if value is None else value.strftime("%H:%M")


def _fmt_str(value: str | None) -> str:
    return "" if value is None else value.strip()



# The editable field set for standalone PilotLogbookEntry offline sync
# (flight_id IS NULL rows) — everything _entry_from_form parses. Order
# matches docs/phase38_offline_logbook_spec.md §38h. `cross_country` is a
# model column with no form field anywhere and is intentionally excluded.
PILOT_EDITABLE_FIELDS: tuple[str, ...] = (
    "date",
    "aircraft_type",
    "aircraft_type_icao",
    "aircraft_registration",
    "departure_place",
    "departure_time",
    "arrival_place",
    "arrival_time",
    "pic_name",
    "night_time",
    "instrument_time",
    "landings_day",
    "landings_night",
    "single_pilot_se",
    "single_pilot_me",
    "multi_pilot",
    "function_pic",
    "function_copilot",
    "function_dual",
    "function_instructor",
    "remarks",
    "entry_type",
    "fstd_type",
    "fstd_duration",
)

# For a *linked* entry (flight_id set), only this user-entered subset is
# independently editable offline — everything else is derived from the
# flight and recomputed server-side (see apply_linked_pilot_entry).
PILOT_LINKED_EDITABLE_FIELDS: tuple[str, ...] = (
    "night_time",
    "instrument_time",
    "landings_day",
    "landings_night",
    "multi_pilot",
    "pic_name",
    "departure_time",
    "arrival_time",
)


def canonical_pilot_entry(pe: "PilotLogbookEntry") -> dict[str, str]:
    """Canonical (string, per-field) serialization of the editable PilotLogbookEntry fields."""
    return {
        "date": pe.date.isoformat() if pe.date else "",
        "aircraft_type": _fmt_str(pe.aircraft_type),
        "aircraft_type_icao": _fmt_str(pe.aircraft_type_icao),
        "aircraft_registration": _fmt_str(pe.aircraft_registration),
        "departure_place": _fmt_str(pe.departure_place),
        "departure_time": _fmt_time(pe.departure_time),
        "arrival_place": _fmt_str(pe.arrival_place),
        "arrival_time": _fmt_time(pe.arrival_time),
        "pic_name": _fmt_str(pe.pic_name),
        "night_time": _fmt_decimal(pe.night_time, 1),
        "instrument_time": _fmt_decimal(pe.instrument_time, 1),
        "landings_day": _fmt_int(pe.landings_day),
        "landings_night": _fmt_int(pe.landings_night),
        "single_pilot_se": _fmt_decimal(pe.single_pilot_se, 1),
        "single_pilot_me": _fmt_decimal(pe.single_pilot_me, 1),
        "multi_pilot": _fmt_decimal(pe.multi_pilot, 1),
        "function_pic": _fmt_decimal(pe.function_pic, 1),
        "function_copilot": _fmt_decimal(pe.function_copilot, 1),
        "function_dual": _fmt_decimal(pe.function_dual, 1),
        "function_instructor": _fmt_decimal(pe.function_instructor, 1),
        "remarks": _fmt_str(pe.remarks),
        "entry_type": _fmt_str(pe.entry_type),
        "fstd_type": _fmt_str(pe.fstd_type),
        "fstd_duration": _fmt_decimal(pe.fstd_duration, 1),
    }


def canonical_linked_pilot_fields(
    pe: "PilotLogbookEntry", fe: "FlightEntry"
) -> dict[str, str]:
    """User-entered subset of a *linked* PilotLogbookEntry (snapshot `fields`,
    outbox base, sync request/response shape).

    `departure_time`/`arrival_time` canonicalize to `""` whenever they mirror
    the flight's corresponding time (matching `flight_form.html`'s
    pre-fill-when-different behaviour) and to `"HH:MM"` only for a genuine
    override — `""` in a payload means "mirror the (possibly updated)
    flight time".
    """
    return {
        "night_time": _fmt_decimal(pe.night_time, 1),
        "instrument_time": _fmt_decimal(pe.instrument_time, 1),
        "landings_day": _fmt_int(pe.landings_day),
        "landings_night": _fmt_int(pe.landings_night),
        "multi_pilot": _fmt_decimal(pe.multi_pilot, 1),
        "pic_name": _fmt_str(pe.pic_name),
        "departure_time": ""
        if pe.departure_time == fe.departure_time
        else _fmt_time(pe.departure_time),
        "arrival_time": ""
        if pe.arrival_time == fe.arrival_time
        else _fmt_time(pe.arrival_time),
    }


def canonical_linked_pilot_derived(pe: "PilotLogbookEntry") -> dict[str, str]:
    """Remaining canonical pilot fields for a linked entry — display-only,
    never conflict-scanned, rejected on write (see `PILOT_LINKED_EDITABLE_FIELDS`).
    """
    full = canonical_pilot_entry(pe)
    return {k: v for k, v in full.items() if k not in PILOT_LINKED_EDITABLE_FIELDS}


def canonical_entry(fe: "FlightEntry", crew: "list[FlightCrew]") -> dict[str, str]:
    """Canonical (string, per-field) serialization of the editable FlightEntry fields."""
    ordered = sorted(crew, key=lambda c: c.sort_order)
    crew0 = ordered[0] if len(ordered) > 0 else None
    crew1 = ordered[1] if len(ordered) > 1 else None
    return {
        "date": fe.date.isoformat() if fe.date else "",
        "departure_icao": (fe.departure_icao or "").strip().upper(),
        "arrival_icao": (fe.arrival_icao or "").strip().upper(),
        "departure_time": _fmt_time(fe.departure_time),
        "arrival_time": _fmt_time(fe.arrival_time),
        "flight_time": _fmt_decimal(fe.flight_time, 1),
        "flight_time_counter_start": _fmt_decimal(fe.flight_time_counter_start, 1),
        "flight_time_counter_end": _fmt_decimal(fe.flight_time_counter_end, 1),
        "engine_time_counter_start": _fmt_decimal(fe.engine_time_counter_start, 1),
        "engine_time_counter_end": _fmt_decimal(fe.engine_time_counter_end, 1),
        "fuel_added_qty": _fmt_decimal(fe.fuel_added_qty, 2),
        "fuel_remaining_qty": _fmt_decimal(fe.fuel_remaining_qty, 2),
        "oil_added_l": _fmt_decimal(fe.oil_added_l, 2),
        "passenger_count": _fmt_int(fe.passenger_count),
        "landing_count": _fmt_int(fe.landing_count),
        "nature_of_flight": _fmt_str(fe.nature_of_flight),
        "notes": _fmt_str(fe.notes),
        "fuel_added_unit": _fmt_str(fe.fuel_added_unit),
        "fuel_event": _fmt_str(fe.fuel_event),
        "crew_name_0": crew0.name if crew0 else "",
        "crew_role_0": crew0.role if crew0 else "",
        "crew_name_1": crew1.name if crew1 else "",
        "crew_role_1": crew1.role if crew1 else "",
    }
