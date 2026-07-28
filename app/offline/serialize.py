"""Canonical string serialization for offline-sync conflict detection.

Conflict detection (see ``offline/routes.py``) compares strings, so every
editable field must have exactly one canonical string form. These functions
are the single authority used by the snapshot API, the sync API's conflict
scan, and the sync API's response — never re-derive these formats elsewhere.

Unified-model note: a Flight row now carries both the airframe-log fields
and the EASA pilot-log figures together — there's no more separate "pilot"
sub-object nested inside the aircraft snapshot (the old
PILOT_LINKED_EDITABLE_FIELDS / canonical_linked_pilot_fields /
canonical_linked_pilot_derived split), since they're just more columns on
the same row now. FLIGHT_EDITABLE_FIELDS below covers a managed-aircraft
row end to end; PILOT_EDITABLE_FIELDS covers a standalone row (aircraft_id
NULL) end to end — both driven by the same Flight model, just a different
field subset per "which side" a workbench edits.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import time as _time

    from models import Flight


# The editable field set for a managed-aircraft Flight row's offline sync
# (app/offline routes, aircraft workbench, outbox). crew_name_0/crew_name_1/
# crew_role_1 are wire names for pic_name/second_crew_name/second_crew_role
# (there's no crew_role_0 any more — the PIC slot's role is implicit).
FLIGHT_EDITABLE_FIELDS: tuple[str, ...] = (
    "date",
    "departure_icao",
    "arrival_icao",
    "departure_time",
    "arrival_time",
    "takeoff_time",
    "landing_time",
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
    "crew_name_1",
    "crew_role_1",
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


# The editable field set for a standalone Flight row's offline sync
# (aircraft_id IS NULL — rental/FSTD/other-club flights) — everything
# parse_pilot_fields/apply_pilot_fields handles. Wire names match the
# pre-refactor PilotLogbookEntry columns (aircraft_type, departure_place,
# remarks, …) even though the underlying Flight columns are named
# differently (other_aircraft_type, departure_icao, notes, …) — see
# pilots/form_parsing.py, which already does this same wire<->storage
# mapping for the online standalone entry form. `cross_country` is a model
# column with no form field anywhere and is intentionally excluded.
PILOT_EDITABLE_FIELDS: tuple[str, ...] = (
    "date",
    "aircraft_type",
    "aircraft_type_icao",
    "aircraft_registration",
    "departure_place",
    "departure_time",
    "arrival_place",
    "arrival_time",
    "takeoff_time",
    "landing_time",
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


def canonical_pilot_entry(pe: "Flight") -> dict[str, str]:
    """Canonical (string, per-field) serialization of a standalone Flight
    row's editable fields (aircraft_id IS NULL)."""
    return {
        "date": pe.date.isoformat() if pe.date else "",
        "aircraft_type": _fmt_str(pe.other_aircraft_type),
        "aircraft_type_icao": _fmt_str(pe.other_aircraft_type_icao),
        "aircraft_registration": _fmt_str(pe.other_aircraft_registration),
        "departure_place": _fmt_str(pe.departure_icao),
        "departure_time": _fmt_time(pe.departure_time),
        "arrival_place": _fmt_str(pe.arrival_icao),
        "arrival_time": _fmt_time(pe.arrival_time),
        "takeoff_time": _fmt_time(pe.takeoff_time),
        "landing_time": _fmt_time(pe.landing_time),
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
        "remarks": _fmt_str(pe.notes),
        "entry_type": _fmt_str(pe.entry_type),
        "fstd_type": _fmt_str(pe.fstd_type),
        "fstd_duration": _fmt_decimal(pe.fstd_duration, 1),
    }


def canonical_entry(fe: "Flight") -> dict[str, str]:
    """Canonical (string, per-field) serialization of a managed-aircraft
    Flight row's editable fields."""
    return {
        "date": fe.date.isoformat() if fe.date else "",
        "departure_icao": (fe.departure_icao or "").strip().upper(),
        "arrival_icao": (fe.arrival_icao or "").strip().upper(),
        "departure_time": _fmt_time(fe.departure_time),
        "arrival_time": _fmt_time(fe.arrival_time),
        "takeoff_time": _fmt_time(fe.takeoff_time),
        "landing_time": _fmt_time(fe.landing_time),
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
        "crew_name_0": _fmt_str(fe.pic_name),
        "crew_name_1": _fmt_str(fe.second_crew_name),
        "crew_role_1": _fmt_str(fe.second_crew_role),
        "night_time": _fmt_decimal(fe.night_time, 1),
        "instrument_time": _fmt_decimal(fe.instrument_time, 1),
        "landings_day": _fmt_int(fe.landings_day),
        "landings_night": _fmt_int(fe.landings_night),
        "single_pilot_se": _fmt_decimal(fe.single_pilot_se, 1),
        "single_pilot_me": _fmt_decimal(fe.single_pilot_me, 1),
        "multi_pilot": _fmt_decimal(fe.multi_pilot, 1),
        "function_pic": _fmt_decimal(fe.function_pic, 1),
        "function_copilot": _fmt_decimal(fe.function_copilot, 1),
        "function_dual": _fmt_decimal(fe.function_dual, 1),
        "function_instructor": _fmt_decimal(fe.function_instructor, 1),
    }
