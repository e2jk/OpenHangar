"""Display strings for the read-only offline logbook snapshots.

The offline logbook pages render these from IndexedDB without a connection,
so every value is pre-formatted here as a string: the browser only lays them
out, and never needs the database model or the server's formatting rules.
"""

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import time as _time

    from models import Flight


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


def aircraft_logbook_row(fe: "Flight") -> dict[str, str]:
    """One managed aircraft's flight, as its offline logbook page shows it."""
    return {
        "date": fe.date.isoformat(),
        "departure_icao": _fmt_str(fe.departure_icao).upper(),
        "arrival_icao": _fmt_str(fe.arrival_icao).upper(),
        "departure_time": _fmt_time(fe.departure_time),
        "arrival_time": _fmt_time(fe.arrival_time),
        "flight_time": _fmt_decimal(fe.flight_time, 1),
        "flight_time_counter_start": _fmt_decimal(fe.flight_time_counter_start, 1),
        "flight_time_counter_end": _fmt_decimal(fe.flight_time_counter_end, 1),
        "engine_time_counter_start": _fmt_decimal(fe.engine_time_counter_start, 1),
        "engine_time_counter_end": _fmt_decimal(fe.engine_time_counter_end, 1),
        "landing_count": _fmt_int(fe.landing_count),
        "passenger_count": _fmt_int(fe.passenger_count),
        "pic_name": _fmt_str(fe.pic_name),
        "notes": _fmt_str(fe.notes),
    }


def pilot_logbook_row(fe: "Flight", user_id: int) -> dict[str, str]:
    """One of the pilot's own logbook rows, as their offline logbook shows it.

    Same privacy as the online pilot logbook: on a flight shared with another
    linked pilot, only this pilot's own function hours and remark are shown.
    """
    visible = fe.visible_function_fields(user_id)
    remarks = " · ".join(
        part for part in (fe.notes, fe.personal_remark_for(user_id)) if part
    )
    row = {
        "date": fe.date.isoformat(),
        "entry_type": _fmt_str(fe.entry_type),
        "fstd_type": _fmt_str(fe.fstd_type),
        "aircraft_type": _fmt_str(fe.display_aircraft_type),
        "registration": _fmt_str(fe.display_registration),
        "departure_place": _fmt_str(fe.departure_icao),
        "departure_time": _fmt_time(fe.departure_time),
        "arrival_place": _fmt_str(fe.arrival_icao),
        "arrival_time": _fmt_time(fe.arrival_time),
        "pic_name": _fmt_str(fe.pic_name),
        "landings_day": _fmt_int(fe.landings_day),
        "landings_night": _fmt_int(fe.landings_night),
        "total_time": _fmt_decimal(fe.total_flight_time, 1),
        "fstd_duration": _fmt_decimal(fe.fstd_duration, 1),
        "night_time": _fmt_decimal(fe.night_time, 1),
        "instrument_time": _fmt_decimal(fe.instrument_time, 1),
        "remarks": remarks,
    }
    for field in (
        "function_pic",
        "function_copilot",
        "function_dual",
        "function_instructor",
    ):
        row[field] = _fmt_decimal(getattr(fe, field), 1) if field in visible else ""
    return row
